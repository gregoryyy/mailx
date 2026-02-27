#!/usr/bin/env python3
"""apple-mail-export: Export Apple Mail .emlx files to standard .mbox format.

Reads directly from Apple Mail's on-disk .emlx storage, bypassing Mail.app
entirely. Designed for power users migrating or backing up large mailboxes.
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import errno
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Callable, Optional

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAIL_DIR = Path.home() / "Library" / "Mail"
DEFAULT_OUTPUT_DIR = Path("./mail-export")

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2
EXIT_BAD_ARGS = 3

# mboxrd From_ escaping — prepend > to any line matching ^>*From
FROM_ESCAPE_RE = re.compile(rb"^(>*From )", re.MULTILINE)
# mboxrd From_ unescaping — remove one leading >
FROM_UNESCAPE_RE = re.compile(rb"^>(>*From )", re.MULTILINE)
# Numeric prefix in .emlx filenames for sorting
EMLX_NUM_RE = re.compile(r"^(\d+)")

FALLBACK_SENDER = "MAILER-DAEMON"
FALLBACK_DATE = "Thu Jan  1 00:00:00 1970"

# Characters not safe in output filenames
UNSAFE_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MailboxInfo:
    name: str  # e.g., "INBOX", "Work/Projects"
    path: Path  # Absolute path to the .mbox directory
    emlx_files: list[Path]  # All .emlx and .partial.emlx files, sorted
    message_count: int  # len(emlx_files)
    account_id: str  # UUID of the parent account directory


@dataclass
class ExportResult:
    mailbox_name: str
    output_path: Path
    messages_written: int
    messages_failed: int
    failed_paths: list[Path]
    hashes: dict[str, str]  # {emlx_filename: sha256_hex}
    partial_count: int
    bytes_written: int


@dataclass
class VerificationResult:
    mailbox_name: str
    expected_count: int
    verified_count: int
    mismatched: list[str]  # emlx filenames with hash mismatch
    missing: list[str]  # emlx filenames not found in mbox
    extra: int  # messages in mbox not in expected set


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------


class Logger:
    def __init__(
        self,
        quiet: bool = False,
        verbose: bool = False,
        log_file: Optional[IO] = None,
    ):
        self.quiet = quiet
        self.verbose = verbose
        self.log_file = log_file
        self._term_width = shutil.get_terminal_size((80, 24)).columns

    def _ts(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def _log_to_file(self, msg: str) -> None:
        if self.log_file:
            self.log_file.write(f"[{self._ts()}] {msg}\n")
            self.log_file.flush()

    def info(self, msg: str) -> None:
        self._log_to_file(msg)
        if not self.quiet:
            print(msg)

    def warn(self, msg: str) -> None:
        self._log_to_file(f"WARNING: {msg}")
        print(f"WARNING: {msg}", file=sys.stderr)

    def error(self, msg: str) -> None:
        self._log_to_file(f"ERROR: {msg}")
        print(f"ERROR: {msg}", file=sys.stderr)

    def debug(self, msg: str) -> None:
        self._log_to_file(f"DEBUG: {msg}")
        if self.verbose:
            print(f"  DEBUG: {msg}", file=sys.stderr)

    def progress(self, name: str, current: int, total: int, rate: float) -> None:
        if self.quiet:
            return
        pct = current * 100 // total if total else 0
        count_str = f"{current:,}/{total:,}"
        rate_str = f"({rate:,.0f} msg/s)"
        pct_str = f"{pct:3d}%"

        name_width = 22
        truncated = name[:name_width].ljust(name_width)

        fixed = len(f"  {truncated}  []  {pct_str}  {count_str}  {rate_str}")
        bar_width = max(10, self._term_width - fixed)
        filled = int(bar_width * current / total) if total else 0
        bar = "#" * filled + "." * (bar_width - filled)

        line = f"  {truncated}  [{bar}]  {pct_str}  {count_str}  {rate_str}"
        sys.stderr.write(f"\r{line}")
        sys.stderr.flush()
        if current == total:
            sys.stderr.write("\n")


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def _emlx_sort_key(filename: str) -> tuple:
    """Sort key: numeric prefix first, then full name for ties."""
    m = EMLX_NUM_RE.match(filename)
    return (int(m.group(1)), filename) if m else (float("inf"), filename)


def _sanitize_name(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return UNSAFE_CHARS_RE.sub("_", name)


def scan_mailboxes(
    mail_dir: Path,
    pattern: str = "*",
    logger: Optional[Logger] = None,
) -> list[MailboxInfo]:
    """Discover mailboxes and their .emlx files under mail_dir."""
    if not mail_dir.exists():
        # Likely a TCC / Full Disk Access issue
        if mail_dir.parent.exists():
            msg = (
                f"Permission denied reading {mail_dir}/\n\n"
                "Apple Mail data is protected by macOS. To grant access:\n"
                "  1. Open System Settings \u2192 Privacy & Security \u2192 Full Disk Access\n"
                "  2. Enable access for your terminal app (Terminal.app, iTerm2, etc.)\n"
                "  3. Restart your terminal and re-run this tool."
            )
        else:
            msg = f"Mail directory not found: {mail_dir}"
        if logger:
            logger.error(msg)
        return []

    # Find V* subdirectories (V9, V10, etc.)
    v_dirs = sorted(
        [d for d in mail_dir.iterdir() if d.is_dir() and d.name.startswith("V")]
    )
    if not v_dirs:
        # Treat mail_dir itself as root (supports --mail-dir override and self-test)
        v_dirs = [mail_dir]

    mailboxes: list[MailboxInfo] = []

    for v_dir in v_dirs:
        # Find account directories (skip MailData)
        for account_dir in sorted(v_dir.iterdir()):
            if not account_dir.is_dir() or account_dir.name == "MailData":
                continue

            account_id = account_dir.name

            # Recursively find .mbox directories with Messages/ subdirs
            for dirpath, dirnames, _filenames in os.walk(account_dir):
                dp = Path(dirpath)
                if dp.name.endswith(".mbox") and (dp / "Messages").is_dir():
                    messages_dir = dp / "Messages"

                    # Collect .emlx files
                    emlx_files = sorted(
                        [
                            f
                            for f in messages_dir.iterdir()
                            if f.is_file() and f.name.endswith(".emlx")
                        ],
                        key=lambda p: _emlx_sort_key(p.name),
                    )

                    if not emlx_files:
                        continue

                    # Derive human-readable name from path
                    try:
                        rel = dp.relative_to(account_dir)
                    except ValueError:
                        rel = Path(dp.name)
                    parts = [
                        p.removesuffix(".mbox") for p in rel.parts if p.endswith(".mbox")
                    ]
                    name = "/".join(parts) if parts else dp.name.removesuffix(".mbox")

                    mailboxes.append(
                        MailboxInfo(
                            name=name,
                            path=dp,
                            emlx_files=emlx_files,
                            message_count=len(emlx_files),
                            account_id=account_id,
                        )
                    )

    # Handle duplicate names across accounts
    name_counts: dict[str, int] = {}
    for mb in mailboxes:
        name_counts[mb.name] = name_counts.get(mb.name, 0) + 1
    for mb in mailboxes:
        if name_counts[mb.name] > 1:
            short_id = mb.account_id[:8]
            mb.name = f"{mb.name} ({short_id})"

    # Filter by pattern
    if pattern != "*":
        mailboxes = [mb for mb in mailboxes if fnmatch.fnmatch(mb.name, pattern)]

    # Sort by name for deterministic output
    mailboxes.sort(key=lambda mb: mb.name)

    return mailboxes


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_emlx(emlx_path: Path, logger: Optional[Logger] = None) -> tuple[Optional[bytes], bool]:
    """Parse an .emlx file and return (rfc822_bytes, is_partial).

    Returns (None, is_partial) on any error without raising.
    """
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


# ---------------------------------------------------------------------------
# Writer helpers
# ---------------------------------------------------------------------------


def _extract_from_and_date(rfc822_bytes: bytes) -> tuple[str, str]:
    """Extract sender address and asctime date from RFC 822 headers."""
    # Find header boundary (search only first 16KB)
    search_bytes = rfc822_bytes[:16384]
    headers = search_bytes
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = search_bytes.find(sep)
        if idx != -1:
            headers = search_bytes[:idx]
            break

    # Unfold continuation lines
    headers = re.sub(rb"\r?\n[ \t]+", b" ", headers)

    # Extract From: header
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

    # Extract Date: header
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


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

# Global interrupt flag for SIGINT handling
_interrupted = False


def write_mbox(
    output_path: Path,
    emlx_files: list[Path],
    mailbox_name: str,
    logger: Logger,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> ExportResult:
    """Export a list of .emlx files to a single .mbox file."""
    global _interrupted

    messages_written = 0
    messages_failed = 0
    partial_count = 0
    bytes_written = 0
    hashes: dict[str, str] = {}
    failed_paths: list[Path] = []
    total = len(emlx_files)

    try:
        with open(output_path, "wb") as outf:
            for i, emlx_path in enumerate(emlx_files):
                if _interrupted:
                    logger.warn("Interrupted. Stopping export.")
                    break

                rfc822_bytes, is_partial = parse_emlx(emlx_path, logger)

                if rfc822_bytes is None:
                    messages_failed += 1
                    failed_paths.append(emlx_path)
                    if progress_callback:
                        progress_callback(i + 1, total)
                    continue

                if is_partial:
                    partial_count += 1

                # Generate From_ separator line
                sender, date_str = _extract_from_and_date(rfc822_bytes)
                separator = f"From {sender} {date_str}\n".encode("ascii", errors="replace")

                # Escape From lines in body
                escaped = _escape_from_lines(rfc822_bytes)

                # Compute SHA-256 of message as it will be stored in mbox.
                # The mbox format requires messages to end with \n, so if the
                # original doesn't, we normalize before hashing.
                hash_bytes = rfc822_bytes
                if not rfc822_bytes.endswith(b"\n"):
                    hash_bytes = rfc822_bytes + b"\n"
                h = hashlib.sha256(hash_bytes).hexdigest()
                hashes[emlx_path.name] = h

                # Write separator + escaped message + blank line terminator
                try:
                    n = outf.write(separator)
                    bytes_written += n
                    n = outf.write(escaped)
                    bytes_written += n
                    if not escaped.endswith(b"\n"):
                        n = outf.write(b"\n")
                        bytes_written += n
                    n = outf.write(b"\n")
                    bytes_written += n
                except OSError as e:
                    if e.errno == errno.ENOSPC:
                        logger.error("Disk full. Export cannot continue.")
                        sys.exit(EXIT_FATAL)
                    raise

                messages_written += 1
                logger.debug(f"Exported: {emlx_path}")

                if progress_callback:
                    progress_callback(i + 1, total)

    except OSError as e:
        if e.errno == errno.ENOSPC:
            logger.error("Disk full. Export cannot continue.")
            sys.exit(EXIT_FATAL)
        raise

    return ExportResult(
        mailbox_name=mailbox_name,
        output_path=output_path,
        messages_written=messages_written,
        messages_failed=messages_failed,
        failed_paths=failed_paths,
        hashes=hashes,
        partial_count=partial_count,
        bytes_written=bytes_written,
    )


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def verify_mbox(
    output_path: Path,
    expected_hashes: dict[str, str],
    mailbox_name: str,
) -> VerificationResult:
    """Re-read an .mbox file and verify message hashes against expected values."""
    verified_hashes: list[str] = []
    current_chunks: list[bytes] = []
    is_first_line = True
    prev_blank = False

    def _finalize_message() -> None:
        if not current_chunks:
            return
        msg = b"".join(current_chunks)
        # Remove trailing \n (mbox blank-line terminator)
        if msg.endswith(b"\n"):
            msg = msg[:-1]
        # Unescape mboxrd From_ lines
        unescaped = _unescape_from_lines(msg)
        h = hashlib.sha256(unescaped).hexdigest()
        verified_hashes.append(h)

    with open(output_path, "rb") as f:
        for raw_line in f:
            is_sep = raw_line.startswith(b"From ") and (is_first_line or prev_blank)

            if is_sep:
                _finalize_message()
                current_chunks = []
                is_first_line = False
            else:
                current_chunks.append(raw_line)

            prev_blank = raw_line in (b"\n", b"\r\n")

    # Finalize last message
    _finalize_message()

    # Compare hashes positionally
    expected_names = list(expected_hashes.keys())
    expected_values = list(expected_hashes.values())

    mismatched: list[str] = []
    missing: list[str] = []

    for idx, (name, expected_hash) in enumerate(zip(expected_names, expected_values)):
        if idx < len(verified_hashes):
            if verified_hashes[idx] != expected_hash:
                mismatched.append(name)
        else:
            missing.append(name)

    extra = max(0, len(verified_hashes) - len(expected_values))
    verified_count = len(verified_hashes) - len(mismatched) - extra

    return VerificationResult(
        mailbox_name=mailbox_name,
        expected_count=len(expected_hashes),
        verified_count=verified_count,
        mismatched=mismatched,
        missing=missing,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------


def _format_duration(seconds: float) -> str:
    """Format seconds as 'Xm Ys'."""
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _format_bytes(n: int) -> str:
    """Format byte count in human-readable form."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} KB"
    return f"{n} B"


