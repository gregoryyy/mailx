# apple-mail-export — Specification

## Overview

A single-file Python CLI tool that reliably exports Apple Mail mailboxes to standard `.mbox` format by reading directly from Apple Mail's on-disk `.emlx` storage, bypassing Mail.app entirely. Designed for power users migrating or backing up large mailboxes (100k+ messages, 10GB+).

## Architecture

```
┌─────────────────────────────────┐
│   ~/Library/Mail/V10/           │
│   ├── INBOX/                    │
│   │   ├── Messages/             │
│   │   │   ├── 12345.emlx        │   READ-ONLY
│   │   │   ├── 12345.partial.emlx│   ────────►
│   │   │   └── ...               │
│   │   └── Info.plist            │
│   └── ...                       │
└─────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│   apple-mail-export             │
│                                 │
│   1. Scanner                    │  Discovers mailboxes and .emlx files
│   2. Parser                     │  Reads .emlx → extracts RFC 822 body
│   3. Writer                     │  Appends to .mbox (RFC 4155)
│   4. Verifier                   │  Re-reads .mbox, compares hashes
│   5. Reporter                   │  Summary + verification report
└─────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────┐
│   ~/backup/mail-export/         │
│   ├── INBOX.mbox                │
│   ├── Sent.mbox                 │   OUTPUT
│   ├── Work/                     │   ────────►
│   │   └── Projects.mbox         │
│   ├── verification-report.json  │
│   └── export-log.txt            │
└─────────────────────────────────┘
```

## Constraints

- **Language:** Python 3.9+ (ships with macOS)
- **Dependencies:** Zero external dependencies. stdlib only.
- **Single file:** One `.py` file. Executable via `python3 apple-mail-export.py` or `chmod +x` + shebang.
- **macOS only.**
- **Read-only on source:** Never write, modify, or delete anything under `~/Library/Mail/`.
- **Streaming:** Never hold an entire mailbox in memory. Process one `.emlx` at a time, append to `.mbox`.

## Apple Mail Storage Format

### Directory Layout

Apple Mail stores data in `~/Library/Mail/` with version-specific subdirectories. The tool must handle:

- `V10/` (macOS Ventura+, most common current layout)
- `V9/` (older macOS versions)
- Other `V*` directories if present

### Mailbox Structure

Each mailbox is a `.mbox` directory (confusingly named by Apple, it is NOT mbox format) containing:

```
SomeMailbox.mbox/
├── Info.plist              # Mailbox metadata (optional, may not exist)
├── Messages/
│   ├── 12345.emlx          # Individual message
│   ├── 12345.partial.emlx  # Partial/large message variant
│   └── ...
└── table_of_contents       # Binary index (ignore)
```

Mailbox hierarchy is represented by nested directories. The tool must recursively discover all `.mbox` directories.

### Account Structure

Messages are organized under account UUIDs:

```
~/Library/Mail/V10/
├── MailData/                    # Global mail data (ignore)
├── <UUID>/                      # Account directory
│   ├── INBOX.mbox/
│   ├── Sent Messages.mbox/
│   ├── Drafts.mbox/
│   └── CustomFolder.mbox/
│       └── SubFolder.mbox/
└── <UUID>/                      # Another account
    └── ...
```

The tool should discover all accounts and present a unified view. Mailbox names should be derived from the directory names with `.mbox` suffix stripped.

### `.emlx` File Format

Each `.emlx` file has this structure:

```
<byte_count>\n
<RFC 822 message of exactly byte_count bytes>
<optional Apple plist XML metadata>
```

- **Line 1:** Integer byte count of the RFC 822 message that follows.
- **Lines 2+:** The raw RFC 822 email message (headers + body), exactly `byte_count` bytes.
- **Remainder:** Optional Apple plist XML with flags, date received, etc. We discard this.

#### Required Content Rules

- The first line must parse as an integer.
- The byte count must be `>= 0`.
- The file must contain at least `byte_count` bytes after the first line.
- The RFC 822 section may contain plain text, HTML, or MIME multipart content.

#### Parser Behavior for This Tool

- Read first line as byte count.
- Read exactly `byte_count` bytes as the message payload.
- Ignore all remaining trailing bytes (Apple metadata).
- If byte count is invalid, negative, or payload is truncated, treat the file as corrupt and continue.

### `.partial.emlx` Files

These contain messages that were only partially downloaded. The tool should:

1. Attempt to include them in the export.
2. Flag them in the verification report as partial.
3. Not count them as failures — they are intentionally incomplete.

## CLI Interface

### Usage

