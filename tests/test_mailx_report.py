from __future__ import annotations

import json
from pathlib import Path

from tests_support import ame


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
