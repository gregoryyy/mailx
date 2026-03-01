# MAILX: apple-mail-export

A modular Python CLI tool that exports Apple Mail mailboxes to standard `.mbox` format by reading directly from Apple Mail's on-disk `.emlx` storage.

Designed for power users migrating or backing up large mailboxes (100k+ messages, 10GB+). Zero external dependencies — stdlib only.

**Author:** Gregor Heinrich  
**Assistants:** Claude Code Opus 4.6, GPT 5.3 Codex  
**Date:** February 2026  
**Version:** v0.2

## Motivation

The need: 
- Apple Mail export is notoriously brittle, breaking without notice for unknown reasons.
- Apple Mail admin actions like moving messages between mailboxes are intransparent and brittle as well, esp. with larger mailboxes.  Infinite "Moving...", "Copying...", "Rebuiding...", "Downloading..."

The solutions:
- Existing solutions don't always seem to be up to date.
- AI-assisted coding based on known requirements allows fast implementation of good-quality software.

--> Normally, this project would have been a clear "Buy" not "Make".  But after trying the second tool in vain, I started giving "Make" a shot.  It took <1h to v0.1 and another 20' for the refactor to v0.2.  AI is an extreme efficiency booster if you know what you want and what a good result looks like.

## Requirements

- macOS
- Python 3.9+
- Full Disk Access enabled for your terminal app (to read `~/Library/Mail/`)

## Quick Start

```bash
# List all discovered mailboxes (default action)
python3 apple-mail-export.py

# Explicit list mode with a filter
python3 apple-mail-export.py --list "INBOX/*"

# Export one mailbox (export implies verify unless --no-verify)
python3 apple-mail-export.py --export "INBOX" --output-dir ~/backup/mail-2026/

# Verify existing exports
python3 apple-mail-export.py --verify --output-dir ~/backup/mail-2026/

# Validate the tool works correctly with synthetic data
python3 apple-mail-export.py --self-test
```

## Usage

```
apple-mail-export [OPTIONS] [GLOB]
```

| Argument / Flag | Default | Description |
|---|---|---|
| `GLOB` | `*` | Glob pattern to filter mailbox names |
| `--mail-dir PATH` | `~/Library/Mail` | Override Apple Mail data directory |
| `--output-dir DIR` | `./mail-export/` | Output directory for `.mbox` files |
| `--list` | default action | List matching mailboxes and exit |
| `--export` | off | Export matching mailbox(es) |
| `--verify` | off | Verify matching mailbox exports (new or existing) |
| `--no-verify` | off | With `--export`, skip post-export verification |
| `--quiet` | off | Only print summary and errors |
| `--verbose` | off | Print debug-level detail |
| `--self-test` | — | Run self-test with synthetic data and exit |
| `--version` | — | Print version and exit |

Quote glob patterns (for example `"INBOX/*"`), so your shell does not expand them before the tool receives them.

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

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run the full test suite (90 tests)
pytest tests -v

# Run a specific test class
pytest tests/test_mailx_emlx.py::TestParseEmlx -v
pytest tests/test_mailx_emlx.py::TestFromEscaping -v
pytest tests/test_mailx_scan.py::TestScanner -v
pytest tests/test_mailx_mbox.py::TestWriter -v
pytest tests/test_mailx_mbox.py::TestVerifier -v
pytest tests/test_mailx_cli.py::TestCLI -v

# Run a single test
pytest tests/test_mailx_mbox.py::TestVerifier::test_tampered_mbox_detects_mismatch -v
```

### Built-in Self-Test

The tool includes a `--self-test` flag that runs without pytest or a venv. It creates synthetic `.emlx` files in a temp directory, runs the full export pipeline, and verifies the results:

```bash
python3 apple-mail-export.py --self-test
```

### Running the Tool

```bash
# List all mailboxes (requires Full Disk Access)
python3 apple-mail-export.py

# Export all mailboxes to a target directory
python3 apple-mail-export.py --export --output-dir ~/backup/mail-export/

# Export a single mailbox with verbose output
python3 apple-mail-export.py --export --verbose --output-dir ~/backup/mail-export/ "INBOX"

# Verify existing exports only (no rewrite)
python3 apple-mail-export.py --verify --output-dir ~/backup/mail-export/
```

## How It Works

The tool has five logical stages:

1. **Scanner** — Discovers mailboxes and `.emlx` files across Apple Mail layout variants
2. **Parser** — Reads each `.emlx` file (byte count + RFC 822 payload)
3. **Writer** — Writes standard `.mbox` output (RFC 4155 + mboxrd escaping)
4. **Verifier** — Re-reads `.mbox` files and compares SHA-256 message hashes
5. **Reporter** — Generates terminal output, `verification-report.json`, and `export-log.txt`

### Module Layout

- `apple-mail-export.py` — CLI entrypoint, argument parsing, action orchestration (`--list`, `--export`, `--verify`)
- `mailx/model.py` — shared constants, exit codes, and dataclasses
- `mailx/logger.py` — terminal and file logging
- `mailx/scan.py` — mailbox discovery and name/output-path helpers
- `mailx/emlx.py` — `.emlx` parsing and message-level helpers
- `mailx/mbox.py` — mbox writing, expected-hash building, and verification
- `mailx/report.py` — formatting, summaries, and verification report writing

## License

MIT

## Authorship and AI Assistance

Primary author: Gregor Heinrich

AI tools were used for drafting, implementation, refactoring and test scaffolding based on specific author instructions. LLM agents are used in narrow loops between specification, context and outputs, all in verifiable files.

All designs, final decisions, code review, testing and release approval were performed by the author.

The author is accountable for correctness, licensing and security of the published work.

## History

- v0.2 modularized code
- v0.1 refactor to split monolith + create package
