from __future__ import annotations

from tests_support import PLAIN_MSG, ame, make_emlx


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
        body, _ = ame.parse_emlx(p, logger)
        assert body is None

    def test_empty_file(self, tmp_path, logger):
        p = tmp_path / "empty.emlx"
        p.write_bytes(b"")
        body, _ = ame.parse_emlx(p, logger)
        assert body is None

    def test_negative_byte_count(self, tmp_path, logger):
        p = tmp_path / "neg.emlx"
        p.write_bytes(b"-5\ncontent")
        body, _ = ame.parse_emlx(p, logger)
        assert body is None

    def test_zero_byte_count(self, tmp_path, logger):
        p = tmp_path / "zero.emlx"
        p.write_bytes(b"0\ntrailing plist xml")
        body, is_partial = ame.parse_emlx(p, logger)
        assert body == b""
        assert is_partial is False

    def test_file_not_found(self, tmp_path, logger):
        p = tmp_path / "nonexistent.emlx"
        body, _ = ame.parse_emlx(p, logger)
        assert body is None

    def test_with_trailing_plist(self, tmp_path, logger):
        plist = b'<?xml version="1.0"?><plist><dict></dict></plist>'
        emlx = make_emlx(PLAIN_MSG) + plist
        p = tmp_path / "plist.emlx"
        p.write_bytes(emlx)
        body, _ = ame.parse_emlx(p, logger)
        assert body == PLAIN_MSG

    def test_no_logger(self, tmp_path):
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        body, _ = ame.parse_emlx(p)
        assert body == PLAIN_MSG


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
        assert ame._unescape_from_lines(b"From foo\n") == b"From foo\n"

    def test_roundtrip(self):
        original = b"Hello\nFrom Alice\n>From Bob\n>>From Carol\nBye\n"
        escaped = ame._escape_from_lines(original)
        recovered = ame._unescape_from_lines(escaped)
        assert recovered == original

    def test_roundtrip_empty(self):
        assert ame._unescape_from_lines(ame._escape_from_lines(b"")) == b""

    def test_roundtrip_no_from(self):
        data = b"Just\nsome\ntext\n"
        assert ame._unescape_from_lines(ame._escape_from_lines(data)) == data


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
        sender, date = ame._extract_from_and_date(b"just body content\r\n")
        assert sender == ame.FALLBACK_SENDER
        assert date == ame.FALLBACK_DATE

    def test_folded_from_header(self):
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
        msg = b"From: alice@test.com\nDate: Thu, 01 Jan 2026 00:00:00 +0000\n\nbody\n"
        sender, date = ame._extract_from_and_date(msg)
        assert sender == "alice@test.com"
        assert "2026" in date

    def test_rfc2047_encoded_from(self):
        msg = b"From: =?UTF-8?Q?M=C3=BCller?= <mueller@example.com>\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\n"
        sender, _ = ame._extract_from_and_date(msg)
        assert sender == "mueller@example.com"

    def test_empty_message(self):
        sender, date = ame._extract_from_and_date(b"")
        assert sender == ame.FALLBACK_SENDER
        assert date == ame.FALLBACK_DATE