def write_verification_report(
    output_dir: Path,
    export_results: list[ExportResult],
    verification_results: list[VerificationResult],
    source_dir: Path,
    duration: float,
) -> None:
    """Write verification-report.json."""
    mailbox_reports = []
    for er, vr in zip(export_results, verification_results):
        mailbox_reports.append(
            {
                "name": er.mailbox_name,
                "messages_found": er.messages_written + er.messages_failed,
                "messages_exported": er.messages_written,
                "messages_verified": vr.verified_count,
                "failures": [str(p) for p in er.failed_paths],
                "partial_messages": er.partial_count,
                "sha256_verified": len(vr.mismatched) == 0 and len(vr.missing) == 0,
            }
        )

    total_found = sum(
        er.messages_written + er.messages_failed for er in export_results
    )
    total_exported = sum(er.messages_written for er in export_results)
    total_verified = sum(vr.verified_count for vr in verification_results)
    total_failures = sum(er.messages_failed for er in export_results)
    total_partial = sum(er.partial_count for er in export_results)
    total_bytes = sum(er.bytes_written for er in export_results)

    report = {
        "tool_version": __version__,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "mailboxes": mailbox_reports,
        "totals": {
            "mailboxes": len(export_results),
            "messages_found": total_found,
            "messages_exported": total_exported,
            "messages_verified": total_verified,
            "failures": total_failures,
            "partial": total_partial,
            "duration_seconds": round(duration, 1),
            "output_bytes": total_bytes,
        },
    }

    report_path = output_dir / "verification-report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")


