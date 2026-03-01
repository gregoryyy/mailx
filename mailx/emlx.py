from __future__ import annotations

import email.utils
import hashlib
import re
import time
from pathlib import Path
from typing import Optional

from .logger import Logger
from .model import FALLBACK_DATE, FALLBACK_SENDER

# mboxrd From_ escaping — prepend > to any line matching ^>*From
FROM_ESCAPE_RE = re.compile(rb"^(>*From )", re.MULTILINE)
# mboxrd From_ unescaping — remove one leading >
FROM_UNESCAPE_RE = re.compile(rb"^>(>*From )", re.MULTILINE)


def parse_emlx(emlx_path: Path, logger: Optional[Logger] = None) -> tuple[Optional[bytes], bool]:
    """Parse an .emlx file and return (rfc822_bytes, is_partial)."""
    is_partial = emlx_path.name.endswith(".partial.emlx")

    try:
        with open(emlx_path, "rb") as f:
            first_line = f.readline()
            if not first_line:
                if logger:
                    logger.warn(f"Empty file: {emlx_path}")
                return (None, is_partial)

            try:
                byte_count = int(first_line.strip())
            except ValueError:
                if logger:
                    logger.warn(f"Invalid byte count in {emlx_path}: {first_line!r}")
                return (None, is_partial)

            if byte_count < 0:
                if logger:
                    logger.warn(f"Negative byte count in {emlx_path}: {byte_count}")
                return (None, is_partial)

            rfc822_bytes = f.read(byte_count)
            if len(rfc822_bytes) != byte_count:
                if logger:
                    logger.warn(
                        f"Truncated file {emlx_path}: expected {byte_count} bytes, "
                        f"got {len(rfc822_bytes)}"
                    )
                return (None, is_partial)

            return (rfc822_bytes, is_partial)

    except PermissionError:
        if logger:
            logger.warn(f"Permission denied: {emlx_path}")
        return (None, is_partial)
    except FileNotFoundError:
        if logger:
            logger.warn(f"File not found: {emlx_path}")
        return (None, is_partial)
    except OSError as e:
        if logger:
            logger.warn(f"I/O error reading {emlx_path}: {e}")
        return (None, is_partial)


def _extract_from_and_date(rfc822_bytes: bytes) -> tuple[str, str]:
    """Extract sender address and asctime date from RFC 822 headers."""
    search_bytes = rfc822_bytes[:16384]
    headers = search_bytes
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = search_bytes.find(sep)
        if idx != -1:
            headers = search_bytes[:idx]
            break

    headers = re.sub(rb"\r?\n[ \t]+", b" ", headers)

    sender = FALLBACK_SENDER
    from_match = re.search(rb"^From:\s*(.+?)$", headers, re.MULTILINE | re.IGNORECASE)
    if from_match:
        try:
            decoded = from_match.group(1).decode("utf-8", errors="replace").strip()
            _, addr = email.utils.parseaddr(decoded)
            if addr:
                sender = addr
        except Exception:
            pass

    date_str = FALLBACK_DATE
    date_match = re.search(rb"^Date:\s*(.+?)$", headers, re.MULTILINE | re.IGNORECASE)
    if date_match:
        try:
            decoded = date_match.group(1).decode("utf-8", errors="replace").strip()
            dt = email.utils.parsedate_to_datetime(decoded)
            date_str = time.asctime(dt.timetuple())
        except Exception:
            pass

    return sender, date_str


def _escape_from_lines(rfc822_bytes: bytes) -> bytes:
    """mboxrd escaping: prepend > to any line matching ^>*From ."""
    return FROM_ESCAPE_RE.sub(rb">\1", rfc822_bytes)


def _unescape_from_lines(msg_bytes: bytes) -> bytes:
    """mboxrd unescaping: remove one leading > from ^>>*From lines."""
    return FROM_UNESCAPE_RE.sub(rb"\1", msg_bytes)


def _hash_for_mbox_verification(rfc822_bytes: bytes) -> str:
    """Hash message bytes in normalized form used by mbox verification."""
    hash_bytes = rfc822_bytes
    if not rfc822_bytes.endswith(b"\n"):
        hash_bytes = rfc822_bytes + b"\n"
    return hashlib.sha256(hash_bytes).hexdigest()


def _make_emlx(rfc822_bytes: bytes) -> bytes:
    """Build an .emlx file: byte count + message."""
    return f"{len(rfc822_bytes)}\n".encode("ascii") + rfc822_bytes
