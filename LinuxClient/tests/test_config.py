"""Tests for the config-file write path (pairing persistence).

``save_config`` is the write side of ``~/.config/telemachus/client.conf``: it
must round-trip through ``load_config``, keep the file at mode 0600 (it holds
the bearer token), and replace it atomically so a crash or a full disk can
never leave a half-written config behind.
"""

import base64
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemachus_client import config  # noqa: E402
from telemachus_client.__main__ import ResolvedConfig, _persist_pairing  # noqa: E402
from telemachus_client.config import (  # noqa: E402
    build_config,
    delete_token,
    load_config,
    save_config,
)

TOKEN = bytes(range(32))
TOKEN_B64 = base64.urlsafe_b64encode(TOKEN).rstrip(b"=").decode("ascii")


class SaveConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "telemachus" / "client.conf"

    def test_round_trip_save_then_load(self):
        save_config(
            {
                "host": "100.64.0.7",
                "port": 54321,
                "token": TOKEN,
                "name": "zorin",
                "decoder": "vaapi",
                "display": "HP",
                "fullscreen": False,
                "input_enabled": True,
            },
            path=self.path,
        )
        self.assertEqual(
            load_config(self.path),
            {
                "host": "100.64.0.7",
                "port": 54321,
                "token": TOKEN_B64,
                "name": "zorin",
                "decoder": "vaapi",
                "display": "HP",
                "fullscreen": False,
                "input_enabled": True,
            },
        )
        self.assertEqual(save_config({}, path=self.path), self.path)

    def test_saved_token_rebuilds_the_wire_bytes(self):
        save_config({"host": "h", "port": 1, "token": TOKEN}, path=self.path)
        built = build_config(None, load_config(self.path))
        self.assertEqual(built.token, TOKEN)

    def test_token_string_is_accepted_and_validated(self):
        save_config({"token": TOKEN_B64}, path=self.path)
        self.assertEqual(load_config(self.path)["token"], TOKEN_B64)
        with self.assertRaises(ValueError):
            save_config({"token": "not-base64url!"}, path=self.path)
        with self.assertRaises(ValueError):
            save_config({"token": b"too short"}, path=self.path)

    def test_creates_parent_directories(self):
        self.assertFalse(self.path.parent.exists())
        save_config({"host": "100.64.0.7"}, path=self.path)
        self.assertTrue(self.path.is_file())

    def test_mode_is_0600_on_create(self):
        save_config({"token": TOKEN}, path=self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_mode_is_tightened_when_overwriting_a_loose_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[client]\nhost = old\n", encoding="utf-8")
        os.chmod(self.path, 0o644)
        save_config({"host": "new"}, path=self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_replacement_swaps_the_inode_and_leaves_no_temp_file(self):
        save_config({"host": "first"}, path=self.path)
        first_inode = os.stat(self.path).st_ino
        save_config({"host": "second"}, path=self.path)
        self.assertNotEqual(os.stat(self.path).st_ino, first_inode)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["client.conf"])
        self.assertEqual(load_config(self.path)["host"], "second")

    def test_failed_replace_keeps_the_old_file_and_no_debris(self):
        save_config({"host": "good", "port": 54321}, path=self.path)
        with patch.object(config.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError):
                save_config({"host": "bad"}, path=self.path)
        self.assertEqual(load_config(self.path), {"host": "good", "port": 54321})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["client.conf"])

    def test_failed_first_write_leaves_no_file(self):
        with patch.object(config.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(ValueError):
                save_config({"host": "bad"}, path=self.path)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_unspecified_keys_are_preserved(self):
        save_config({"host": "old", "decoder": "cuda", "display": "HP"}, path=self.path)
        save_config({"host": "new", "port": 41000, "token": TOKEN, "name": "zorin"},
                    path=self.path)
        loaded = load_config(self.path)
        self.assertEqual(loaded["decoder"], "cuda")
        self.assertEqual(loaded["display"], "HP")
        self.assertEqual(loaded["host"], "new")
        self.assertEqual(loaded["token"], TOKEN_B64)

    def test_none_and_blank_remove_keys(self):
        save_config({"host": "h", "token": TOKEN, "decoder": "sw"}, path=self.path)
        save_config({"token": None, "decoder": ""}, path=self.path)
        self.assertEqual(load_config(self.path), {"host": "h"})

    def test_invalid_value_does_not_touch_the_file(self):
        original = "[client]\nhost = good\nport = 54321\n"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(original, encoding="utf-8")
        for bad in ({"port": 0}, {"port": 70000}, {"port": "abc"},
                    {"fullscreen": "maybe"}, {"token": b"short"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    save_config(bad, path=self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_corrupt_existing_file_is_replaced_not_fatal(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[client]\nport = not-a-number\n", encoding="utf-8")
        save_config({"port": 41000}, path=self.path)
        self.assertEqual(load_config(self.path), {"port": 41000})

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            save_config({"hosst": "100.64.0.7"}, path=self.path)
        self.assertFalse(self.path.exists())

    def test_default_path_is_config_path(self):
        with patch.object(config, "CONFIG_PATH", self.path):
            save_config({"host": "100.64.0.7", "port": 41000})
            self.assertEqual(load_config(), {"host": "100.64.0.7", "port": 41000})


class DeleteTokenTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "client.conf"

    def test_removes_token_and_keeps_the_rest(self):
        save_config({"host": "h", "port": 41000, "token": TOKEN, "name": "zorin"},
                    path=self.path)
        self.assertTrue(delete_token(self.path))
        self.assertEqual(load_config(self.path), {"host": "h", "port": 41000,
                                                  "name": "zorin"})
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_deletes_a_token_only_file(self):
        save_config({"token": TOKEN}, path=self.path)
        self.assertTrue(delete_token(self.path))
        self.assertFalse(self.path.exists())

    def test_returns_false_when_no_token_is_saved(self):
        save_config({"host": "h"}, path=self.path)
        self.assertFalse(delete_token(self.path))
        self.assertTrue(self.path.exists())
        self.assertFalse(delete_token(Path(self._tmp.name) / "absent.conf"))


class PersistPairingTest(unittest.TestCase):
    """``--qr`` must leave the pairing on disk for the next run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "client.conf"
        self.cfg = ResolvedConfig(
            host="100.64.0.7", port=54321, token=TOKEN, name="zorin",
            decoder="auto", display=None, fullscreen=True, input_enabled=True,
        )

    def test_writes_the_connection_facts(self):
        with patch.object(config, "CONFIG_PATH", self.path):
            _persist_pairing(self.cfg)
        self.assertEqual(
            load_config(self.path),
            {"host": "100.64.0.7", "port": 54321, "token": TOKEN_B64,
             "name": "zorin"},
        )

    def test_keeps_unrelated_settings_already_in_the_file(self):
        save_config({"decoder": "cuda", "display": "HP"}, path=self.path)
        with patch.object(config, "CONFIG_PATH", self.path):
            _persist_pairing(self.cfg)
        loaded = load_config(self.path)
        self.assertEqual(loaded["decoder"], "cuda")
        self.assertEqual(loaded["display"], "HP")
        self.assertEqual(loaded["host"], "100.64.0.7")

    def test_write_failure_is_not_fatal(self):
        with patch.object(config, "CONFIG_PATH", self.path), \
                patch.object(config, "save_config", side_effect=ValueError("nope")):
            _persist_pairing(self.cfg)  # must not raise


if __name__ == "__main__":
    unittest.main()
