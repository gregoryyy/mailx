from __future__ import annotations

from tests_support import PLAIN_MSG, ame, make_emlx


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
        assert by_name["INBOX"].message_count == 5
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
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "INBOX", logger)
        inbox = mailboxes[0]
        nums = [int(p.name.split(".")[0]) for p in inbox.emlx_files]
        assert nums == sorted(nums)

    def test_skips_maildata(self, tmp_path, logger):
        v10 = tmp_path / "V10"
        maildata = v10 / "MailData"
        maildata.mkdir(parents=True)
        fake = maildata / "Fake.mbox" / "Messages"
        fake.mkdir(parents=True)
        (fake / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))

        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 0

    def test_duplicate_names_across_accounts(self, tmp_path, logger):
        for uuid in ("AAAA-1111", "BBBB-2222"):
            msgs = tmp_path / "V10" / uuid / "INBOX.mbox" / "Messages"
            msgs.mkdir(parents=True)
            (msgs / "1.emlx").write_bytes(make_emlx(PLAIN_MSG))

        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        names = [mb.name for mb in mailboxes]
        assert len(names) == 2
        assert all("INBOX" in n for n in names)
        assert names[0] != names[1]

    def test_account_id_populated(self, mail_tree, logger):
        mail_root, _ = mail_tree
        mailboxes = ame.scan_mailboxes(mail_root, "*", logger)
        for mb in mailboxes:
            assert mb.account_id == "ACCT-UUID"

    def test_skips_empty_mailbox(self, tmp_path, logger):
        msgs = tmp_path / "V10" / "UUID" / "Empty.mbox" / "Messages"
        msgs.mkdir(parents=True)
        mailboxes = ame.scan_mailboxes(tmp_path, "*", logger)
        assert len(mailboxes) == 0


class TestSanitizeName:
    def test_safe_name_unchanged(self):
        assert ame._sanitize_name("INBOX") == "INBOX"

    def test_slashes_preserved(self):
        assert ame._sanitize_name("Work/Projects") == "Work/Projects"

    def test_unsafe_chars_replaced(self):
        assert ame._sanitize_name('My:Box<1>') == "My_Box_1_"

    def test_null_byte_replaced(self):
        assert ame._sanitize_name("test\x00name") == "test_name"


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
