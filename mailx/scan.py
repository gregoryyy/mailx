from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Optional

from .logger import Logger
from .model import MailboxInfo

# Numeric prefix in .emlx filenames for sorting
EMLX_NUM_RE = re.compile(r"^(\d+)")
# Characters not safe in output filenames
UNSAFE_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')


def _emlx_sort_key(filename: str) -> tuple:
    """Sort key: numeric prefix first, then full name for ties."""
    m = EMLX_NUM_RE.match(filename)
    return (int(m.group(1)), filename) if m else (float("inf"), filename)


def _sanitize_name(name: str) -> str:
    """Replace filesystem-unsafe characters with underscores."""
    return UNSAFE_CHARS_RE.sub("_", name)


def _output_path_for_mailbox(output_dir: Path, mailbox_name: str) -> Path:
    """Map mailbox name to output .mbox path, preserving nested hierarchy."""
    safe_name = _sanitize_name(mailbox_name)
    parts = safe_name.split("/")
    if len(parts) > 1:
        return output_dir / "/".join(parts[:-1]) / f"{parts[-1]}.mbox"
    return output_dir / f"{safe_name}.mbox"


def scan_mailboxes(
    mail_dir: Path,
    pattern: str = "*",
    logger: Optional[Logger] = None,
) -> list[MailboxInfo]:
    """Discover mailboxes and their .emlx files under mail_dir."""
    if not mail_dir.exists():
        if mail_dir.parent.exists():
            msg = (
                f"Permission denied reading {mail_dir}/\n\n"
                "Apple Mail data is protected by macOS. To grant access:\n"
                "  1. Open System Settings -> Privacy & Security -> Full Disk Access\n"
                "  2. Enable access for your terminal app (Terminal.app, iTerm2, etc.)\n"
                "  3. Restart your terminal and re-run this tool."
            )
        else:
            msg = f"Mail directory not found: {mail_dir}"
        if logger:
            logger.error(msg)
        return []

    def _permission_help(path: Path) -> str:
        return (
            f"Permission denied reading {path}/\n\n"
            "Apple Mail data is protected by macOS. To grant access:\n"
            "  1. Open System Settings -> Privacy & Security -> Full Disk Access\n"
            "  2. Enable access for your terminal app (Terminal.app, iTerm2, etc.)\n"
            "  3. Restart your terminal and re-run this tool."
        )

    try:
        v_dirs = sorted(
            [d for d in mail_dir.iterdir() if d.is_dir() and d.name.startswith("V")]
        )
    except PermissionError:
        if logger:
            logger.error(_permission_help(mail_dir))
        return []
    except OSError as e:
        if logger:
            logger.error(f"I/O error reading {mail_dir}: {e}")
        return []

    if not v_dirs:
        v_dirs = [mail_dir]

    mailboxes: list[MailboxInfo] = []

    for v_dir in v_dirs:
        grouped: dict[Path, list[Path]] = {}

        def on_walk_error(err: OSError) -> None:
            if logger:
                logger.warn(f"Cannot read directory {err.filename}: {err.strerror}")

        for dirpath, dirnames, filenames in os.walk(v_dir, onerror=on_walk_error):
            dp = Path(dirpath)
            if "MailData" in dp.parts:
                dirnames[:] = []
                continue
            if "MailData" in dirnames:
                dirnames.remove("MailData")

            emlx_names = [name for name in filenames if name.lower().endswith(".emlx")]
            if not emlx_names:
                continue

            mailbox_root: Optional[Path] = None
            cursor = dp
            while True:
                if cursor.name.endswith(".mbox"):
                    mailbox_root = cursor
                    break
                if cursor == v_dir or cursor.parent == cursor:
                    break
                cursor = cursor.parent

            if mailbox_root is None:
                if logger:
                    logger.debug(f"Skipping .emlx files outside .mbox tree: {dp}")
                continue

            files = grouped.setdefault(mailbox_root, [])
            files.extend(dp / name for name in emlx_names)

        for mailbox_root, emlx_files_unsorted in grouped.items():
            emlx_files = sorted(emlx_files_unsorted, key=lambda p: _emlx_sort_key(p.name))
            if not emlx_files:
                continue

            try:
                rel = mailbox_root.relative_to(v_dir)
            except ValueError:
                rel = Path(mailbox_root.name)
            parts = [p.removesuffix(".mbox") for p in rel.parts if p.endswith(".mbox")]
            name = "/".join(parts) if parts else mailbox_root.name.removesuffix(".mbox")

            account_id = rel.parts[0] if rel.parts else "UNKNOWN"

            mailboxes.append(
                MailboxInfo(
                    name=name,
                    path=mailbox_root,
                    emlx_files=emlx_files,
                    message_count=len(emlx_files),
                    account_id=account_id,
                )
            )

    name_counts: dict[str, int] = {}
    for mb in mailboxes:
        name_counts[mb.name] = name_counts.get(mb.name, 0) + 1
    for mb in mailboxes:
        if name_counts[mb.name] > 1:
            short_id = mb.account_id[:8]
            mb.name = f"{mb.name} ({short_id})"

    if pattern != "*":
        mailboxes = [mb for mb in mailboxes if fnmatch.fnmatch(mb.name, pattern)]

    mailboxes.sort(key=lambda mb: mb.name)
    return mailboxes