```
apple-mail-export [OPTIONS] [OUTPUT_DIR]
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `OUTPUT_DIR` | `./mail-export/` | Output directory for `.mbox` files |

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--mail-dir PATH` | `~/Library/Mail` | Override Apple Mail data directory |
| `--mailbox PATTERN` | `*` | Glob pattern to filter mailbox names (e.g., `"INBOX"`, `"Work/*"`) |
| `--verify / --no-verify` | `--verify` | Run post-export verification (on by default) |
| `--quiet` | off | Only print summary and errors |
| `--verbose` | off | Print debug-level detail (file paths, timing) |
| `--dry-run` | off | Scan and report without writing any files |
| `--version` | — | Print version string and exit |
| `--help` | — | Print usage and exit |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success: all messages exported and verified |
| 1 | Partial success: export complete but verification found failures |
| 2 | Fatal error: export could not complete (I/O error, permissions, etc.) |
| 3 | Invalid arguments |

## Module Design

All in a single file, but logically organized into these sections:

### 1. Scanner (`scan_mailboxes`)

```python
def scan_mailboxes(mail_dir: Path, pattern: str = "*") -> list[MailboxInfo]
```

- Recursively walk `mail_dir` for directories ending in `.mbox` that contain a `Messages/` subdirectory.
- Support both `V9/` and `V10/` layouts.
- Filter by glob `pattern` against the derived mailbox name.
- Return list of `MailboxInfo` dataclass:

```python
@dataclass
class MailboxInfo:
    name: str              # e.g., "INBOX", "Work/Projects"
    path: Path             # Absolute path to the .mbox directory
    emlx_files: list[Path] # All .emlx and .partial.emlx files found
    message_count: int     # len(emlx_files)
    account_id: str        # UUID of the parent account
```

- Derive human-readable `name` by stripping `.mbox` suffix and reconstructing hierarchy from directory nesting.
- Sort `emlx_files` numerically by the integer filename (12345.emlx → 12345) for deterministic output order.

### 2. Parser (`parse_emlx`)

```python
def parse_emlx(emlx_path: Path) -> tuple[bytes, bool]
```

- Open file in binary mode (`rb`).
- Read first line, parse as integer byte count.
- Read exactly `byte_count` bytes → this is the RFC 822 message.
- Return `(rfc822_bytes, is_partial)` where `is_partial` is `True` if filename ends with `.partial.emlx`.
- **Error handling:** If byte count is missing, unparseable, or file is truncated, log warning and return `(None, is_partial)`. Do not raise — the export continues with other messages.

### 3. Writer (`write_mbox`)

```python
def write_mbox(output_path: Path, emlx_files: list[Path], progress_callback) -> ExportResult
```

- Open output `.mbox` file for writing in binary mode.
- For each `.emlx` file:
  1. Parse with `parse_emlx()`.
  2. If parse returned `None`, record as failure, continue.
  3. Generate `From ` separator line per RFC 4155:
     - Extract `From` header and `Date` header from the RFC 822 bytes.
     - Format: `From sender@example.com Thu Jan 01 00:00:00 2025\n`
     - If headers can't be parsed, use `From MAILER-DAEMON Thu Jan 01 00:00:00 1970\n` as fallback.
  4. Escape any lines in the body starting with `From ` by prepending `>`.
  5. Write separator + message + trailing newline.
  6. Compute SHA-256 of the RFC 822 bytes (before mbox escaping) and store in memory for verification.
  7. Call `progress_callback` with current count.
- Return `ExportResult`:

```python
@dataclass
class ExportResult:
    mailbox_name: str
    output_path: Path
    messages_written: int
    messages_failed: int
    failed_paths: list[Path]
    hashes: dict[str, str]      # {emlx_filename: sha256_hex}
    partial_count: int
    bytes_written: int
```

### 4. Verifier (`verify_mbox`)

```python
def verify_mbox(output_path: Path, expected_hashes: dict[str, str]) -> VerificationResult
```

- Re-read the `.mbox` file.
- Split on `From ` separator lines to extract individual messages.
- For each extracted message:
  1. Un-escape `>From ` lines back to `From `.
  2. Compute SHA-256.
  3. Match against `expected_hashes`.
- Return `VerificationResult`:

```python
@dataclass
class VerificationResult:
    mailbox_name: str
    expected_count: int
    verified_count: int
    mismatched: list[str]       # emlx filenames with hash mismatch
    missing: list[str]          # emlx filenames not found in mbox
    extra: int                  # messages in mbox not in expected set
```

### 5. Reporter

#### Terminal Output

**Scan phase:**
```
Scanning ~/Library/Mail/V10/ ...
Found 14 mailboxes, 103,847 messages (9.7 GB)
```

**Export phase (per mailbox):**
```
Exporting mailboxes:
  INBOX                  [################............]  58%  43,210/74,502  (1,204 msg/s)
```

