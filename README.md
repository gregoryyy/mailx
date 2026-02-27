# apple-mail-export

A single-file Python CLI tool that exports Apple Mail mailboxes to standard `.mbox` format by reading directly from Apple Mail's on-disk `.emlx` storage.

Designed for power users migrating or backing up large mailboxes (100k+ messages, 10GB+). Zero external dependencies — stdlib only.

## Requirements

- macOS
- Python 3.9+
- Full Disk Access enabled for your terminal app (to read `~/Library/Mail/`)

## Quick Start

```bash
# Run with default settings (exports all mailboxes to ./mail-export/)
python3 apple-mail-export.py

# Export to a specific directory
python3 apple-mail-export.py ~/backup/mail-2026/

# Export only INBOX
python3 apple-mail-export.py --mailbox "INBOX" ~/backup/mail-2026/

# Dry run — see what would be exported without writing files
python3 apple-mail-export.py --dry-run

# Validate the tool works correctly with synthetic data
python3 apple-mail-export.py --self-test
```

## Usage

```
apple-mail-export [OPTIONS] [OUTPUT_DIR]
```

| Argument / Flag | Default | Description |
|---|---|---|
| `OUTPUT_DIR` | `./mail-export/` | Output directory for `.mbox` files |
| `--mail-dir PATH` | `~/Library/Mail` | Override Apple Mail data directory |
| `--mailbox PATTERN` | `*` | Glob pattern to filter mailbox names |
| `--verify` / `--no-verify` | `--verify` | Run post-export SHA-256 verification |
| `--quiet` | off | Only print summary and errors |
| `--verbose` | off | Print debug-level detail |
| `--dry-run` | off | Scan and report without writing files |
| `--self-test` | — | Run self-test with synthetic data and exit |
| `--version` | — | Print version and exit |

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | All messages exported and verified |
| 1 | Export complete but some failures or verification issues |
| 2 | Fatal error (I/O, permissions, disk full) |
| 3 | Invalid arguments |

## Output

```
~/backup/mail-2026/
├── INBOX.mbox                  # Standard mbox format
├── Sent.mbox
├── Work/
│   └── Projects.mbox
├── verification-report.json    # SHA-256 verification details
└── export-log.txt              # Timestamped log of the export
```

## Full Disk Access

Apple Mail data at `~/Library/Mail/` is protected by macOS. Your terminal app needs Full Disk Access:

1. Open **System Settings > Privacy & Security > Full Disk Access**
2. Enable access for your terminal (Terminal.app, iTerm2, etc.)
3. Restart the terminal

## Development

```bash
# Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# Run tests
pytest test_apple_mail_export.py -v

# Run built-in self-test
python3 apple-mail-export.py --self-test
```

## How It Works

The tool has five logical stages:

1. **Scanner** — Discovers mailboxes under `~/Library/Mail/V{9,10}/` by finding `.mbox` directories that contain `Messages/` subdirectories
2. **Parser** — Reads each `.emlx` file (Apple's per-message format): parses the byte count header, extracts the RFC 822 message body
3. **Writer** — Writes messages to standard `.mbox` files (RFC 4155) with proper `From ` separators and mboxrd escaping
4. **Verifier** — Re-reads each `.mbox` file, splits messages, un-escapes, and compares SHA-256 hashes against the originals
5. **Reporter** — Generates terminal output, `verification-report.json`, and `export-log.txt`

## License

MIT
