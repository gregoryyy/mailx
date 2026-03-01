#!/usr/bin/env python3
"""apple-mail-export: Export Apple Mail .emlx files to standard .mbox format.

Reads directly from Apple Mail's on-disk .emlx storage, bypassing Mail.app
entirely. Designed for power users migrating or backing up large mailboxes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Optional

from mailx.emlx import (
    _escape_from_lines,
    _extract_from_and_date,
    _make_emlx,
    _unescape_from_lines,
    parse_emlx,
)
from mailx.logger import Logger
from mailx.mbox import build_expected_hashes, verify_mbox, write_mbox
from mailx.model import (
    DEFAULT_MAIL_DIR,
    DEFAULT_OUTPUT_DIR,
    EXIT_FATAL,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    ExportResult,
    FALLBACK_DATE,
    FALLBACK_SENDER,
    VerificationResult,
    __version__,
)
from mailx.report import (
    _display_width,
    _format_bytes,
    _format_duration,
    _pad_display,
    _truncate_display,
    print_summary,
    write_verification_report,
)
from mailx.scan import _emlx_sort_key, _output_path_for_mailbox, _sanitize_name, scan_mailboxes

# Global interrupt flag for SIGINT handling
_interrupted = False


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def run_self_test(logger: Logger) -> int:
    """Run self-test with synthetic .emlx data. Returns exit code."""
    logger.info("Running self-test...")

    tmpdir = Path(tempfile.mkdtemp(prefix="apple-mail-export-test-"))
    try:
        return _run_self_test_inner(tmpdir, logger)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_self_test_inner(tmpdir: Path, logger: Logger) -> int:
    """Inner self-test logic. Returns exit code."""
    assertions_passed = 0
    assertions_failed = 0

    def assert_eq(label: str, actual, expected) -> None:
        nonlocal assertions_passed, assertions_failed
        if actual == expected:
            assertions_passed += 1
        else:
            assertions_failed += 1
            logger.error(f"ASSERTION FAILED: {label}")
            logger.error(f"  expected: {expected!r}")
            logger.error(f"  actual:   {actual!r}")

    def assert_true(label: str, value: bool) -> None:
        nonlocal assertions_passed, assertions_failed
        if value:
            assertions_passed += 1
        else:
            assertions_failed += 1
            logger.error(f"ASSERTION FAILED: {label}")

    # --- Build synthetic mail directory ---
    mail_dir = tmpdir / "V10"
    account_dir = mail_dir / "TEST-UUID-001"

    # INBOX mailbox
    inbox_dir = account_dir / "INBOX.mbox"
    inbox_msgs = inbox_dir / "Messages"
    inbox_msgs.mkdir(parents=True)

    # Message 1: plain text with From in body (tests escaping round-trip)
    msg1 = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Date: Thu, 01 Jan 2026 12:00:00 +0000\r\n"
        b"Subject: Plain text test\r\n"
        b"\r\n"
        b"Hello, this is a plain text message.\r\n"
        b"From the desk of Alice.\r\n"
        b">From here too.\r\n"
    )
    (inbox_msgs / "1.emlx").write_bytes(_make_emlx(msg1))

    # Message 2: HTML
    msg2 = (
        b"From: bob@example.com\r\n"
        b"To: alice@example.com\r\n"
        b"Date: Fri, 02 Jan 2026 13:00:00 +0000\r\n"
        b"Subject: HTML test\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b"<html><body><h1>Hello</h1></body></html>\r\n"
    )
    (inbox_msgs / "2.emlx").write_bytes(_make_emlx(msg2))

    # Message 3: MIME multipart with base64 PNG attachment
    msg3 = (
        b"From: carol@example.com\r\n"
        b"To: alice@example.com\r\n"
        b"Date: Sat, 03 Jan 2026 14:00:00 +0000\r\n"
        b"Subject: Attachment test\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/mixed; boundary=\"BOUNDARY\"\r\n"
        b"\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: text/plain\r\n"
        b"\r\n"
        b"See attached.\r\n"
        b"--BOUNDARY\r\n"
        b"Content-Type: image/png; name=\"pixel.png\"\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n"
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE\r\n"
        b"QVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==\r\n"
        b"--BOUNDARY--\r\n"
    )
    (inbox_msgs / "3.emlx").write_bytes(_make_emlx(msg3))

    # Message 4: multiple recipients
    msg4 = (
        b"From: dave@example.com\r\n"
        b"To: alice@example.com, bob@example.com\r\n"
        b"Cc: carol@example.com\r\n"
        b"Date: Sun, 04 Jan 2026 15:00:00 +0000\r\n"
        b"Subject: Multiple recipients\r\n"
        b"\r\n"
        b"Message to multiple people.\r\n"
    )
    (inbox_msgs / "4.emlx").write_bytes(_make_emlx(msg4))

    # Message 5: partial (valid but flagged)
    msg5 = (
        b"From: eve@example.com\r\n"
        b"To: alice@example.com\r\n"
        b"Date: Mon, 05 Jan 2026 16:00:00 +0000\r\n"
        b"Subject: Partial download\r\n"
        b"\r\n"
        b"This message was only partially downloaded.\r\n"
    )
    (inbox_msgs / "5.partial.emlx").write_bytes(_make_emlx(msg5))

    # Message 6: corrupt (wrong byte count)
    corrupt_body = b"This is some content"
    corrupt_emlx = b"9999\n" + corrupt_body
    (inbox_msgs / "6.emlx").write_bytes(corrupt_emlx)

    # Nested mailbox: Work/Projects
    nested_dir = account_dir / "Work.mbox" / "Projects.mbox"
    nested_msgs = nested_dir / "Messages"
    nested_msgs.mkdir(parents=True)

    msg100 = (
        b"From: =?UTF-8?Q?M=C3=BCller?= <mueller@example.com>\r\n"
        b"To: alice@example.com\r\n"
        b"Date: Tue, 06 Jan 2026 17:00:00 +0000\r\n"
        b"Subject: =?UTF-8?Q?=C3=9Cberpr=C3=BCfung?=\r\n"
        b"\r\n"
        b"UTF-8 encoded subject test.\r\n"
    )
    (nested_msgs / "100.emlx").write_bytes(_make_emlx(msg100))

    # --- Run pipeline ---
    output_dir = tmpdir / "output"
    output_dir.mkdir()

    # 1. Scan
    mailboxes = scan_mailboxes(tmpdir, "*", logger)
    assert_eq("mailbox count", len(mailboxes), 2)

    mbox_by_name = {mb.name: mb for mb in mailboxes}
    assert_true("INBOX found", "INBOX" in mbox_by_name)
    assert_true("Work/Projects found", "Work/Projects" in mbox_by_name)

    inbox = mbox_by_name.get("INBOX")
    nested = mbox_by_name.get("Work/Projects")

    if inbox:
        assert_eq("INBOX message count", inbox.message_count, 6)
    if nested:
        assert_eq("Work/Projects message count", nested.message_count, 1)

    # 2. Export
    export_results: list[ExportResult] = []
    verification_results: list[VerificationResult] = []

    for mb in mailboxes:
        mbox_out = _output_path_for_mailbox(output_dir, mb.name)
        mbox_out.parent.mkdir(parents=True, exist_ok=True)
        result = write_mbox(mbox_out, mb.emlx_files, mb.name, logger)
        export_results.append(result)

    inbox_result = next((r for r in export_results if r.mailbox_name == "INBOX"), None)
    nested_result = next(
        (r for r in export_results if r.mailbox_name == "Work/Projects"), None
    )

    if inbox_result:
        assert_eq("INBOX written", inbox_result.messages_written, 5)
        assert_eq("INBOX failed", inbox_result.messages_failed, 1)
        assert_eq("INBOX partial", inbox_result.partial_count, 1)
        assert_true(
            "corrupt file in failures",
            any(p.name == "6.emlx" for p in inbox_result.failed_paths),
        )

    if nested_result:
        assert_eq("Work/Projects written", nested_result.messages_written, 1)
        assert_eq("Work/Projects failed", nested_result.messages_failed, 0)

    # 3. Verify
    for result in export_results:
        vr = verify_mbox(result.output_path, result.hashes, result.mailbox_name)
        verification_results.append(vr)

    inbox_vr = next(
        (vr for vr in verification_results if vr.mailbox_name == "INBOX"), None
    )
    nested_vr = next(
        (vr for vr in verification_results if vr.mailbox_name == "Work/Projects"), None
    )

    if inbox_vr:
        assert_eq("INBOX verified count", inbox_vr.verified_count, 5)
        assert_eq("INBOX mismatched", len(inbox_vr.mismatched), 0)
        assert_eq("INBOX missing", len(inbox_vr.missing), 0)
        assert_eq("INBOX extra", inbox_vr.extra, 0)

    if nested_vr:
        assert_eq("Work/Projects verified count", nested_vr.verified_count, 1)
        assert_eq("Work/Projects mismatched", len(nested_vr.mismatched), 0)

    # 4. Check output files exist
    assert_true("INBOX.mbox exists", (output_dir / "INBOX.mbox").exists())
    assert_true(
        "Work/Projects.mbox exists",
        (output_dir / "Work/Projects.mbox").exists()
        or (output_dir / "Work_Projects.mbox").exists(),
    )

    # 5. Verify report generation
    write_verification_report(output_dir, export_results, verification_results, tmpdir, 0.1)
    report_path = output_dir / "verification-report.json"
    assert_true("verification-report.json exists", report_path.exists())
    if report_path.exists():
        with open(report_path) as f:
            report_data = json.load(f)
        assert_eq("report version", report_data["tool_version"], __version__)
        assert_eq("report mailbox count", len(report_data["mailboxes"]), 2)

    total = assertions_passed + assertions_failed
    if assertions_failed == 0:
        logger.info(f"Self-test PASSED: {assertions_passed}/{total} assertions OK")
        return EXIT_SUCCESS

    logger.error(f"Self-test FAILED: {assertions_failed}/{total} assertions failed")
    return EXIT_FATAL


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class MailExportArgumentParser(argparse.ArgumentParser):
    def print_help(self, file=None) -> None:
        super().print_help(file=file)
        stream = file if file is not None else sys.stdout
        stream.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = MailExportArgumentParser(
        prog="apple-mail-export",
        usage="apple-mail-export [OPTIONS] [GLOB]",
        description=(
            "Export Apple Mail .emlx files to standard .mbox format.\n\n"
            "Synopsis:\n"
            "  apple-mail-export [OPTIONS] [GLOB]"
        ),
        epilog=(
            "Typical workflow:\n"
            "  1) apple-mail-export [--list]\n"
            "  2) apple-mail-export --export [GLOB]\n\n"
            "Mailbox glob syntax (fnmatch):\n"
            "  *       match any characters\n"
            "  ?       match one character\n"
            "  [abc]   match one char in set\n"
            "  [!abc]  match one char not in set\n\n"
            "Examples:\n"
            "  apple-mail-export --list \"INBOX/*\"\n"
            "  apple-mail-export --export \"*Sent*\"\n"
            "  apple-mail-export --verify \"[Gg]mail/*\"\n\n"
            "Tip:\n"
            "  Quote glob patterns so your shell does not expand them first.\n\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "glob",
        nargs="?",
        default="*",
        metavar="GLOB",
        help='Mailbox glob filter (default: "*")',
    )
    parser.add_argument(
        "--mail-dir",
        type=Path,
        default=DEFAULT_MAIL_DIR,
        help="Apple Mail data directory (default: ~/Library/Mail)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for .mbox files (default: ./mail-export/)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List matching mailboxes and exit (default action)",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export matching mailbox(es)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing or newly exported mailbox(es)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="With --export, skip post-export verification",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print summary and errors",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug-level detail (file paths, timing)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test with synthetic data and exit",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    global _interrupted

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose are mutually exclusive")
    if args.no_verify and not args.export:
        parser.error("--no-verify is only valid with --export")

    if args.self_test:
        logger = Logger(quiet=False, verbose=True)
        return run_self_test(logger)

    glob_pattern: str = args.glob
    mail_dir: Path = args.mail_dir.resolve()
    output_dir: Path = args.output_dir.resolve()

    action_list = args.list or (not args.export and not args.verify)
    action_export = args.export
    action_verify = args.verify or (args.export and not args.no_verify)

    if action_list and (action_export or args.verify):
        parser.error("--list cannot be combined with --export or --verify")

    log_fh: Optional[IO] = None
    if action_export:
        output_dir.mkdir(parents=True, exist_ok=True)
        log_fh = open(output_dir / "export-log.txt", "w")
    elif action_verify:
        if not output_dir.exists():
            print(f"ERROR: Output directory not found: {output_dir}", file=sys.stderr)
            return EXIT_FATAL
        log_fh = open(output_dir / "export-log.txt", "a")

    logger = Logger(quiet=args.quiet, verbose=args.verbose, log_file=log_fh)

    def sigint_handler(signum, frame):
        del signum, frame
        global _interrupted
        _interrupted = True

    prev_handler = signal.signal(signal.SIGINT, sigint_handler)

    exit_code = EXIT_SUCCESS
    start_time = time.monotonic()

    try:
        logger.info(f"Scanning {mail_dir}/ ...")
        mailboxes = scan_mailboxes(mail_dir, glob_pattern, logger)

        if not mailboxes:
            if glob_pattern != "*":
                logger.error(f"No mailboxes match pattern '{glob_pattern}'.")
            else:
                logger.error(f"No mailboxes found. Check --mail-dir path: {mail_dir}")
            return EXIT_FATAL

        total_messages = sum(mb.message_count for mb in mailboxes)
        total_size = sum(sum(f.stat().st_size for f in mb.emlx_files) for mb in mailboxes)
        logger.info(
            f"Found {len(mailboxes)} mailboxes, "
            f"{total_messages:,} messages ({_format_bytes(total_size)})"
        )

        if action_list:
            logger.info("")
            logger.info("MAILBOXES")
            logger.info("")
            max_name_width = max((_display_width(mb.name) for mb in mailboxes), default=0)
            name_col_width = max(30, min(52, max_name_width))
            for mb in mailboxes:
                mb_size = sum(f.stat().st_size for f in mb.emlx_files)
                name_col = _pad_display(_truncate_display(mb.name, name_col_width), name_col_width)
                logger.info(
                    f"  {name_col}  {mb.message_count:>8,} messages  "
                    f"({_format_bytes(mb_size)})"
                )
            return EXIT_SUCCESS

        export_results: list[ExportResult] = []
        verification_results: list[VerificationResult] = []

        if action_export:
            logger.info("")
            logger.info("Exporting mailboxes:")

            for mb in mailboxes:
                if _interrupted:
                    break

                mbox_out = _output_path_for_mailbox(output_dir, mb.name)
                mbox_out.parent.mkdir(parents=True, exist_ok=True)

                export_start = time.monotonic()

                def make_progress_cb(mb_name: str, t0: float):
                    def cb(current: int, total: int):
                        elapsed = max(time.monotonic() - t0, 0.001)
                        rate = current / elapsed
                        logger.progress(mb_name, current, total, rate)

                    return cb

                result = write_mbox(
                    mbox_out,
                    mb.emlx_files,
                    mb.name,
                    logger,
                    progress_callback=make_progress_cb(mb.name, export_start),
                    should_stop=lambda: _interrupted,
                )
                export_results.append(result)

                if result.messages_failed > 0:
                    exit_code = EXIT_PARTIAL

        if action_verify and not _interrupted:
            logger.info("")
            logger.info("Verifying exports...")

            if not action_export:
                for mb in mailboxes:
                    mbox_out = _output_path_for_mailbox(output_dir, mb.name)
                    hashes, failed, failed_paths, partial_count = build_expected_hashes(
                        mb.emlx_files, logger
                    )
                    if failed > 0:
                        exit_code = EXIT_PARTIAL
                    export_results.append(
                        ExportResult(
                            mailbox_name=mb.name,
                            output_path=mbox_out,
                            messages_written=len(hashes),
                            messages_failed=failed,
                            failed_paths=failed_paths,
                            hashes=hashes,
                            partial_count=partial_count,
                            bytes_written=mbox_out.stat().st_size if mbox_out.exists() else 0,
                        )
                    )

            for result in export_results:
                if not result.output_path.exists():
                    exit_code = EXIT_PARTIAL
                    logger.info(
                        f"  {result.mailbox_name:<22s}  ✗  missing output mbox "
                        f"({result.output_path})"
                    )
                    verification_results.append(
                        VerificationResult(
                            mailbox_name=result.mailbox_name,
                            expected_count=len(result.hashes),
                            verified_count=0,
                            mismatched=[],
                            missing=list(result.hashes.keys()),
                            extra=0,
                        )
                    )
                    continue

                vr = verify_mbox(result.output_path, result.hashes, result.mailbox_name)
                verification_results.append(vr)

                if not vr.mismatched and not vr.missing and vr.extra == 0:
                    logger.info(
                        f"  {result.mailbox_name:<22s}  ✓  "
                        f"{vr.verified_count:,} messages, SHA-256 verified"
                    )
                else:
                    exit_code = EXIT_PARTIAL
                    issues = []
                    if vr.mismatched:
                        issues.append(f"{len(vr.mismatched)} hash mismatches")
                    if vr.missing:
                        issues.append(f"{len(vr.missing)} missing")
                    if vr.extra:
                        issues.append(f"{vr.extra} extra")
                    logger.info(f"  {result.mailbox_name:<22s}  ✗  {', '.join(issues)}")
                    for name in vr.mismatched + vr.missing:
                        logger.info(f"                            → {name}")

            duration = time.monotonic() - start_time
            write_verification_report(output_dir, export_results, verification_results, mail_dir, duration)

        duration = time.monotonic() - start_time
        print_summary(
            mail_dir,
            output_dir,
            export_results,
            verification_results if action_verify else None,
            duration,
            logger,
        )

        if _interrupted:
            logger.warn("Export was interrupted. Results are partial.")
            exit_code = EXIT_FATAL

        return exit_code

    finally:
        signal.signal(signal.SIGINT, prev_handler)
        if log_fh:
            log_fh.close()


if __name__ == "__main__":
    sys.exit(main())
