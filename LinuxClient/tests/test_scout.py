"""Smoke tests for the tkinter scout (no GUI is ever launched).

The module must import cleanly and expose a callable :func:`run_scout` even on
hosts without tkinter or a display. Pure helpers (command building, peer
formatting, scanning, config reads) are covered with mocks; ``run_scout`` is
only exercised through its graceful "no tkinter" path.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client import scout  # noqa: E402
from telemachus_client.__main__ import build_arg_parser  # noqa: E402
from telemachus_client.config import build_config, load_config  # noqa: E402
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

    def test_qr_wins_over_host(self):
        argv = scout.build_launch_argv(host="ignored", port=1, qr="telemachus://h:1?t=x")
        self.assertEqual(argv[-2:], ["--qr", "telemachus://h:1?t=x"])
        self.assertNotIn("--host", argv)

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
        scout.save_pairing(TOKEN_URI, "100.64.0.7", path=self.path)
        loaded = load_config(self.path)
        self.assertNotIn("decoder", loaded)
        self.assertNotIn("display", loaded)

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


class SystemdServiceTest(unittest.TestCase):
    def test_unit_has_fixed_execstart_and_session_env(self):
        text = scout.systemd_unit_text()
        self.assertIn("ExecStart=%h/.local/bin/telemachus-linux\n", text)
        self.assertIn("Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n",
                      text)
        self.assertIn("Environment=XDG_RUNTIME_DIR=%t\n", text)
        self.assertIn("Environment=WAYLAND_DISPLAY=wayland-0\n", text)
        self.assertIn("Environment=DISPLAY=:0\n", text)
        self.assertIn("Restart=on-failure\n", text)
        self.assertIn("PartOf=graphical-session.target\n", text)

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
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ak_path = Path(self._tmp.name) / "ssh" / "authorized_keys"

    def test_wraps_bare_public_key(self):
        key = "ssh-ed25519 AAAAKEY telemachus-mac"
        returned = scout.authorize_mac(key, path=self.ak_path)
        self.assertEqual(returned, self.ak_path)
        content = self.ak_path.read_text(encoding="utf-8")
        self.assertIn(
            'restrict,command="/usr/bin/systemctl --user start telemachus-linux.service" '
            "ssh-ed25519 AAAAKEY telemachus-mac",
            content,
        )
        self.assertEqual(self.ak_path.stat().st_mode & 0o777, 0o600)

    def test_full_authorized_keys_line_used_as_is(self):
        line = 'restrict,command="/bin/true" ssh-ed25519 AAAAKEY mac'
        scout.authorize_mac(line, path=self.ak_path)
        content = self.ak_path.read_text(encoding="utf-8")
        self.assertEqual(content.strip(), line)

    def test_creates_parent_directory(self):
        self.assertFalse(self.ak_path.parent.exists())
        scout.authorize_mac("ssh-ed25519 AAAAKEY telemachus-mac",
                            authorized_keys_path=self.ak_path)
        self.assertTrue(self.ak_path.is_file())

    def test_avoids_duplicate_keys(self):
        key = "ssh-ed25519 AAAAKEY telemachus-mac"
        scout.authorize_mac(key, path=self.ak_path)
        scout.authorize_mac(key, path=self.ak_path)
        lines = [ln for ln in self.ak_path.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)

    def test_avoids_duplicate_full_line(self):
        line = 'restrict,command="/bin/true" ssh-ed25519 AAAAKEY mac'
        scout.authorize_mac(line, path=self.ak_path)
        scout.authorize_mac(line, path=self.ak_path)
        lines = [ln for ln in self.ak_path.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            scout.authorize_mac("", path=self.ak_path)
        with self.assertRaises(ValueError):
            scout.authorize_mac("   ", path=self.ak_path)

    def test_missing_input_raises(self):
        with self.assertRaises(ValueError):
            scout.authorize_mac()

    def test_appends_second_distinct_key(self):
        scout.authorize_mac("ssh-ed25519 AAAAKEY1 mac1", path=self.ak_path)
        scout.authorize_mac("ssh-ed25519 AAAAKEY2 mac2", path=self.ak_path)
        lines = [ln for ln in self.ak_path.read_text().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)

    def test_aliases_exist(self):
        for name in ("authorize_mac", "authorize_mac_key", "authorize_key",
                     "authorize", "add_authorized_key", "add_mac_key",
                     "authorize_mac_public_key"):
            self.assertTrue(hasattr(scout, name))
            self.assertTrue(callable(getattr(scout, name)))

    def test_alias_behavior(self):
        key = "ssh-ed25519 AAAAKEY telemachus-mac"
        scout.authorize_key(key, path=self.ak_path)
        self.assertIn("AAAAKEY", self.ak_path.read_text())

    def test_mocked_file_io(self):
        # Patch Path to a fake that records read/write calls.
        from unittest.mock import MagicMock, patch
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_path.parent.mkdir = MagicMock()
        mock_path.read_text.return_value = ""
        with patch.object(scout, "Path", return_value=mock_path):
            # Path(path) returns mock_path
            scout.authorize_mac("ssh-ed25519 AAAAKEY telemachus-mac",
                                path="/tmp/fake/authorized_keys")
        mock_path.write_text.assert_called_once()
        written = mock_path.write_text.call_args.args[0]
        self.assertIn("AAAAKEY", written)

    def test_duplicate_detection_with_mocked_existing(self):
        from unittest.mock import MagicMock, patch
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "ssh-ed25519 AAAAKEY telemachus-mac\n"
        with patch.object(scout, "Path", return_value=mock_path):
            scout.authorize_mac("ssh-ed25519 AAAAKEY telemachus-mac",
                                path="/tmp/fake/authorized_keys")
        mock_path.write_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
