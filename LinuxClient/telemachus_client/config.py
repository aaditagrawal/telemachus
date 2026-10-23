"""INI file + CLI configuration for the Linux client (stdlib only).

File layout (``~/.config/telemachus/client.conf``)::

    [client]
    host = 192.168.1.10
    port = 54321
    token = BASE64URL_32_BYTE_TOKEN
    name = my-linux-box
    decoder = auto
    display = :0
    fullscreen = true
    input_enabled = true

Precedence is CLI > file > defaults. CLI parsers must use ``default=None``
for every overridable option so "unset" is distinguishable from an explicit
value (in particular, boolean flags must default to ``None``, not
``False``, or they would silently override the file).

:func:`save_config` is the write side of that file (pairing persistence): it
merges into whatever the file already holds, writes atomically, and keeps the
file mode at ``0600`` because it carries the bearer token.
"""

from __future__ import annotations

import base64
import binascii
import configparser
import logging
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from .protocol import DEFAULT_PORT

log = logging.getLogger("telemachus.client.config")

CONFIG_PATH = Path.home() / ".config" / "telemachus" / "client.conf"

#: Keys written to the file, in a stable order (diff-friendly).
CONFIG_KEYS: tuple[str, ...] = (
    "host",
    "port",
    "token",
    "name",
    "decoder",
    "display",
    "fullscreen",
    "input_enabled",
)

_TOKEN_B64URL_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


@dataclass
class ClientConfig:
    host: str
    port: int
    token: bytes
    name: str
    decoder: str = "auto"
    display: Optional[str] = None
    fullscreen: bool = True
    input_enabled: bool = True


def format_token(b64url: str) -> bytes:
    """Decode a base64url (no padding) token string to its 32 raw bytes."""
    if isinstance(b64url, bytes):
        try:
            text = b64url.decode("ascii")
        except UnicodeDecodeError:
            raise ValueError("token is not ASCII base64url") from None
    else:
        text = b64url.strip()
    if not _TOKEN_B64URL_RE.match(text):
        raise ValueError("token is not base64url")
    raw = text.encode("ascii") + b"=" * (-len(text) % 4)
    try:
        token = base64.b64decode(raw, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"malformed token: {exc}") from None
    if len(token) != 32:
        raise ValueError(f"token must decode to 32 bytes, got {len(token)}")
    return token


_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def _truncate_name_64(name: str) -> str:
    raw = name.encode("utf-8")
    if len(raw) <= 64:
        return name
    cut = raw[:64]
    while True:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]


def load_config(path: Union[Path, str, None] = None) -> Dict[str, Any]:
    """Read the INI file; return coerced settings, or ``{}`` if absent.

    Only the ``[client]`` section is read (a section-less file is treated as
    if its keys were under ``[client]``). ``port`` is coerced to int and
    ``fullscreen``/``input_enabled`` to bool; ``token`` is kept as the raw
    base64url string and decoded later by :func:`build_config`.
    """
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ValueError(f"cannot read config {cfg_path}: {exc}") from None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        try:
            parser.read_string(text)
        except configparser.MissingSectionHeaderError:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string("[client]\n" + text)
    except configparser.Error as exc:
        raise ValueError(f"invalid config {cfg_path}: {exc}") from None
    if not parser.has_section("client"):
        return {}
    section = parser["client"]
    out: Dict[str, Any] = {}
    for key in (
        "host",
        "port",
        "token",
        "name",
        "decoder",
        "display",
        "fullscreen",
        "input_enabled",
    ):
        if key not in section:
            continue
        raw_value = section[key]
        try:
            if key == "port":
                out[key] = int(raw_value)
            elif key in ("fullscreen", "input_enabled"):
                out[key] = _parse_bool(raw_value)
            else:
                out[key] = raw_value
        except ValueError as exc:
            raise ValueError(
                f"invalid config value for {key!r} in {cfg_path}: {exc}"
            ) from None
    return out


def _cli_get(cli_args: Any, key: str) -> Any:
    if cli_args is None:
        return None
    if isinstance(cli_args, Mapping):
        return cli_args.get(key)
    return getattr(cli_args, key, None)


def _pick(cli_args: Any, file_cfg: Mapping[str, Any], key: str, default: Any) -> Any:
    cli_value = _cli_get(cli_args, key)
    if cli_value is not None:
        return cli_value
    if key in file_cfg:
        return file_cfg[key]
    return default


