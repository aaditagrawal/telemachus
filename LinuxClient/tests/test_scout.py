"""Smoke tests for the tkinter scout (no GUI is ever launched).

The module must import cleanly and expose a callable :func:`run_scout` even on
hosts without tkinter or a display. Pure helpers (command building, peer
formatting, scanning, config reads) are covered with mocks; ``run_scout`` is
only exercised through its graceful "no tkinter" path.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client import config, scout  # noqa: E402
from telemachus_client.__main__ import build_arg_parser, resolve_config  # noqa: E402
from telemachus_client.config import build_config, load_config, save_config  # noqa: E402
from telemachus_client.tailscale import Peer  # noqa: E402

TOKEN_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
TOKEN_URI = f"telemachus://192.168.1.50:54321?t={TOKEN_B64}&name=mac"


class ModuleSmokeTest(unittest.TestCase):
    def test_run_scout_is_callable(self):
        self.assertTrue(callable(scout.run_scout))
        self.assertTrue(callable(scout.main))

    def test_scout_flag_is_registered(self):
        args = build_arg_parser().parse_args(["--scout"])
        self.assertTrue(args.scout)

    def test_run_scout_reports_missing_tkinter(self):
        with patch.object(scout, "tk", None):
            self.assertEqual(scout.run_scout(), 1)


class BuildLaunchArgvTest(unittest.TestCase):
    def test_host_and_port(self):
        argv = scout.build_launch_argv(host="100.64.0.7", port=54321)
        self.assertEqual(
            argv[1:], ["-m", "telemachus_client", "--host", "100.64.0.7",
                       "--port", "54321"],
        )

    def test_qr_retains_explicit_host_and_port_overrides(self):
        argv = scout.build_launch_argv(host="100.64.0.7", port=41000, qr=TOKEN_URI)
        args = build_arg_parser().parse_args(argv[3:])
        with patch("telemachus_client.__main__._load_file_config", return_value={}):
            cfg = resolve_config(args)
        self.assertEqual((cfg.host, cfg.port), ("100.64.0.7", 41000))
        self.assertEqual(cfg.token, bytes(range(32)))

    def test_qr_with_host_uses_the_qr_port_unless_explicit(self):
        uri = TOKEN_URI.replace(":54321?", ":41000?")
        argv = scout.build_launch_argv(host="100.64.0.7", qr=uri)
        self.assertNotIn("--port", argv)
        args = build_arg_parser().parse_args(argv[3:])
        with patch("telemachus_client.__main__._load_file_config", return_value={}):
            cfg = resolve_config(args)
        self.assertEqual((cfg.host, cfg.port), ("100.64.0.7", 41000))

    def test_qr_without_overrides(self):
        self.assertEqual(scout.build_launch_argv(qr=TOKEN_URI)[3:],
                         ["--qr", TOKEN_URI])

    def test_host_defaults_port(self):
        argv = scout.build_launch_argv(host="100.64.0.7")
        self.assertIn(str(scout.DEFAULT_PORT), argv)

    def test_missing_host_without_qr_raises(self):
        with self.assertRaises(ValueError):
            scout.build_launch_argv()


class DescribePeerTest(unittest.TestCase):
    def test_includes_name_ip_and_detail(self):
        line = scout.describe_peer(
            Peer("mac-mini", "100.64.0.7", dns="mac-mini.ts.net", os="macOS")
        )
        self.assertIn("mac-mini", line)
        self.assertIn("100.64.0.7", line)
        self.assertIn("macOS", line)

    def test_minimal_peer_has_no_detail_suffix(self):
        self.assertEqual(scout.describe_peer(Peer("box", "100.64.0.9")), "box  100.64.0.9")


class ScanHostsTest(unittest.TestCase):
    def test_missing_tailscale_reports_failure(self):
        with patch("telemachus_client.tailscale.tailscale_available", return_value=False):
            result = scout.scan_hosts()
        self.assertFalse(result.ok)
        self.assertEqual(result.hosts, [])
        self.assertIn("tailscale", result.message.lower())

    def test_no_online_peers_reports_failure(self):
        with patch("telemachus_client.tailscale.tailscale_available", return_value=True), \
                patch("telemachus_client.tailscale.list_peers",
                      return_value=[Peer("sleeping", "100.64.0.8", online=False)]):
            result = scout.scan_hosts()
        self.assertFalse(result.ok)
        self.assertIn("No online", result.message)

    def test_no_open_port_reports_failure(self):
        peer = Peer("mac", "100.64.0.7", online=True)
        with patch("telemachus_client.tailscale.tailscale_available", return_value=True), \
                patch("telemachus_client.tailscale.list_peers", return_value=[peer]), \
                patch("telemachus_client.tailscale.discover_hosts", return_value=[]):
            result = scout.scan_hosts(port=54321)
        self.assertFalse(result.ok)
        self.assertIn("54321", result.message)

    def test_hosts_returned_on_success(self):
        peer = Peer("mac", "100.64.0.7", online=True)
        with patch("telemachus_client.tailscale.tailscale_available", return_value=True), \
                patch("telemachus_client.tailscale.list_peers", return_value=[peer]), \
                patch("telemachus_client.tailscale.discover_hosts", return_value=[peer]):
            result = scout.scan_hosts()
        self.assertTrue(result.ok)
        self.assertEqual(result.hosts, [peer])


class ConfigHelpersTest(unittest.TestCase):
    def test_has_saved_token(self):
        with patch.object(scout, "saved_config", return_value={"token": "abc"}):
            self.assertTrue(scout.has_saved_token())
        with patch.object(scout, "saved_config", return_value={}):
            self.assertFalse(scout.has_saved_token())

    def test_config_port_uses_saved_value(self):
        with patch.object(scout, "saved_config", return_value={"port": 41000}):
            self.assertEqual(scout.config_port(), 41000)

    def test_config_port_falls_back_to_default(self):
        with patch.object(scout, "saved_config", return_value={}):
            self.assertEqual(scout.config_port(), scout.DEFAULT_PORT)
        with patch.object(scout, "saved_config", return_value={"port": 70000}):
            self.assertEqual(scout.config_port(), scout.DEFAULT_PORT)


class SavePairingTest(unittest.TestCase):
    """Pairing from the scout persists the Tailscale peer address, not the URI's."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "client.conf"

    def test_peer_ip_overrides_the_uri_host(self):
        host, port = scout.save_pairing(TOKEN_URI, "100.64.0.7", path=self.path)
        self.assertEqual((host, port), ("100.64.0.7", 54321))
        loaded = load_config(self.path)
        self.assertEqual(loaded["host"], "100.64.0.7")
        self.assertEqual(loaded["port"], 54321)
        self.assertEqual(loaded["token"], TOKEN_B64)
        self.assertNotIn("name", loaded)
        self.assertEqual(len(build_config(None, loaded).token), 32)

    def test_existing_settings_are_left_alone(self):
        save_config({"decoder": "vaapi", "display": "HP", "fullscreen": False},
                    path=self.path)
        scout.save_pairing(TOKEN_URI, "100.64.0.7", path=self.path)
        loaded = load_config(self.path)
        self.assertEqual(loaded["decoder"], "vaapi")
        self.assertEqual(loaded["display"], "HP")
        self.assertFalse(loaded["fullscreen"])

    def test_malformed_uri_raises_value_error(self):
        for bad in ("not-a-uri", "telemachus://192.168.1.50:54321", ""):
            with self.subTest(uri=bad):
                with self.assertRaises(ValueError):
                    scout.save_pairing(bad, "100.64.0.7", path=self.path)

    def test_missing_host_raises_value_error(self):
        with self.assertRaises(ValueError):
            scout.save_pairing(TOKEN_URI, "", path=self.path)

    def test_unwritable_path_raises_value_error(self):
        blocker = Path(self._tmp.name) / "client.conf"
        blocker.write_text("not a directory\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            scout.save_pairing(TOKEN_URI, "100.64.0.7",
                               path=blocker / "nested.conf")


