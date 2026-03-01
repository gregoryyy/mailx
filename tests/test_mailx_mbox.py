from __future__ import annotations

import hashlib

from tests_support import HTML_MSG, MSG_WITH_FROM, PLAIN_MSG, ame, make_emlx


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
        assert out.read_bytes().startswith(b"From alice@example.com ")

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
        assert b"\n>From the desk" in content
        assert b"\n>>From here" in content
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

        assert out.read_bytes().endswith(b"\n\n")

    def test_empty_file_list(self, tmp_path, logger):
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [], "test", logger)

        assert result.messages_written == 0
        assert result.messages_failed == 0
        assert result.bytes_written == 0


class TestVerifier:
    def _export_and_verify(self, tmp_path, logger, messages):
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
        _, vr = self._export_and_verify(tmp_path, logger, [MSG_WITH_FROM])
        assert vr.verified_count == 1
        assert vr.mismatched == []

    def test_tampered_mbox_detects_mismatch(self, tmp_path, logger):
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [p], "test", logger)

        content = out.read_bytes()
        out.write_bytes(content.replace(b"Hello world", b"Goodbye world"))

        vr = ame.verify_mbox(out, result.hashes, "test")
        assert len(vr.mismatched) == 1

    def test_missing_message_in_mbox(self, tmp_path, logger):
        p = tmp_path / "1.emlx"
        p.write_bytes(make_emlx(PLAIN_MSG))
        out = tmp_path / "test.mbox"
        result = ame.write_mbox(out, [p], "test", logger)

        hashes = dict(result.hashes)
        hashes["99.emlx"] = "deadbeef" * 8
        vr = ame.verify_mbox(out, hashes, "test")
        assert "99.emlx" in vr.missing

    def test_extra_message_in_mbox(self, tmp_path, logger):
        for i, msg in enumerate([PLAIN_MSG, HTML_MSG], 1):
            (tmp_path / f"{i}.emlx").write_bytes(make_emlx(msg))
        out = tmp_path / "test.mbox"
        files = sorted(tmp_path.glob("*.emlx"))
        result = ame.write_mbox(out, files, "test", logger)

        one_hash = {list(result.hashes.keys())[0]: list(result.hashes.values())[0]}
        vr = ame.verify_mbox(out, one_hash, "test")
        assert vr.extra == 1
