from __future__ import annotations

from tests_support import MSG_WITH_FROM, ame, make_emlx


class TestEndToEnd:
    def test_full_pipeline_with_all_message_types(self, tmp_path, logger):
        acct = tmp_path / "V10" / "UUID-E2E"
        msgs = acct / "TestBox.mbox" / "Messages"
        msgs.mkdir(parents=True)

        (msgs / "1.emlx").write_bytes(make_emlx(MSG_WITH_FROM))

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

        no_newline = b"From: a@b.com\r\nDate: Thu, 01 Jan 2026 00:00:00 +0000\r\n\r\nno trailing newline"
        (msgs / "3.emlx").write_bytes(make_emlx(no_newline))

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 1
        assert mailboxes[0].name == "TestBox"
        assert mailboxes[0].message_count == 3

        out_mbox = output_dir / "TestBox.mbox"
        result = ame.write_mbox(out_mbox, mailboxes[0].emlx_files, "TestBox", logger)
        assert result.messages_written == 3
        assert result.messages_failed == 0

        vr = ame.verify_mbox(out_mbox, result.hashes, "TestBox")
        assert vr.verified_count == 3
        assert vr.mismatched == []
        assert vr.missing == []
        assert vr.extra == 0
