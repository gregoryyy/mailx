from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__version__ = "0.1.0"

DEFAULT_MAIL_DIR = Path.home() / "Library" / "Mail"
DEFAULT_OUTPUT_DIR = Path("./mail-export")

EXIT_SUCCESS = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2
EXIT_BAD_ARGS = 3

FALLBACK_SENDER = "MAILER-DAEMON"
FALLBACK_DATE = "Thu Jan  1 00:00:00 1970"


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
