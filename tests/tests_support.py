"""Shared fixtures, helpers, and test data for apple-mail-export tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The module file uses hyphens (apple-mail-export.py), which isn't a valid
# Python identifier, so we use importlib to load it.
_spec = importlib.util.spec_from_file_location(
    "apple_mail_export",
    REPO_ROOT / "apple-mail-export.py",
)
ame = importlib.util.module_from_spec(_spec)
sys.modules["apple_mail_export"] = ame
_spec.loader.exec_module(ame)


@pytest.fixture
def logger():
    return ame.Logger(quiet=True, verbose=False)


@pytest.fixture
def verbose_logger():
    return ame.Logger(quiet=False, verbose=True)


def make_emlx(rfc822_bytes: bytes) -> bytes:
    """Build .emlx content: byte count line + RFC 822 message."""
    return f"{len(rfc822_bytes)}\n".encode("ascii") + rfc822_bytes


PLAIN_MSG = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n"
    b"Subject: Test\r\n"
    b"\r\n"
    b"Hello world.\r\n"
)

MSG_WITH_FROM = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n"
    b"Subject: From test\r\n"
    b"\r\n"
    b"Hello.\r\n"
    b"From the desk of Alice.\r\n"
    b">From here too.\r\n"
    b">>From deep.\r\n"
)

HTML_MSG = (
    b"From: bob@example.com\r\n"
    b"To: alice@example.com\r\n"
    b"Date: Fri, 02 Jan 2026 13:00:00 +0000\r\n"
    b"Subject: HTML\r\n"
    b"Content-Type: text/html\r\n"
    b"\r\n"
    b"<html><body><h1>Hi</h1></body></html>\r\n"
)


@pytest.fixture
def mail_tree(tmp_path):
    """Build a synthetic Apple Mail directory tree.

    Returns (mail_root, output_dir) where mail_root contains:
      V10/ACCT-UUID/INBOX.mbox/Messages/{1,2,3}.emlx + 4.partial.emlx + 5(corrupt)
      V10/ACCT-UUID/Work.mbox/Projects.mbox/Messages/100.emlx
    """
    mail_root = tmp_path / "V10"
    acct = mail_root / "ACCT-UUID"

    # INBOX
    inbox_msgs = acct / "INBOX.mbox" / "Messages"
    inbox_msgs.mkdir(parents=True)
    (inbox_msgs / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))
    (inbox_msgs / "2.emlx").write_bytes(make_emlx(HTML_MSG))
    (inbox_msgs / "3.emlx").write_bytes(make_emlx(MSG_WITH_FROM))
    (inbox_msgs / "4.partial.emlx").write_bytes(
        make_emlx(b"From: x@x.com\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\npartial\r\n")
    )
    # corrupt: byte count says 9999 but content is short
    (inbox_msgs / "5.emlx").write_bytes(b"9999\nshort")

    # Nested: Work/Projects
    nested_msgs = acct / "Work.mbox" / "Projects.mbox" / "Messages"
    nested_msgs.mkdir(parents=True)
    (nested_msgs / "100.emlx").write_bytes(make_emlx(PLAIN_MSG))

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    return tmp_path, output_dir
