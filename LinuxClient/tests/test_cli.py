"""Exercise the CLI/config boundary used by interactive and service starts."""

import base64

from telemachus_client import __main__ as cli
from telemachus_client import config
from telemachus_client.config import load_config, save_config
import pytest


def test_saved_preferences_survive_start_without_flags(monkeypatch):
    monkeypatch.setattr(cli, "_load_file_config", lambda: {
        "decoder": "sw", "fullscreen": False, "input_enabled": False,
    })
    cfg = cli.resolve_config(cli.build_arg_parser().parse_args([]))
    assert cfg.decoder == "sw"
    assert cfg.fullscreen is False
    assert cfg.input_enabled is False


def test_explicit_cli_flags_override_saved_preferences(monkeypatch):
    monkeypatch.setattr(cli, "_load_file_config", lambda: {
        "decoder": "vaapi", "fullscreen": False, "input_enabled": True,
    })
    cfg = cli.resolve_config(cli.build_arg_parser().parse_args([
        "--decoder", "sw", "--fullscreen", "--no-input",
    ]))
    assert cfg.decoder == "sw"
    assert cfg.fullscreen is True
    assert cfg.input_enabled is False


def test_windowed_flag_overrides_saved_fullscreen(monkeypatch):
    monkeypatch.setattr(cli, "_load_file_config", lambda: {"fullscreen": True})
    cfg = cli.resolve_config(cli.build_arg_parser().parse_args(["--windowed"]))
    assert cfg.fullscreen is False


def test_default_preferences(monkeypatch):
    monkeypatch.setattr(cli, "_load_file_config", dict)
    cfg = cli.resolve_config(cli.build_arg_parser().parse_args([]))
    assert cfg.decoder == "auto"
    assert cfg.fullscreen is True
    assert cfg.input_enabled is True


def test_sectionless_config_can_be_read_and_updated(tmp_path):
    path = tmp_path / "client.conf"
    path.write_text("host = old-mac\ndecoder = sw\n")
    assert load_config(path)["host"] == "old-mac"
    save_config({"host": "new-mac"}, path)
    assert load_config(path) == {"host": "new-mac", "decoder": "sw"}


def test_config_treats_percent_signs_as_literal_text(tmp_path):
    path = tmp_path / "client.conf"
    save_config({"name": "100% Linux"}, path)
    assert load_config(path)["name"] == "100% Linux"


def test_malformed_config_reports_value_error(tmp_path):
    path = tmp_path / "client.conf"
    path.write_text("[client]\nhost = first\nhost = second\n")
    with pytest.raises(ValueError, match="invalid config"):
        load_config(path)


@pytest.mark.parametrize("uri", [
    "https://mac:54321?t=invalid",
    "telemachus://mac:invalid?t=invalid",
    "telemachus://mac:0?t=invalid",
    "telemachus://mac:54321",
    "telemachus://mac:54321?t=short",
])
def test_invalid_qr_reports_cli_error_without_starting(monkeypatch, uri):
    monkeypatch.setattr(cli, "_load_file_config", dict)
    monkeypatch.setattr(cli, "run_client", lambda *args: pytest.fail("client started"))
    with pytest.raises(SystemExit, match="ERROR: bad config: invalid --qr URI"):
        cli.main(["--qr", uri])


@pytest.mark.parametrize("text", [
    "[client]\nhost = first\nhost = second\n",
    "[client]\nport = invalid\n",
    "[client]\nfullscreen = invalid\n",
])
def test_bad_saved_config_stops_before_starting(monkeypatch, tmp_path, text):
    path = tmp_path / "client.conf"
    path.write_text(text)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(cli, "run_client", lambda *args: pytest.fail("client started"))
    with pytest.raises(SystemExit, match="ERROR: bad config: invalid config"):
        cli.main([])
    assert path.read_text() == text


def test_qr_pairing_replaces_corrupt_config(monkeypatch, tmp_path):
    path = tmp_path / "client.conf"
    path.write_text("[client]\nhost = first\nhost = second\n")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    connected = []

    def run_client(args, cfg):
        connected.append(cfg)
        return 0

    monkeypatch.setattr(cli, "run_client", run_client)
    token_b64 = base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")
    uri = f"telemachus://new-mac:41000?t={token_b64}&name=Mac"
    assert cli.main(["--qr", uri]) == 0
    assert len(connected) == 1
    assert (connected[0].host, connected[0].port, connected[0].token) == (
        "new-mac", 41000, bytes(range(32)),
    )
    saved = load_config(path)
    assert (saved["host"], saved["port"], saved["token"]) == (
        "new-mac", 41000, token_b64,
    )
    assert path.stat().st_mode & 0o777 == 0o600