def print_summary(
    source_dir: Path,
    output_dir: Path,
    export_results: list[ExportResult],
    verification_results: Optional[list[VerificationResult]],
    duration: float,
    logger: Logger,
) -> None:
    """Print the final summary block."""
    total_found = sum(
        er.messages_written + er.messages_failed for er in export_results
    )
    total_exported = sum(er.messages_written for er in export_results)
    total_failures = sum(er.messages_failed for er in export_results)
    total_partial = sum(er.partial_count for er in export_results)
    total_bytes = sum(er.bytes_written for er in export_results)

    verified_str = ""
    if verification_results:
        total_verified = sum(vr.verified_count for vr in verification_results)
        verified_str = f" + verified"
        msg_detail = f"{total_found:,} found \u2192 {total_exported:,} exported{verified_str}"
    else:
        msg_detail = f"{total_found:,} found \u2192 {total_exported:,} exported"

    logger.info("")
    logger.info("SUMMARY")
    logger.info(f"  Source:       {source_dir}")
    logger.info(f"  Output:       {output_dir}")
    logger.info(f"  Mailboxes:    {len(export_results)}")
    logger.info(f"  Messages:     {msg_detail}")
    logger.info(f"  Failures:     {total_failures}")
    logger.info(f"  Partial:      {total_partial}")
    logger.info(f"  Duration:     {_format_duration(duration)}")
    logger.info(f"  Output size:  {_format_bytes(total_bytes)}")


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