def build_config(cli_args: Any, file_cfg: Mapping[str, Any]) -> ClientConfig:
    """Merge CLI args over file settings over defaults into a ClientConfig.

    ``cli_args`` may be an ``argparse.Namespace``, a plain dict, or ``None``;
    ``None`` values mean "unset". ``token`` accepts a base64url string or
    raw 32 bytes; when absent it defaults to ``b""`` (USB/loopback mode,
    where :meth:`StreamClient.connect` skips the handshake). ``name``
    defaults to the local hostname truncated to 64 UTF-8 bytes.
    """
    host = str(_pick(cli_args, file_cfg, "host", "127.0.0.1"))
    try:
        port = int(_pick(cli_args, file_cfg, "port", DEFAULT_PORT))  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid port: {exc}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be 1..65535, got {port}")

    token_raw = _pick(cli_args, file_cfg, "token", None)
    if token_raw is None or token_raw == "":
        token = b""
    elif isinstance(token_raw, bytes):
        if len(token_raw) != 32:
            raise ValueError(f"token must be 32 bytes, got {len(token_raw)}")
        token = bytes(token_raw)
    elif isinstance(token_raw, str):
        token = format_token(token_raw)
    else:
        raise ValueError(f"token must be base64url str or 32 bytes, got {type(token_raw)}")

    name_raw = _pick(cli_args, file_cfg, "name", None)
    if name_raw is None or str(name_raw) == "":
        try:
            name_raw = socket.gethostname()
        except OSError:
            name_raw = "linux"
    name = _truncate_name_64(str(name_raw))
    if not name.encode("utf-8"):
        name = "linux"

    fullscreen_raw = _pick(cli_args, file_cfg, "fullscreen", True)
    input_raw = _pick(cli_args, file_cfg, "input_enabled", True)
    try:
        fullscreen = _parse_bool(fullscreen_raw)
        input_enabled = _parse_bool(input_raw)
    except ValueError as exc:
        raise ValueError(f"invalid boolean config: {exc}") from None

    display_raw = _pick(cli_args, file_cfg, "display", None)
    return ClientConfig(
        host=host,
        port=port,
        token=token,
        name=name,
        decoder=str(_pick(cli_args, file_cfg, "decoder", "auto")),
        display=None if display_raw is None else str(display_raw),
        fullscreen=fullscreen,
        input_enabled=input_enabled,
    )


def _is_unset(value: Any) -> bool:
    """True for "remove this key": ``None``, an empty/blank string, or b""."""
    if value is None:
        return True
    if isinstance(value, bytes):
        return len(value) == 0
    return str(value).strip() == ""


def _token_to_b64url(value: Any) -> str:
    """Serialize a token for the file; validate it exactly like the reader.

    Accepts the raw 32 bytes used on the wire or an already-base64url string.
    """
    if isinstance(value, bytes):
        if len(value) != 32:
            raise ValueError(f"token must be 32 bytes, got {len(value)}")
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    text = str(value).strip()
    format_token(text)  # raises ValueError unless it decodes to 32 bytes
    return text


def _serialize_values(values: Mapping[str, Any]) -> Dict[str, str]:
    """Coerce config values to their INI text form, in :data:`CONFIG_KEYS` order.

    Unset values (see :func:`_is_unset`) are omitted so callers can clear a key
    by passing ``None``/``""``. Raises :class:`ValueError` on a malformed token,
    port, or boolean.
    """
    out: Dict[str, str] = {}
    for key in CONFIG_KEYS:
        if key not in values or _is_unset(values[key]):
            continue
        value = values[key]
        if key == "port":
            try:
                port = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid port: {exc}") from None
            if not 1 <= port <= 65535:
                raise ValueError(f"port must be 1..65535, got {port}")
            out[key] = str(port)
        elif key == "token":
            out[key] = _token_to_b64url(value)
        elif key in ("fullscreen", "input_enabled"):
            out[key] = "true" if _parse_bool(value) else "false"
        else:
            out[key] = str(value).strip()
    return out


def save_config(values: Mapping[str, Any], path: Union[Path, str, None] = None) -> Path:
    """Persist ``values`` to the INI file (merge), returning the path written.

    Existing keys the caller did not mention are preserved, so a pairing save
    that carries only host/port/token/name does not drop a hand-set decoder or
    display. A key mapped to ``None`` or ``""`` is *removed* (that is how
    :func:`delete_token` clears the token). ``token`` accepts raw 32 bytes or a
    base64url string; ``port``, ``fullscreen``, and ``input_enabled`` are
    validated before anything is written, so a rejected value leaves the file
    untouched.

    The write is atomic: the parent directory is created, a temp file in the
    same directory is written and fsynced, then ``os.replace`` swaps it in, so
    a crash can never leave a truncated config behind. The file is mode
    ``0600`` (it carries the bearer token).
    """
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    unknown = [key for key in values if key not in CONFIG_KEYS]
    if unknown:
        # A typo must not look like a successful save: the user would pair,
        # see nothing wrong, and get a client with no host next run.
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    try:
        merged: Dict[str, Any] = dict(load_config(cfg_path))
    except ValueError as exc:
        # A corrupt file is unusable as a base; the caller's explicit values
        # are still worth persisting. Say so instead of failing the pairing.
        log.warning("ignoring unreadable config %s: %s", cfg_path, exc)
        merged = {}
    for key in CONFIG_KEYS:
        if key not in values:
            continue
        if _is_unset(values[key]):
            merged.pop(key, None)
        else:
            merged[key] = values[key]

    serialized = _serialize_values(merged)
    lines = ["[client]"]
    lines.extend(f"{key} = {serialized[key]}" for key in CONFIG_KEYS if key in serialized)
    text = "\n".join(lines) + "\n"

    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create {cfg_path.parent}: {exc}") from None
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{cfg_path.name}.", suffix=".tmp", dir=str(cfg_path.parent)
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, cfg_path)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ValueError(f"cannot write config {cfg_path}: {exc}") from None
    return cfg_path


def delete_token(path: Union[Path, str, None] = None) -> bool:
    """Remove the saved token; return whether one was there.

    Other settings survive. The file itself is deleted when the token was its
    only content (an empty config is the same as no config), otherwise it is
    rewritten through :func:`save_config` so permissions stay ``0600``.
    """
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    existing = load_config(cfg_path)
    if "token" not in existing:
        return False
    if len(existing) == 1:
        try:
            cfg_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ValueError(f"cannot remove config {cfg_path}: {exc}") from None
        return True
    save_config({"token": None}, path=cfg_path)
    return True
