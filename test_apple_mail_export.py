"""Tests for apple-mail-export."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import importlib

import pytest

sys.path.insert(0, str(Path(__file__).parent))

# The module file uses hyphens (apple-mail-export.py), which isn't a valid
# Python identifier, so we use importlib to load it.
_spec = importlib.util.spec_from_file_location(
    "apple_mail_export",
    Path(__file__).parent / "apple-mail-export.py",
)
ame = importlib.util.module_from_spec(_spec)
sys.modules["apple_mail_export"] = ame
_spec.loader.exec_module(ame)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ===================================================================
# Parser tests
# ===================================================================


class TestParseEmlx:
    def test_valid_message(self, tmp_path, logger):
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        body, is_partial = ame.parse_emlx(p, logger)
        assert body == PLAIN_MSG
        assert is_partial is False

    def test_partial_flag(self, tmp_path, logger):
        p = tmp_path / "1.partial.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        body, is_partial = ame.parse_emlx(p, logger)
        assert body == PLAIN_MSG
        assert is_partial is True

    def test_corrupt_bad_byte_count(self, tmp_path, logger):
        p = tmp_path / "bad.emlx"
        p.write_bytes(b"notanumber\nsome content")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body is None
        assert is_partial is False

    def test_corrupt_truncated(self, tmp_path, logger):
        p = tmp_path / "trunc.emlx"
        p.write_bytes(b"9999\nshort")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body is None

    def test_empty_file(self, tmp_path, logger):
        p = tmp_path / "empty.emlx"
        p.write_bytes(b"")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body is None

    def test_negative_byte_count(self, tmp_path, logger):
        p = tmp_path / "neg.emlx"
        p.write_bytes(b"-5\ncontent")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body is None

    def test_zero_byte_count(self, tmp_path, logger):
        p = tmp_path / "zero.emlx"
        p.write_bytes(b"0\ntrailing plist xml")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body == b""
        assert is_partial is False

    def test_file_not_found(self, tmp_path, logger):
        p = tmp_path / "nonexistent.emlx"
        body, is_partial = ame.parse_emlx(p, logger)
        assert body is None

    def test_with_trailing_plist(self, tmp_path, logger):
        """Parser should read only byte_count bytes, ignoring trailing plist."""
        plist = b'<?xml version="1.0"?><plist><dict></dict></plist>'
        emlx = make_emlx(PLAIN_MSG) + plist
        p = tmp_path / "plist.emlx"
        p.write_bytes(emlx)
        body, _ = ame.parse_emlx(p, logger)
        assert body == PLAIN_MSG

    def test_no_logger(self, tmp_path):
        """Parser works without a logger."""
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        body, _ = ame.parse_emlx(p)
        assert body == PLAIN_MSG


# ===================================================================
# From_ escaping / unescaping tests
# ===================================================================


class TestFromEscaping:
    def test_escape_from_line(self):
        assert ame._escape_from_lines(b"From foo\n") == b">From foo\n"

    def test_escape_already_escaped(self):
        assert ame._escape_from_lines(b">From foo\n") == b">>From foo\n"

    def test_escape_double_escaped(self):
        assert ame._escape_from_lines(b">>From foo\n") == b">>>From foo\n"

    def test_no_escape_needed(self):
        assert ame._escape_from_lines(b"Hello world\n") == b"Hello world\n"

    def test_from_mid_word_not_escaped(self):
        """'Fromage' should not be escaped — only 'From ' with space."""
        assert ame._escape_from_lines(b"Fromage\n") == b"Fromage\n"

    def test_multiline_escaping(self):
        inp = b"line1\nFrom sender\nline3\n>From x\n"
        out = ame._escape_from_lines(inp)
        assert out == b"line1\n>From sender\nline3\n>>From x\n"

    def test_unescape_from_line(self):
        assert ame._unescape_from_lines(b">From foo\n") == b"From foo\n"

    def test_unescape_double(self):
        assert ame._unescape_from_lines(b">>From foo\n") == b">From foo\n"

    def test_unescape_no_op(self):
        """Plain 'From ' should not be unescaped (no leading >)."""
        assert ame._unescape_from_lines(b"From foo\n") == b"From foo\n"

    def test_roundtrip(self):
        """Escape then unescape should recover original."""
        original = b"Hello\nFrom Alice\n>From Bob\n>>From Carol\nBye\n"
        escaped = ame._escape_from_lines(original)
        recovered = ame._unescape_from_lines(escaped)
        assert recovered == original

    def test_roundtrip_empty(self):
        assert ame._unescape_from_lines(ame._escape_from_lines(b"")) == b""

    def test_roundtrip_no_from(self):
        data = b"Just\nsome\ntext\n"
        assert ame._unescape_from_lines(ame._escape_from_lines(data)) == data


# ===================================================================
# Header extraction tests
# ===================================================================


class TestExtractFromAndDate:
    def test_basic(self):
        sender, date = ame._extract_from_and_date(PLAIN_MSG)
        assert sender == "alice@example.com"
        assert "2026" in date
        assert "Jan" in date

    def test_angle_bracket_from(self):
        msg = b"From: Alice <alice@test.com>\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\n"
        sender, _ = ame._extract_from_and_date(msg)
        assert sender == "alice@test.com"

    def test_missing_from_header(self):
        msg = b"To: bob@example.com\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\nbody\r\n"
        sender, _ = ame._extract_from_and_date(msg)
        assert sender == ame.FALLBACK_SENDER

    def test_missing_date_header(self):
        msg = b"From: alice@example.com\r\n\r\nbody\r\n"
        _, date = ame._extract_from_and_date(msg)
        assert date == ame.FALLBACK_DATE

    def test_no_headers(self):
        msg = b"just body content\r\n"
        sender, date = ame._extract_from_and_date(msg)
        assert sender == ame.FALLBACK_SENDER
        assert date == ame.FALLBACK_DATE

    def test_folded_from_header(self):
        """From header with continuation line."""
        msg = (
            b"From: Very Long Display Name\r\n"
            b" <alice@example.com>\r\n"
            b"Date: Thu, 01 Jan 2026 00:00:00 +0000\r\n"
            b"\r\n"
            b"body\r\n"
        )
        sender, _ = ame._extract_from_and_date(msg)
        assert sender == "alice@example.com"

    def test_lf_only_headers(self):
        """Handle headers with LF-only line endings."""
        msg = b"From: alice@test.com\nDate: Thu, 01 Jan 2026 00:00:00 +0000\n\nbody\n"
        sender, date = ame._extract_from_and_date(msg)
        assert sender == "alice@test.com"
        assert "2026" in date

    def test_rfc2047_encoded_from(self):
        """RFC 2047 display name — addr should still be extractable."""
        msg = b"From: =?UTF-8?Q?M=C3=BCller?= <mueller@example.com>\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\n"
        sender, _ = ame._extract_from_and_date(msg)
        assert sender == "mueller@example.com"

    def test_empty_message(self):
        sender, date = ame._extract_from_and_date(b"")
        assert sender == ame.FALLBACK_SENDER
        assert date == ame.FALLBACK_DATE


# ===================================================================
# Scanner tests
# ===================================================================


class TestScanner:
    def test_discovers_mailboxes(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "*", logger)
        names = {mb.name for mb in mailboxes}
        assert "INBOX" in names
        assert "Work/Projects" in names

    def test_message_counts(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "*", logger)
        by_name = {mb.name: mb for mb in mailboxes}
        assert by_name["INBOX"].message_count == 5  # 3 valid + 1 partial + 1 corrupt
        assert by_name["Work/Projects"].message_count == 1

    def test_filter_pattern(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "INBOX", logger)
        assert len(mailboxes) == 1
        assert mailboxes[0].name == "INBOX"

    def test_filter_wildcard_pattern(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "Work/*", logger)
        assert len(mailboxes) == 1
        assert mailboxes[0].name == "Work/Projects"

    def test_filter_no_match(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "Nonexistent", logger)
        assert len(mailboxes) == 0

    def test_nonexistent_dir(self, tmp_path, logger):
        mailboxes = ame.scan_mailboxes(tmp_path / "no-such-dir", "*", logger)
        assert mailboxes == []

    def test_empty_mail_dir(self, tmp_path, logger):
        mail = tmp_path / "empty"
        mail.mkdir()
        mailboxes = ame.scan_mailboxes(mail, "*", logger)
        assert mailboxes == []

    def test_numeric_sort_order(self, mail_tree, logger):
        """emlx files should be sorted numerically, not lexically."""
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "INBOX", logger)
        inbox = mailboxes[0]
        nums = [int(p.name.split(".")[0]) for p in inbox.emlx_files]
        assert nums == sorted(nums)

    def test_skips_maildata(self, tmp_path, logger):
        """MailData/ directory should be skipped."""
        v10 = tmp_path / "V10"
        maildata = v10 / "MailData"
        maildata.mkdir(parents=True)
        # Put a fake .mbox inside MailData — should be ignored
        fake = maildata / "Fake.mbox" / "Messages"
        fake.mkdir(parents=True)
        (fake / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))

        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 0

    def test_duplicate_names_across_accounts(self, tmp_path, logger):
        """Two accounts with INBOX should be disambiguated."""
        for uuid in ("AAAA-1111", "BBBB-2222"):
            msgs = tmp_path / "V10" / uuid / "INBOX.mbox" / "Messages"
            msgs.mkdir(parents=True)
            (msgs / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))

        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        names = [mb.name for mb in mailboxes]
        assert len(names) == 2
        # Both should be disambiguated
        assert all("INBOX" in n for n in names)
        assert names[0] != names[1]

    def test_account_id_populated(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "*", logger)
        for mb in mailboxes:
            assert mb.account_id == "ACCT-UUID"

    def test_skips_empty_mailbox(self, tmp_path, logger):
        """A .mbox dir with Messages/ but no .emlx files should be skipped."""
        msgs = tmp_path / "V10" / "UUID" / "Empty.mbox" / "Messages"
        msgs.mkdir(parents=True)
        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 0


# ===================================================================
# Writer tests
# ===================================================================


class TestWriter:
    def test_basic_export(self, tmp_path, logger):
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"

        result = ame.write_mbox(out, [emlx], "test", logger)

        assert result.messages_written == 1
        assert result.messages_failed == 0
        assert result.partial_count == 0
        assert result.bytes_written > 0
        assert out.exists()
        content = out.read_bytes()
        assert content.startswith(b"From alice@example.com ")

    def test_corrupt_counted_as_failure(self, tmp_path, logger):
        emlx = tmp_path / "bad.emlx"
        emlx.write_bytes(b"9999\nshort")
        out = tmp_path / "test.mbox"

        result = ame.write_mbox(out, [emlx], "test", logger)

        assert result.messages_written == 0
        assert result.messages_failed == 1
        assert len(result.failed_paths) == 1

    def test_partial_counted(self, tmp_path, logger):
        emlx = tmp_path / "1.partial.emlx"
        emlx.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"

        result = ame.write_mbox(out, [emlx], "test", logger)

        assert result.messages_written == 1
        assert result.partial_count == 1

    def test_mixed_valid_and_corrupt(self, tmp_path, logger):
        (tmp_path / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))
        (tmp_path / "2.emlx").write_bytes(b"9999\nbad")
        (tmp_path / "3.emlx").write_bytes(make_emlx(HTML_MSG))
        out = tmp_path / "test.mbox"

        files = sorted(tmp_path.glob("*.emlx"), key=lambda p: p.name)
        result = ame.write_mbox(out, files, "test", logger)

        assert result.messages_written == 2
        assert result.messages_failed == 1

    def test_from_escaping_in_output(self, tmp_path, logger):
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(make_emlx(MSG_WITH_FROM))
        out = tmp_path / "test.mbox"

        ame.write_mbox(out, [emlx], "test", logger)

        content = out.read_bytes()
        # Body "From the desk" should be escaped to ">From the desk"
        assert b"\n>From the desk" in content
        # ">From here" should be escaped to ">>From here"
        assert b"\n>>From here" in content
        # ">>From deep" should be escaped to ">>>From deep"
        assert b"\n>>>From deep" in content

    def test_hashes_populated(self, tmp_path, logger):
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"

        result = ame.write_mbox(out, [emlx], "test", logger)

        assert "1.emlx" in result.hashes
        expected_hash = hashlib.sha256(PLAIN_MSG).hexdigest()
        assert result.hashes["1.emlx"] == expected_hash

    def test_progress_callback(self, tmp_path, logger):
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"
        calls = []

        ame.write_mbox(out, [emlx], "test", logger, progress_callback=lambda c, t: calls.append((c, t)))

        assert calls == [(1, 1)]

    def test_mbox_ends_with_newlines(self, tmp_path, logger):
        emlx = tmp_path / "1.emlx"
        emlx.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"

        ame.write_mbox(out, [emlx], "test", logger)

        content = out.read_bytes()
        # mbox message should end with blank line (two newlines)
        assert content.endswith(b"\n\n")

    def test_empty_file_list(self, tmp_path, logger):
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [], "test", logger)

        assert result.messages_written == 0
        assert result.messages_failed == 0
        assert result.bytes_written == 0


# ===================================================================
# Verifier tests
# ===================================================================


class TestVerifier:
    def _export_and_verify(self, tmp_path, logger, messages):
        """Helper: write messages to emlx files, export to mbox, verify."""
        files = []
        for i, msg in enumerate(messages, 1):
            p = tmp_path / f"{i}.emlx"
            p.write_bytes(make_emlx(msg))
            files.append(p)

        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, files, "test", logger)
        vr = ame.verify_mbox(out, result.hashes, "test")
        return result, vr

    def test_single_message_roundtrip(self, tmp_path, logger):
        _, vr = self._export_and_verify(tmp_path, logger, [PLAIN_MSG])
        assert vr.verified_count == 1
        assert vr.mismatched == []
        assert vr.missing == []
        assert vr.extra == 0

    def test_multiple_messages_roundtrip(self, tmp_path, logger):
        _, vr = self._export_and_verify(tmp_path, logger, [PLAIN_MSG, HTML_MSG, MSG_WITH_FROM])
        assert vr.verified_count == 3
        assert vr.mismatched == []
        assert vr.missing == []
        assert vr.extra == 0

    def test_from_lines_roundtrip(self, tmp_path, logger):
        """Messages with From in the body should roundtrip perfectly."""
        _, vr = self._export_and_verify(tmp_path, logger, [MSG_WITH_FROM])
        assert vr.verified_count == 1
        assert vr.mismatched == []

    def test_tampered_mbox_detects_mismatch(self, tmp_path, logger):
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [p], "test", logger)

        # Tamper with the mbox file
        content = out.read_bytes()
        tampered = content.replace(b"Hello world", b"Goodbye world")
        out.write_bytes(tampered)

        vr = ame.verify_mbox(out, result.hashes, "test")
        assert len(vr.mismatched) == 1

    def test_missing_message_in_mbox(self, tmp_path, logger):
        """If mbox has fewer messages than expected, missing should be reported."""
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [p], "test", logger)

        # Add a fake expected hash that doesn't exist in the file
        hashes = dict(result.hashes)
        hashes["99.emlx"] = "deadbeef" * 8
        vr = ame.verify_mbox(out, hashes, "test")
        assert "99.emlx" in vr.missing

    def test_extra_message_in_mbox(self, tmp_path, logger):
        """If mbox has more messages than expected, extra should be counted."""
        # Write two messages
        for i, msg in enumerate([PLAIN_MSG, HTML_MSG], 1):
            (tmp_path / f"{i}.emlx").write_bytes(make_emlx(msg))
        out = tmp_path / "test.mbox"
        files = sorted(tmp_path.glob("*.emlx"))
        result = ame.write_mbox(out, files, "test", logger)

        # Only pass hashes for the first message
        one_hash = {list(result.hashes.keys())[0]: list(result.hashes.values())[0]}
        vr = ame.verify_mbox(out, one_hash, "test")
        assert vr.extra == 1


# ===================================================================
# Reporter / format helpers tests
# ===================================================================


class TestFormatHelpers:
    def test_format_duration_seconds_only(self):
        assert ame._format_duration(45) == "45s"

    def test_format_duration_minutes(self):
        assert ame._format_duration(125) == "2m 05s"

    def test_format_duration_zero(self):
        assert ame._format_duration(0) == "0s"

    def test_format_bytes_bytes(self):
        assert ame._format_bytes(500) == "500 B"

    def test_format_bytes_kb(self):
        assert ame._format_bytes(1500) == "1.5 KB"

    def test_format_bytes_mb(self):
        assert ame._format_bytes(2_500_000) == "2.5 MB"

    def test_format_bytes_gb(self):
        assert ame._format_bytes(10_000_000_000) == "10.0 GB"


class TestVerificationReport:
    def test_report_structure(self, tmp_path):
        er = ame.ExportResult(
            mailbox_name="INBOX",
            output_path=tmp_path / "INBOX.mbox",
            messages_written=10,
            messages_failed=1,
            failed_paths=[Path("/fake/11.emlx")],
            hashes={"1.emlx": "abc123"},
            partial_count=2,
            bytes_written=5000,
        )
        vr = ame.VerificationResult(
            mailbox_name="INBOX",
            expected_count=10,
            verified_count=10,
            mismatched=[],
            missing=[],
            extra=0,
        )

        ame.write_verification_report(tmp_path, [er], [vr], Path("/src"), 1.5)

        report_path = tmp_path / "verification-report.json"
        assert report_path.exists()
        data = json.loads(report_path.read_text())

        assert data["tool_version"] == ame.__version__
        assert "timestamp" in data
        assert data["source_dir"] == "/src"
        assert len(data["mailboxes"]) == 1

        mb = data["mailboxes"][0]
        assert mb["name"] == "INBOX"
        assert mb["messages_found"] == 11
        assert mb["messages_exported"] == 10
        assert mb["partial_messages"] == 2
        assert mb["sha256_verified"] is True

        totals = data["totals"]
        assert totals["mailboxes"] == 1
        assert totals["failures"] == 1
        assert totals["duration_seconds"] == 1.5


# ===================================================================
# Sanitize name tests
# ===================================================================


class TestSanitizeName:
    def test_safe_name_unchanged(self):
        assert ame._sanitize_name("INBOX") == "INBOX"

    def test_slashes_preserved(self):
        """Forward slashes are directory separators, not unsafe."""
        assert ame._sanitize_name("Work/Projects") == "Work/Projects"

    def test_unsafe_chars_replaced(self):
        assert ame._sanitize_name('My:Box<1>') == "My_Box_1_"

    def test_null_byte_replaced(self):
        assert ame._sanitize_name("test\x00name") == "test_name"


# ===================================================================
# Emlx sort key tests
# ===================================================================


class TestEmlxSortKey:
    def test_numeric_sort(self):
        names = ["10.emlx", "2.emlx", "1.emlx", "100.emlx"]
        result = sorted(names, key=ame._emlx_sort_key)
        assert result == ["1.emlx", "2.emlx", "10.emlx", "100.emlx"]

    def test_partial_sorts_after_regular(self):
        names = ["1.partial.emlx", "1.emlx"]
        result = sorted(names, key=ame._emlx_sort_key)
        assert result == ["1.emlx", "1.partial.emlx"]

    def test_non_numeric_sorts_last(self):
        names = ["1.emlx", "abc.emlx", "2.emlx"]
        result = sorted(names, key=ame._emlx_sort_key)
        assert result == ["1.emlx", "2.emlx", "abc.emlx"]


# ===================================================================
# Logger tests
# ===================================================================


class TestLogger:
    def test_quiet_suppresses_info(self, capsys):
        log = ame.Logger(quiet=True)
        log.info("should not appear")
        assert capsys.readouterr().out == ""

    def test_info_prints(self, capsys):
        log = ame.Logger(quiet=False)
        log.info("hello")
        assert "hello" in capsys.readouterr().out

    def test_verbose_debug(self, capsys):
        log = ame.Logger(verbose=True)
        log.debug("detail")
        assert "detail" in capsys.readouterr().err

    def test_non_verbose_no_debug(self, capsys):
        log = ame.Logger(verbose=False)
        log.debug("detail")
        assert capsys.readouterr().err == ""

    def test_warn_always_prints(self, capsys):
        log = ame.Logger(quiet=True)
        log.warn("warning!")
        assert "warning!" in capsys.readouterr().err

    def test_log_file_written(self, tmp_path):
        log_path = tmp_path / "test.log"
        with open(log_path, "w") as f:
            log = ame.Logger(log_file=f)
            log.info("logged")
        content = log_path.read_text()
        assert "logged" in content
        # Should have timestamp
        assert "[" in content


# ===================================================================
# CLI integration tests
# ===================================================================


class TestCLI:
    def test_self_test_via_main(self):
        exit_code = ame.main(["--self-test"])
        assert exit_code == ame.EXIT_SUCCESS

    def test_default_lists_mailboxes(self, mail_tree):
        mail_root, output_dir = mail_tree
        exit_code = ame.main([
            "--mail-dir", str(mail_root),
            "--quiet",
        ])
        assert exit_code == ame.EXIT_SUCCESS
        # List mode should not create .mbox files.
        assert list(output_dir.glob("*.mbox")) == []

    def test_full_export(self, mail_tree):
        mail_root, output_dir = mail_tree
        exit_code = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
        ])
        # Exit code 1 (partial) because of one corrupt file.
        assert exit_code == ame.EXIT_PARTIAL
        mbox_files = list(output_dir.glob("**/*.mbox"))
        assert len(mbox_files) >= 2
        # Export implies verify unless --no-verify is set.
        assert (output_dir / "verification-report.json").exists()

    def test_full_export_no_verify(self, mail_tree):
        mail_root, output_dir = mail_tree
        exit_code = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--no-verify",
            "--quiet",
        ])
        assert exit_code == ame.EXIT_PARTIAL
        assert not (output_dir / "verification-report.json").exists()

    def test_verify_existing_exports(self, mail_tree):
        mail_root, output_dir = mail_tree
        first = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--no-verify",
            "--quiet",
        ])
        assert first == ame.EXIT_PARTIAL

        exit_code = ame.main([
            "--verify",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
        ])
        assert exit_code == ame.EXIT_PARTIAL
        assert (output_dir / "verification-report.json").exists()

    def test_no_mailboxes_found(self, tmp_path):
        empty = tmp_path / "empty_mail"
        empty.mkdir()
        exit_code = ame.main([
            "--mail-dir", str(empty),
            "--quiet",
        ])
        assert exit_code == ame.EXIT_FATAL

    def test_glob_argument_filter(self, mail_tree):
        mail_root, output_dir = mail_tree
        exit_code = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
            "Work/*",
        ])
        assert exit_code == ame.EXIT_SUCCESS
        mbox_files = list(output_dir.glob("**/*.mbox"))
        assert len(mbox_files) == 1

    def test_existing_mbox_files_are_overwritten(self, mail_tree):
        mail_root, output_dir = mail_tree
        inbox_out = output_dir / "INBOX.mbox"
        inbox_out.write_bytes(b"fake")

        exit_code = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
        ])
        assert exit_code in (ame.EXIT_SUCCESS, ame.EXIT_PARTIAL)
        assert inbox_out.read_bytes() != b"fake"

    def test_export_log_created(self, mail_tree):
        mail_root, output_dir = mail_tree
        ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
        ])
        assert (output_dir / "export-log.txt").exists()

    def test_nonexistent_output_dir_created(self, mail_tree):
        mail_root, output_dir = mail_tree
        new_out = output_dir / "nested" / "deep"
        ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(new_out),
            "--quiet",
        ])
        assert new_out.exists()


# ===================================================================
# End-to-end pipeline test
# ===================================================================


class TestEndToEnd:
    def test_full_pipeline_with_all_message_types(self, tmp_path, logger):
        """Full pipeline: scan -> export -> verify with varied messages."""
        # Build mail structure
        acct = tmp_path / "V10" / "UUID-E2E"
        msgs = acct / "TestBox.mbox" / "Messages"
        msgs.mkdir(parents=True)

        # Message with From in body (escaping test)
        (msgs / "1.emlx").write_bytes(make_emlx(MSG_WITH_FROM))

        # Message with multipart MIME
        mime_msg = (
            b"From: sender@test.com\r\n"
            b"Date: Thu, 01 Jan 2026 00:00:00 +0000\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=\"B\"\r\n"
            b"\r\n"
            b"--B\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"text part\r\n"
            b"--B\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Transfer-Encoding: base64\r\n"
            b"\r\n"
            b"AQIDBA==\r\n"
            b"--B--\r\n"
        )
        (msgs / "2.emlx").write_bytes(make_emlx(mime_msg))

        # Message with no trailing newline
        no_newline = b"From: a@b.com\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\nno trailing newline"
        (msgs / "3.emlx").write_bytes(make_emlx(no_newline))

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Scan
        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 1
        assert mailboxes[0].name == "TestBox"
        assert mailboxes[0].message_count == 3

        # Export
        out_mbox = output_dir / "TestBox.mbox"
        result = ame.write_mbox(out_mbox, mailboxes[0].emlx_files, "TestBox", logger)
        assert result.messages_written == 3
        assert result.messages_failed == 0

        # Verify
        vr = ame.verify_mbox(out_mbox, result.hashes, "TestBox")
        assert vr.verified_count == 3
        assert vr.mismatched == []
        assert vr.missing == []
        assert vr.extra == 0
