"""mailx package."""

from .model import (
    DEFAULT_MAIL_DIR,
    DEFAULT_OUTPUT_DIR,
    EXIT_BAD_ARGS,
    EXIT_FATAL,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    ExportResult,
    FALLBACK_DATE,
    FALLBACK_SENDER,
    MailboxInfo,
    VerificationResult,
    __version__,
)

__all__ = [
    "__version__",
    "DEFAULT_MAIL_DIR",
    "DEFAULT_OUTPUT_DIR",
    "EXIT_SUCCESS",
    "EXIT_PARTIAL",
    "EXIT_FATAL",
    "EXIT_BAD_ARGS",
    "FALLBACK_SENDER",
    "FALLBACK_DATE",
    "MailboxInfo",
    "ExportResult",
    "VerificationResult",
]
