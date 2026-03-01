from __future__ import annotations

import errno
import hashlib
import sys
from pathlib import Path
from typing import Callable, Optional

from .emlx import (
    _escape_from_lines,
    _extract_from_and_date,
    _hash_for_mbox_verification,
    _unescape_from_lines,
    parse_emlx,
)
from .logger import Logger
from .model import EXIT_FATAL, ExportResult, VerificationResult


def build_expected_hashes(
    emlx_files: list[Path],
    logger: Logger,
) -> tuple[dict[str, str], int, list[Path], int]:
    """Parse .emlx files and compute expected message hashes.

    Returns (hashes, failed_count, failed_paths, partial_count).
    """
    hashes: dict[str, str] = {}
    failed_count = 0
    failed_paths: list[Path] = []
    partial_count = 0

    for emlx_path in emlx_files:
        rfc822_bytes, is_partial = parse_emlx(emlx_path, logger)
        if rfc822_bytes is None:
            failed_count += 1
            failed_paths.append(emlx_path)
            continue
        if is_partial:
            partial_count += 1
        hashes[emlx_path.name] = _hash_for_mbox_verification(rfc822_bytes)

    return hashes, failed_count, failed_paths, partial_count


def write_mbox(
    output_path: Path,
    emlx_files: list[Path],
    mailbox_name: str,
    logger: Logger,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> ExportResult:
    """Export a list of .emlx files to a single .mbox file."""
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
                if should_stop and should_stop():
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

                sender, date_str = _extract_from_and_date(rfc822_bytes)
                separator = f"From {sender} {date_str}\n".encode("ascii", errors="replace")

                escaped = _escape_from_lines(rfc822_bytes)

                hashes[emlx_path.name] = _hash_for_mbox_verification(rfc822_bytes)

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
        if msg.endswith(b"\n"):
            msg = msg[:-1]
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

    _finalize_message()

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
