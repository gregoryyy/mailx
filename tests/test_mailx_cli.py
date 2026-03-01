from __future__ import annotations

from tests_support import ame


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
        assert list(output_dir.glob("*.mbox")) == []

    def test_full_export(self, mail_tree):
        mail_root, output_dir = mail_tree
        exit_code = ame.main([
            "--export",
            "--mail-dir", str(mail_root),
            "--output-dir", str(output_dir),
            "--quiet",
        ])
        assert exit_code == ame.EXIT_PARTIAL
        assert len(list(output_dir.glob("**/*.mbox"))) >= 2
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
        assert len(list(output_dir.glob("**/*.mbox"))) == 1

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