class ScoutConnectTest(unittest.TestCase):
    """Exercise real Scout actions and config writes without creating Tk."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "client.conf"
        self.config_path = patch.object(config, "CONFIG_PATH", self.path)
        self.config_path.start()
        self.addCleanup(self.config_path.stop)
        self.app = scout.ScoutApp.__new__(scout.ScoutApp)
        self.app.root = Mock()
        self.app.status_var = Mock()
        self.app.listbox = Mock()
        self.app.listbox.curselection.return_value = (0,)
        self.app.hosts = [Peer("mac", "100.64.0.7")]
        self.app.port = 41000
        self.app._prompt_uri = Mock(return_value=TOKEN_URI)

    def test_saved_token_connect_persists_peer_and_preserves_preferences(self):
        preferences = {
            "token": TOKEN_B64, "name": "linux", "decoder": "vaapi",
            "display": "HP", "fullscreen": False, "input_enabled": False,
        }
        save_config({"host": "192.168.1.50", "port": 54321, **preferences})

        def check_saved_before_launch(**kwargs):
            self.assertEqual(load_config(), {
                "host": "100.64.0.7", "port": 41000, **preferences,
            })

        with patch.object(scout, "launch_client", side_effect=check_saved_before_launch) as launch:
            self.app.on_connect()
        launch.assert_called_once_with(host="100.64.0.7", port=41000, qr=None)
        self.app._prompt_uri.assert_not_called()
        self.assertEqual(load_config()["host"], "100.64.0.7")

    def test_new_pairing_persists_selected_peer(self):
        save_config({"decoder": "sw", "fullscreen": False})
        with patch.object(scout, "launch_client") as launch:
            self.app.on_connect()
        self.assertEqual(load_config(), {
            "host": "100.64.0.7", "port": 54321, "token": TOKEN_B64,
            "decoder": "sw", "fullscreen": False,
        })
        launch.assert_called_once_with(host="100.64.0.7", port=54321, qr=None)

    def test_pairing_save_failure_keeps_peer_and_hides_token_in_status(self):
        with patch.object(config, "save_config", side_effect=ValueError(TOKEN_URI)), \
                patch.object(scout, "launch_client") as launch:
            self.app.on_connect()
        launch.assert_called_once_with(host="100.64.0.7", port=None, qr=TOKEN_URI)
        self.assertFalse(self.path.exists())
        statuses = [call.args[0] for call in self.app.status_var.set.call_args_list]
        self.assertTrue(statuses)
        self.assertIn("pairing not saved", statuses[-1])
        for status in statuses:
            self.assertNotIn(TOKEN_B64, status)
            self.assertNotIn("telemachus://", status)
        argv = scout.build_launch_argv(**launch.call_args.kwargs)
        with patch("telemachus_client.__main__._load_file_config", return_value={}):
            cfg = resolve_config(build_arg_parser().parse_args(argv[3:]))
        self.assertEqual((cfg.host, cfg.port), ("100.64.0.7", 54321))
        self.assertEqual(cfg.token, bytes(range(32)))

    def test_saved_address_write_failure_preserves_old_pairing_and_connects(self):
        save_config({"host": "old", "token": TOKEN_B64})
        with patch.object(config.os, "replace", side_effect=OSError("disk full")), \
                patch.object(scout, "launch_client") as launch:
            self.app.on_connect()
        self.assertEqual(load_config(), {"host": "old", "token": TOKEN_B64})
        launch.assert_called_once_with(host="100.64.0.7", port=41000, qr=None)
        self.assertIn("address not saved", self.app.status_var.set.call_args.args[0])

    def test_qr_only_launch_status_does_not_show_pairing_uri(self):
        with patch.object(scout, "launch_client"):
            self.app._launch(qr=TOKEN_URI)
        self.assertNotIn(TOKEN_B64, self.app.status_var.set.call_args.args[0])


class SystemdServiceTest(unittest.TestCase):
    def test_unit_has_fixed_execstart_and_session_env(self):
        text = scout.systemd_unit_text()
        self.assertIn("ExecStart=%h/.local/bin/telemachus-linux\n", text)
        self.assertIn("Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n",
                      text)
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t\n", text)
        self.assertIn("Environment=WAYLAND_DISPLAY=wayland-0\n", text)
        self.assertIn("Environment=DISPLAY=:0\n", text)
        self.assertNotIn("SDL_VIDEODRIVER=", text)
        self.assertIn("Restart=on-failure\n", text)
        self.assertIn("PartOf=graphical-session.target\n", text)
        self.assertIn("WantedBy=graphical-session.target\n", text)

    def test_reference_unit_file_matches_the_generator(self):
        reference = scout._PROJECT_DIR / "systemd" / "telemachus-linux.service"
        if not reference.is_file():
            self.skipTest(f"{reference} not present in this checkout")
        self.assertEqual(reference.read_text(encoding="utf-8").strip(),
                         scout.systemd_unit_text().strip())

    def test_install_writes_the_unit_and_only_reloads(self):
        with tempfile.TemporaryDirectory() as unit_dir:
            unit_dir_path = Path(unit_dir) / "systemd" / "user"
            with patch.object(scout.subprocess, "run") as run:
                unit_path = scout.install_user_unit(unit_dir_path)
            self.assertEqual(unit_path, unit_dir_path / scout.SERVICE_NAME)
            self.assertEqual(unit_path.read_text(encoding="utf-8"),
                             scout.systemd_unit_text())
            run.assert_called_once_with(
                ["systemctl", "--user", "daemon-reload"], check=False,
                capture_output=True, text=True, timeout=scout._SYSTEMCTL_TIMEOUT,
            )

    def test_start_invokes_systemctl_start(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch.object(scout.subprocess, "run", return_value=completed) as run:
            proc = scout.start_user_service()
        self.assertEqual(proc.returncode, 0)
        run.assert_called_once_with(
            ["systemctl", "--user", "start", "telemachus-linux.service"],
            check=False, capture_output=True, text=True,
            timeout=scout._SYSTEMCTL_TIMEOUT,
        )

    def test_launcher_path_points_at_the_install_location(self):
        self.assertEqual(scout.launcher_path(), Path.home() / ".local/bin"
                         / "telemachus-linux")


class AuthorizeMacTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("ssh-keygen") is None:
            raise unittest.SkipTest("real-key tests need ssh-keygen")
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.keys = []
        for name in ("mac", "other"):
            key_path = Path(temporary.name) / name
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name,
                 "-f", str(key_path)],
                check=True, capture_output=True, timeout=10,
            )
            cls.keys.append(key_path.with_suffix(".pub").read_text().strip())

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ak_path = Path(temporary.name) / "ssh" / "authorized_keys"
        self.key, self.other_key = self.keys
        self.restricted = f'restrict,command="{scout.SSH_SERVICE_COMMAND}" {self.key}'

    def test_wraps_bare_key_and_creates_private_directory_and_file(self):
        previous_umask = os.umask(0)
        try:
            returned = scout.authorize_mac(self.key, path=self.ak_path)
        finally:
            os.umask(previous_umask)
        self.assertEqual(returned, self.ak_path)
        self.assertEqual(self.ak_path.read_text(), self.restricted + "\n")
        self.assertEqual(stat.S_IMODE(self.ak_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.ak_path.parent.stat().st_mode), 0o700)

    def test_default_path(self):
        with patch.object(scout, "authorized_keys_path", return_value=self.ak_path):
            self.assertEqual(scout.authorize_mac(self.key), self.ak_path)

    def test_full_line_always_has_restrict_and_forced_service_command(self):
        for options in ("restrict", "no-pty,no-port-forwarding",
                        f'command="{scout.SSH_SERVICE_COMMAND}"',
                        f'RESTRICT,COMMAND="{scout.SSH_SERVICE_COMMAND}"'):
            with self.subTest(options=options):
                scout.authorize_mac(f"{options} {self.key}", path=self.ak_path)
                self.assertEqual(self.ak_path.read_text(), self.restricted + "\n")

    def test_accepts_exact_mac_emitted_line_and_normalizes_command(self):
        # LinuxClientLauncher.authorizedKeysLine shell-quotes the unit and
        # places an option separator before it.
        mac_line = (
            'restrict,command="/usr/bin/systemctl --user start -- '
            "'telemachus-linux.service'\" " + self.key
        )
        scout.authorize_mac(mac_line, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), self.restricted + "\n")

    def test_accepts_quoted_unit_without_optional_separator(self):
        line = (
            'command="/usr/bin/systemctl --user start '
            "'telemachus-linux.service'\" " + self.key
        )
        scout.authorize_mac(line, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), self.restricted + "\n")

    def test_normalizes_existing_mac_line_without_duplicate_or_unrelated_changes(self):
        existing = (
            self.other_key + '\nrestrict,command="/usr/bin/systemctl --user start -- '
            "'telemachus-linux.service'\" " + self.key + "\n"
        )
        self.ak_path.parent.mkdir()
        self.ak_path.write_text(existing)
        scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), self.other_key + "\n" + self.restricted + "\n")

    def test_rejects_other_shell_commands_units_arguments_and_operators(self):
        commands = (
            "systemctl --user start -- 'telemachus-linux.service'",
            "/usr/bin/systemctl --user restart -- 'telemachus-linux.service'",
            "/usr/bin/systemctl --system start -- 'telemachus-linux.service'",
            "/usr/bin/systemctl --user start -- 'other.service'",
            "/usr/bin/systemctl --user start -- 'telemachus-linux.service' other.service",
            "/usr/bin/systemctl --user start -- -- 'telemachus-linux.service'",
            scout.SSH_SERVICE_COMMAND + " && /bin/sh",
            scout.SSH_SERVICE_COMMAND + "; /bin/sh",
            scout.SSH_SERVICE_COMMAND + " | /bin/sh",
            scout.SSH_SERVICE_COMMAND + " >/tmp/telemachus-command-output",
            scout.SSH_SERVICE_COMMAND + " $(/bin/true)",
            scout.SSH_SERVICE_COMMAND + " `/bin/true`",
            scout.SSH_SERVICE_COMMAND + " # ignored shell comment",
        )
        for command in commands:
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    scout.authorize_mac(f'restrict,command="{command}" {self.key}',
                                        path=self.ak_path)
                self.assertFalse(self.ak_path.exists())

    def test_rejects_incompatible_command_and_unsafe_options_before_writing(self):
        unsafe = (
            'restrict,command="/bin/true"',
            'command="/bin/sh"',
            "restrict,pty", "restrict,agent-forwarding", "restrict,port-forwarding",
            "restrict,X11-forwarding", "restrict,user-rc", "cert-authority",
            'environment="PATH=/tmp"', 'permitopen="*:22"', "unknown-option",
            f'restrict,command="{scout.SSH_SERVICE_COMMAND}; /bin/sh"',
            f'command="{scout.SSH_SERVICE_COMMAND}",command="/bin/sh"',
            'restrict,command="unterminated',
        )
        for options in unsafe:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    scout.authorize_mac(f"{options} {self.key}", path=self.ak_path)
                self.assertFalse(self.ak_path.exists())

    def test_rejects_empty_invalid_and_multiline_input(self):
        for bad in ("", "   ", "ssh-ed25519 AAAAKEY mac", "not a key",
                    self.key + "\n" + self.other_key, self.key + "\n",
                    self.key + "\r" + self.other_key, self.key + "\x00"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    scout.authorize_mac(bad, path=self.ak_path)
                self.assertFalse(self.ak_path.exists())

    def test_rejects_key_type_that_does_not_match_blob(self):
        with self.assertRaises(ValueError):
            scout.authorize_mac(self.key.replace("ssh-ed25519", "ssh-rsa", 1),
                                path=self.ak_path)
        self.assertFalse(self.ak_path.exists())

    def test_deduplicates_exact_blob_with_different_comment_and_options(self):
        scout.authorize_mac(self.restricted, path=self.ak_path)
        key_with_new_comment = " ".join(self.key.split()[:2]) + " another comment"
        scout.authorize_mac(key_with_new_comment, path=self.ak_path)
        content = self.ak_path.read_text()
        self.assertEqual(len(content.splitlines()), 1)
        self.assertIn("another comment", content)
        self.assertIn(f'restrict,command="{scout.SSH_SERVICE_COMMAND}"', content)

    def test_consolidates_existing_unrestricted_duplicates_into_restricted_key(self):
        self.ak_path.parent.mkdir()
        self.ak_path.write_text(self.key + "\n" + self.restricted + "\n")
        scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), self.restricted + "\n")

    def test_comment_substrings_do_not_hide_new_key_or_change_unrelated_lines(self):
        blob = self.key.split()[1]
        existing = f"# {self.key}\n# keep this key reference: {self.key}\n{self.other_key} ref={blob}"
        self.ak_path.parent.mkdir()
        self.ak_path.write_text(existing)
        scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), existing + "\n" + self.restricted + "\n")

    def test_preserves_unrelated_keys_with_quoted_options(self):
        existing = f'command="echo hello, world",no-pty {self.other_key}\n'
        self.ak_path.parent.mkdir()
        self.ak_path.write_text(existing)
        scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), existing + self.restricted + "\n")

    def test_does_not_remove_existing_source_restriction_or_change_other_command(self):
        for options in ('from="100.64.0.7"', 'command="/bin/true"'):
            with self.subTest(options=options):
                self.ak_path.parent.mkdir(exist_ok=True)
                existing = f"{options} {self.key}\n"
                self.ak_path.write_text(existing)
                with self.assertRaises(ValueError):
                    scout.authorize_mac(self.key, path=self.ak_path)
                self.assertEqual(self.ak_path.read_text(), existing)

    def test_read_failure_keeps_existing_keys(self):
        self.ak_path.parent.mkdir()
        existing = self.other_key + "\n"
        self.ak_path.write_text(existing)
        with patch.object(Path, "read_text", side_effect=PermissionError("cannot read")):
            with self.assertRaises(PermissionError):
                scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), existing)
        self.assertEqual(list(self.ak_path.parent.iterdir()), [self.ak_path])

    def test_undecodable_existing_file_is_preserved(self):
        self.ak_path.parent.mkdir()
        self.ak_path.write_bytes(b"\xff existing access\n")
        with self.assertRaises(UnicodeError):
            scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_bytes(), b"\xff existing access\n")

    def test_failed_atomic_replace_keeps_existing_keys_and_cleans_temp_file(self):
        scout.authorize_mac(self.other_key, path=self.ak_path)
        existing = self.ak_path.read_text()
        with patch.object(scout.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(self.ak_path.read_text(), existing)
        self.assertEqual(list(self.ak_path.parent.iterdir()), [self.ak_path])

    def test_failed_write_creates_no_authorized_keys_or_temp_file(self):
        with patch.object(scout.os, "fsync", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                scout.authorize_mac(self.key, path=self.ak_path)
        self.assertFalse(self.ak_path.exists())
        self.assertEqual(list(self.ak_path.parent.iterdir()), [])

    def test_temp_file_is_private_before_replacement_and_final_mode_is_tightened(self):
        self.ak_path.parent.mkdir()
        self.ak_path.write_text(self.other_key + "\n")
        self.ak_path.chmod(0o644)
        real_replace = os.replace

        def check_private_before_replace(source, destination):
            self.assertEqual(stat.S_IMODE(os.stat(source).st_mode), 0o600)
            self.assertIn(self.restricted, Path(source).read_text())
            real_replace(source, destination)

        with patch.object(scout.os, "replace", side_effect=check_private_before_replace):
            scout.authorize_mac(self.key, path=self.ak_path)
        self.assertEqual(stat.S_IMODE(self.ak_path.stat().st_mode), 0o600)
        self.assertEqual(list(self.ak_path.parent.iterdir()), [self.ak_path])

    def test_rejects_symlink_without_replacing_it_or_its_target(self):
        target = self.ak_path.parent.parent / "original"
        target.write_text(self.other_key + "\n")
        self.ak_path.parent.mkdir()
        self.ak_path.symlink_to(target)
        with self.assertRaises(ValueError):
            scout.authorize_mac(self.key, path=self.ak_path)
        self.assertTrue(self.ak_path.is_symlink())
        self.assertEqual(target.read_text(), self.other_key + "\n")


if __name__ == "__main__":
    unittest.main()
