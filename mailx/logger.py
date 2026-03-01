from __future__ import annotations

import datetime
import shutil
import sys
from typing import IO, Optional


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
