"""Unit tests for Tailscale peer parsing, filtering, and TCP probing."""

import argparse
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client.__main__ import (  # noqa: E402
    ResolvedConfig,
    apply_tailscale_discovery,
)
from telemachus_client.tailscale import (  # noqa: E402
    Peer,
    discover_hosts,
    list_peers,
    parse_status,
    tailscale_available,
)

STATUS_JSON = json.dumps(
    {
        "BackendState": "Running",
        "Peer": {
            "nodekey:aa": {
                "HostName": "mac-mini",
                "DNSName": "mac-mini.tailnet.ts.net.",
                "OS": "macOS",
                "Online": True,
                "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"],
                "Tags": ["tag:server", "tag:media"],
            },
            "nodekey:bb": {
                "HostName": "sleeping",
                "DNSName": "sleeping.tailnet.ts.net.",
                "OS": "linux",
                "Online": False,
                "TailscaleIPs": ["100.64.0.8"],
            },
            "nodekey:cc": {
                "HostName": "",
                "DNSName": "dns-only.tailnet.ts.net.",
                "OS": "linux",
                "Online": True,
                "AllowedIPs": ["100.64.0.9/32"],
            },
            "nodekey:dd": {
                "HostName": "",
                "DNSName": "",
                "Online": True,
            },
        },
    }
)


class ParseStatusTest(unittest.TestCase):
    def test_parses_peers_and_keeps_json_order(self):
        peers = parse_status(STATUS_JSON)
        self.assertEqual([p.name for p in peers], ["mac-mini", "sleeping", "dns-only"])
        mac = peers[0]
        self.assertEqual(mac.ip, "100.64.0.7")
        self.assertEqual(mac.dns, "mac-mini.tailnet.ts.net")
        self.assertEqual(mac.os, "macOS")
        self.assertTrue(mac.online)
        self.assertEqual(mac.tags, ("tag:server", "tag:media"))

    def test_offline_peer_is_still_returned(self):
        sleeping = parse_status(STATUS_JSON)[1]
        self.assertFalse(sleeping.online)

    def test_missing_tailscale_ips_falls_back_to_allowed_ips(self):
        peer = parse_status(STATUS_JSON)[2]
        self.assertEqual(peer.ip, "100.64.0.9")

    def test_missing_hostname_uses_dns_label(self):
        self.assertEqual(parse_status(STATUS_JSON)[2].name, "dns-only")

    def test_peer_without_name_or_address_is_dropped(self):
        self.assertEqual(len(parse_status(STATUS_JSON)), 3)

    def test_missing_tags_default_to_empty_tuple(self):
        self.assertEqual(parse_status(STATUS_JSON)[1].tags, ())

    def test_empty_peer_map(self):
        self.assertEqual(parse_status('{"Peer": {}}'), [])
        self.assertEqual(parse_status("{}"), [])

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_status("not json")

    def test_non_object_payload_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_status("[]")


class ListPeersTest(unittest.TestCase):
    def test_parses_status_output(self):
        completed = subprocess.CompletedProcess(
            args=["tailscale", "status", "--json"],
            returncode=0,
            stdout=STATUS_JSON,
            stderr="",
        )
        with patch(
            "telemachus_client.tailscale.subprocess.run", return_value=completed
        ) as run:
            peers = list_peers(timeout=2.5)
        run.assert_called_once_with(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        self.assertEqual(len(peers), 3)

    def test_missing_binary_returns_empty(self):
        with patch(
            "telemachus_client.tailscale.subprocess.run",
            side_effect=FileNotFoundError("tailscale"),
        ):
            self.assertEqual(list_peers(), [])

    def test_timeout_returns_empty(self):
        with patch(
            "telemachus_client.tailscale.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tailscale", timeout=1.0),
        ):
            self.assertEqual(list_peers(timeout=1.0), [])

    def test_garbage_output_returns_empty(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="daemon not running", stderr=""
        )
        with patch(
            "telemachus_client.tailscale.subprocess.run", return_value=completed
        ):
            self.assertEqual(list_peers(), [])

    def test_failed_command_without_output_returns_empty(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Tailscale is stopped."
        )
        with patch(
            "telemachus_client.tailscale.subprocess.run", return_value=completed
        ):
            self.assertEqual(list_peers(), [])


class TailscaleAvailableTest(unittest.TestCase):
    def test_available_when_which_finds_binary(self):
        with patch(
            "telemachus_client.tailscale.shutil.which",
            return_value="/usr/bin/tailscale",
        ):
            self.assertTrue(tailscale_available())

    def test_unavailable_when_which_returns_none(self):
        with patch("telemachus_client.tailscale.shutil.which", return_value=None):
            self.assertFalse(tailscale_available())