def _make_emlx(rfc822_bytes: bytes) -> bytes:
    """Build an .emlx file: byte count + message."""
    return f"{len(rfc822_bytes)}\n".encode("ascii") + rfc822_bytes


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

    # Sort to get deterministic order
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
        # Create output subdirectory for nested mailboxes
        safe_name = _sanitize_name(mb.name)
        mbox_out = output_dir / f"{safe_name}.mbox"
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
    write_verification_report(
        output_dir, export_results, verification_results, tmpdir, 0.1
    )
    report_path = output_dir / "verification-report.json"
    assert_true("verification-report.json exists", report_path.exists())
    if report_path.exists():
        with open(report_path) as f:
            report_data = json.load(f)
        assert_eq("report version", report_data["tool_version"], __version__)
        assert_eq("report mailbox count", len(report_data["mailboxes"]), 2)

    # --- Results ---
    total = assertions_passed + assertions_failed
    if assertions_failed == 0:
        logger.info(f"Self-test PASSED: {assertions_passed}/{total} assertions OK")
        return EXIT_SUCCESS
    else:
        logger.error(
            f"Self-test FAILED: {assertions_failed}/{total} assertions failed"
        )
        return EXIT_FATAL


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apple-mail-export",
        description="Export Apple Mail .emlx files to standard .mbox format.",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        metavar="OUTPUT_DIR",
        help="Output directory for .mbox files (default: ./mail-export/)",
    )
    parser.add_argument(
        "--mail-dir",
        type=Path,
        default=DEFAULT_MAIL_DIR,
        help="Apple Mail data directory (default: ~/Library/Mail)",
    )
    parser.add_argument(
        "--mailbox",
        default="*",
        help='Glob pattern to filter mailbox names (e.g., "INBOX", "Work/*")',
    )
    parser.add_argument(
        "--verify",
        dest="verify",
        action="store_true",
        default=True,
        help="Run post-export verification (default)",
    )
    parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="Skip post-export verification",
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
        "--dry-run",
        action="store_true",
        help="Scan and report without writing any files",
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

    # Handle self-test early (no log file, no output dir needed)
    if args.self_test:
        logger = Logger(quiet=False, verbose=True)
        return run_self_test(logger)

    output_dir: Path = args.output_dir.resolve()
    mail_dir: Path = args.mail_dir.resolve()

    # Check for existing .mbox files in output directory
    if output_dir.exists():
        existing = list(output_dir.glob("**/*.mbox"))
        if existing:
            print(
                f"ERROR: Output directory already contains .mbox files: {output_dir}\n"
                "Remove existing files or choose a different output directory.",
                file=sys.stderr,
            )
            return EXIT_FATAL
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Set up log file
    log_file_path = output_dir / "export-log.txt"
    log_fh = open(log_file_path, "w")

    logger = Logger(quiet=args.quiet, verbose=args.verbose, log_file=log_fh)

    # Set up SIGINT handler
    def sigint_handler(signum, frame):
        global _interrupted
        _interrupted = True

    prev_handler = signal.signal(signal.SIGINT, sigint_handler)

    exit_code = EXIT_SUCCESS
    start_time = time.monotonic()

    try:
        # Scan
        logger.info(f"Scanning {mail_dir}/ ...")
        mailboxes = scan_mailboxes(mail_dir, args.mailbox, logger)

        if not mailboxes:
            if args.mailbox != "*":
                logger.error(f"No mailboxes match pattern '{args.mailbox}'.")
            else:
                logger.error(
                    f"No mailboxes found. Check --mail-dir path: {mail_dir}"
                )
            return EXIT_FATAL

        total_messages = sum(mb.message_count for mb in mailboxes)
        total_size = sum(
            sum(f.stat().st_size for f in mb.emlx_files) for mb in mailboxes
        )
        logger.info(
            f"Found {len(mailboxes)} mailboxes, "
            f"{total_messages:,} messages ({_format_bytes(total_size)})"
        )

        # Dry run: just report and exit
        if args.dry_run:
            logger.info("")
            logger.info("DRY RUN — no files will be written.")
            logger.info("")
            for mb in mailboxes:
                mb_size = sum(f.stat().st_size for f in mb.emlx_files)
                logger.info(
                    f"  {mb.name:<30s}  {mb.message_count:>8,} messages  "
                    f"({_format_bytes(mb_size)})"
                )
            return EXIT_SUCCESS

        # Export
        logger.info("")
        logger.info("Exporting mailboxes:")

        export_results: list[ExportResult] = []
        verification_results: list[VerificationResult] = []

        for mb in mailboxes:
            if _interrupted:
                break

            # Build output path, preserving hierarchy
            safe_name = _sanitize_name(mb.name)
            parts = safe_name.split("/")
            if len(parts) > 1:
                mbox_out = output_dir / "/".join(parts[:-1]) / f"{parts[-1]}.mbox"
            else:
                mbox_out = output_dir / f"{safe_name}.mbox"
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
            )
            export_results.append(result)

            if result.messages_failed > 0:
                exit_code = EXIT_PARTIAL

        # Verify
        if args.verify and export_results and not _interrupted:
            logger.info("")
            logger.info("Verifying exports...")

            for result in export_results:
                vr = verify_mbox(
                    result.output_path, result.hashes, result.mailbox_name
                )
                verification_results.append(vr)

                if not vr.mismatched and not vr.missing and vr.extra == 0:
                    logger.info(
                        f"  {result.mailbox_name:<22s}  \u2713  "
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
                    logger.info(
                        f"  {result.mailbox_name:<22s}  \u2717  {', '.join(issues)}"
                    )
                    for name in vr.mismatched + vr.missing:
                        logger.info(f"                            \u2192 {name}")

            # Write verification report
            duration = time.monotonic() - start_time
            write_verification_report(
                output_dir,
                export_results,
                verification_results,
                mail_dir,
                duration,
            )

        # Summary
        duration = time.monotonic() - start_time
        print_summary(
            mail_dir,
            output_dir,
            export_results,
            verification_results if args.verify else None,
            duration,
            logger,
        )

        if _interrupted:
            logger.warn("Export was interrupted. Results are partial.")
            exit_code = EXIT_FATAL

        return exit_code

    finally:
        signal.signal(signal.SIGINT, prev_handler)
        log_fh.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