- Progress bar updates in-place (carriage return). 
- Width-aware: detect terminal width, truncate mailbox name if needed.
- If `--quiet`: suppress progress, only print final summary.
- If `--verbose`: also print each `.emlx` file path as processed.

**Verification phase:**
```
Verifying exports...
  INBOX.mbox             ✓  74,502 messages, SHA-256 verified
  Drafts.mbox            ✗  2 messages failed integrity check
                            → 12345.emlx
                            → 12348.emlx
```

**Summary (always printed):**
```
SUMMARY
  Source:       ~/Library/Mail/V10/
  Output:       ~/backup/mail-2026/
  Mailboxes:    14
  Messages:     103,847 found → 103,845 exported + verified
  Failures:     2
  Partial:      23
  Duration:     4m 32s
  Output size:  9.6 GB
```

#### verification-report.json

```json
{
  "tool_version": "0.1.0",
  "timestamp": "2026-02-27T14:30:00Z",
  "source_dir": "~/Library/Mail/V10/",
  "output_dir": "~/backup/mail-2026/",
  "mailboxes": [
    {
      "name": "INBOX",
      "messages_found": 74502,
      "messages_exported": 74502,
      "messages_verified": 74502,
      "failures": [],
      "partial_messages": 3,
      "sha256_verified": true
    }
  ],
  "totals": {
    "mailboxes": 14,
    "messages_found": 103847,
    "messages_exported": 103845,
    "messages_verified": 103845,
    "failures": 2,
    "partial": 23,
    "duration_seconds": 272,
    "output_bytes": 10307921510
  }
}
```

#### export-log.txt

Full terminal output captured to file, including timestamps on each line.

## Error Handling

| Error | Behavior |
|-------|----------|
| `~/Library/Mail` not found | Print error explaining Full Disk Access requirement. Link to macOS settings. Exit 2. |
| Permission denied on `.emlx` file | Log warning, skip message, count as failure. Continue. |
| Corrupt `.emlx` (bad byte count) | Log warning, skip message, count as failure. Continue. |
| Disk full during write | Exit 2 with clear message. Partial `.mbox` files remain (user can delete). |
| Output dir already has `.mbox` files | Exit 2 with message: "Remove existing files or choose different output directory." |
| No mailboxes found | Exit 2: "No mailboxes found. Check --mail-dir path." |
| No mailboxes match `--mailbox` pattern | Exit 2: "No mailboxes match pattern 'X'." |
| Keyboard interrupt (Ctrl+C) | Clean exit. Print partial summary of what was exported so far. Exit 2. |

## Full Disk Access Note

The tool reads `~/Library/Mail/` which is protected by macOS TCC. The terminal application running the tool must have Full Disk Access enabled.

The tool must detect this failure mode and print a helpful message:

```
Error: Permission denied reading ~/Library/Mail/

Apple Mail data is protected by macOS. To grant access:
  1. Open System Settings → Privacy & Security → Full Disk Access
  2. Enable access for your terminal app (Terminal.app, iTerm2, etc.)
  3. Restart your terminal and re-run this tool.
```

## Testing Approach

Since this will be prototyped in Claude Code, include a `--self-test` flag that:

1. Creates a temporary directory with synthetic `.emlx` files (valid, corrupt, partial).
2. Runs the full pipeline: scan → export → verify.
3. Asserts expected outcomes.
4. Cleans up temp directory.

This allows the tool to be validated without access to real Apple Mail data.

### Synthetic Test Data

The self-test should generate:

- **5 valid `.emlx` files** with known content, including:
  - Plain text message
  - HTML message
  - Message with MIME attachment (small base64-encoded PNG)
  - Message with multiple recipients
  - Message with non-ASCII headers (UTF-8 encoded subject)
- **1 corrupt `.emlx`** (wrong byte count)
- **1 `.partial.emlx`** (valid but flagged as partial)
- **Nested mailbox structure** (2 levels deep)

Expected results:
- 5 messages exported, 1 failure (corrupt), 1 partial
- Verification passes for 5 + 1 partial
- Corrupt file listed in failures
- Exit code 1 (partial success due to corrupt file)

## Performance Expectations

For 100k messages / 10GB:
- **Scan phase:** < 30 seconds (filesystem stat calls only)
- **Export phase:** Bounded by disk I/O. Target: 1000+ messages/second on SSD.
- **Verification phase:** Similar to export (re-read + hash). ~same duration.
- **Total:** Under 10 minutes on modern Mac with SSD.

Memory usage should stay under 100MB regardless of mailbox size (streaming).

## Version

`0.1.0` — MVP one-shot export.