class DiscoverHostsTest(unittest.TestCase):
    def test_only_online_peers_with_open_port_are_returned(self):
        peers = [
            Peer("mac", "100.64.0.7", online=True),
            Peer("sleeping", "100.64.0.8", online=False),
            Peer("closed", "100.64.0.9", online=True),
        ]
        calls = []

        def fake_connect(address, timeout=None):
            calls.append((address, timeout))
            if address[0] == "100.64.0.9":
                raise ConnectionRefusedError("refused")
            return MagicMock()

        with patch(
            "telemachus_client.tailscale.socket.create_connection",
            side_effect=fake_connect,
        ):
            found = discover_hosts(peers, port=12345, probe_timeout=0.25)

        self.assertEqual([p.name for p in found], ["mac"])
        self.assertIn((("100.64.0.7", 12345), 0.25), calls)
        self.assertNotIn(("100.64.0.8", 12345), calls)
        self.assertIn((("100.64.0.9", 12345), 0.25), calls)

    def test_offline_peers_are_never_probed(self):
        peers = [Peer("sleeping", "100.64.0.8", online=False)]
        with patch(
            "telemachus_client.tailscale.socket.create_connection"
        ) as connect:
            self.assertEqual(discover_hosts(peers), [])
        connect.assert_not_called()

    def test_peer_without_ip_is_skipped(self):
        peers = [Peer("nameless", "", online=True)]
        with patch(
            "telemachus_client.tailscale.socket.create_connection"
        ) as connect:
            self.assertEqual(discover_hosts(peers), [])
        connect.assert_not_called()

    def test_probe_errors_exclude_peer(self):
        peers = [Peer("mac", "100.64.0.7", online=True)]
        with patch(
            "telemachus_client.tailscale.socket.create_connection",
            side_effect=OSError("unreachable"),
        ):
            self.assertEqual(discover_hosts(peers), [])

    def test_invalid_port_raises_value_error(self):
        with self.assertRaises(ValueError):
            discover_hosts([Peer("mac", "100.64.0.7", online=True)], port=70000)


class DiscoveryFlowTest(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            discover_tailscale=True,
            qr=None,
            selftest=False,
            fake_payload=False,
            host=None,
        )
        self.cfg = ResolvedConfig(
            host="127.0.0.1",
            port=54321,
            token=b"",
            name="linux",
            decoder="auto",
            display=None,
            fullscreen=True,
            input_enabled=True,
        )
        self.peer = Peer("mac-mini", "100.64.0.7", dns="mac-mini.ts.net", online=True)

    def test_qr_skips_discovery(self):
        self.args.qr = "telemachus://192.0.2.1:54321?t=secret&name=mac"
        with patch(
            "telemachus_client.tailscale.list_peers"
        ) as peers:
            self.assertIsNone(apply_tailscale_discovery(self.args, self.cfg))
        peers.assert_not_called()
        self.assertEqual(self.cfg.host, "127.0.0.1")

    def test_selection_sets_host(self):
        self.cfg.token = b"x" * 32
        with patch(
            "telemachus_client.tailscale.tailscale_available", return_value=True
        ), patch(
            "telemachus_client.tailscale.list_peers", return_value=[self.peer]
        ), patch(
            "telemachus_client.tailscale.discover_hosts", return_value=[self.peer]
        ), patch("builtins.input", return_value="1"), redirect_stdout(io.StringIO()):
            self.assertIsNone(apply_tailscale_discovery(self.args, self.cfg))
        self.assertEqual(self.cfg.host, "100.64.0.7")

    def test_cancel_does_not_connect(self):
        with patch(
            "telemachus_client.tailscale.tailscale_available", return_value=True
        ), patch(
            "telemachus_client.tailscale.list_peers", return_value=[self.peer]
        ), patch(
            "telemachus_client.tailscale.discover_hosts", return_value=[self.peer]
        ), patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertEqual(
                apply_tailscale_discovery(self.args, self.cfg), 1
            )
        self.assertEqual(self.cfg.host, "127.0.0.1")

    def test_no_token_prints_qr_command_and_exits(self):
        output = io.StringIO()
        with patch(
            "telemachus_client.tailscale.tailscale_available", return_value=True
        ), patch(
            "telemachus_client.tailscale.list_peers", return_value=[self.peer]
        ), patch(
            "telemachus_client.tailscale.discover_hosts", return_value=[self.peer]
        ), patch("builtins.input", return_value="1"), redirect_stdout(output):
            rc = apply_tailscale_discovery(self.args, self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("--qr", output.getvalue())
        self.assertIn("100.64.0.7", output.getvalue())


if __name__ == "__main__":
    unittest.main()
