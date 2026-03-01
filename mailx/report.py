from __future__ import annotations

import datetime
import json
import unicodedata
from pathlib import Path
from typing import Optional

from .logger import Logger
from .model import ExportResult, VerificationResult, __version__


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


def _display_width(s: str) -> int:
    """Approximate terminal cell width for a Unicode string."""
    width = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def _pad_display(s: str, width: int) -> str:
    """Pad with spaces to a target terminal display width."""
    pad = max(0, width - _display_width(s))
    return s + (" " * pad)


def _truncate_display(s: str, width: int) -> str:
    """Truncate a string to target terminal display width (with ellipsis)."""
    if width <= 0:
        return ""
    if _display_width(s) <= width:
        return s
    if width == 1:
        return "…"

    out: list[str] = []
    used = 0
    target = width - 1
    for ch in s:
        if unicodedata.combining(ch):
            if out:
                out.append(ch)
            continue
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + ch_w > target:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + "…"


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

    total_found = sum(er.messages_written + er.messages_failed for er in export_results)
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
    total_found = sum(er.messages_written + er.messages_failed for er in export_results)
    total_exported = sum(er.messages_written for er in export_results)
    total_failures = sum(er.messages_failed for er in export_results)
    total_partial = sum(er.partial_count for er in export_results)
    total_bytes = sum(er.bytes_written for er in export_results)

    if verification_results:
        verified_str = " + verified"
        msg_detail = f"{total_found:,} found → {total_exported:,} exported{verified_str}"
    else:
        msg_detail = f"{total_found:,} found → {total_exported:,} exported"

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
