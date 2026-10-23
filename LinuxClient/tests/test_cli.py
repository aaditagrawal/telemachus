"""Exercise the CLI/config boundary used by interactive and service starts."""

from telemachus_client import __main__ as cli
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
