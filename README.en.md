# gmail-statement-fetcher

Automatically download bank/financial statement PDFs from Gmail — config-driven, deduplication built-in, dual IMAP/OAuth support.

**Requires Python 3.9+** · Part of the [notoriouslab](https://github.com/notoriouslab) open-source toolkit.

> [繁體中文 README](README.md)

---

## Why This Tool

Most Gmail-based statement tools are one-off scripts tied to a single bank.
This one is **config-driven**: add any bank without touching code. Any AI agent framework can call it via shell — a `SKILL.md` is included for direct [OpenClaw](https://openclaw.ai/) integration.

| Feature | Description |
|---------|-------------|
| Multi-bank | JSON config, no code changes to add a bank |
| Deduplication | UID-based, never re-downloads the same email |
| IMAP mode | stdlib only, zero install, headless-friendly |
| OAuth 2.0 | `gmail.readonly` scope |
| ZIP extraction | stdlib, ZIP bomb–protected (100 MB cap) |
| PDF decryption | optional pikepdf; passwords in `.env`, not config |
| Normalized filenames | `永豐銀行_信用卡對帳單_2026_02.pdf` |
| Dry-run preview | `--dry-run` shows matches without downloading |
| Atomic writes | `tempfile` + `os.replace()` — no partial files |
| Privacy-safe dedup | stores subject SHA-256 hashes, not raw subjects |
| Security hardened | token 0o600, log masking, log injection stripped |

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/notoriouslab/gmail-statement-fetcher.git
cd gmail-statement-fetcher

# 2. Copy and edit config
cp config.example.json config.json
# Edit config.json — add your bank's sender domain and subject keywords

# 3. Set credentials
cp .env.example .env
# Edit .env — fill in GMAIL_USER and GMAIL_APP_PASSWORD

# 4a. IMAP mode — no extra install needed
pip install python-dotenv   # optional but recommended
python3 fetcher.py

# 4b. OAuth mode — install dependencies first
pip install google-auth-oauthlib google-api-python-client python-dotenv
# Set AUTH_METHOD=oauth in .env, place credentials.json in project root
python3 fetcher.py

# Output: ./downloads/永豐銀行_銀行對帳單_2026_02.pdf
```

Preview matched emails without downloading:

```bash
python3 fetcher.py --dry-run --verbose
```

---

## Authentication

### IMAP + App Password — recommended for servers

Headless-friendly, no browser needed, stdlib only.

1. Enable 2FA on your Google account
2. Go to **Security → App Passwords**, create one for "Mail"
3. Set in `.env`: `AUTH_METHOD=imap`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`

### OAuth 2.0 — recommended for personal use

Uses `gmail.readonly` scope — more secure, but requires one-time browser authorization.

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the Gmail API
3. Create OAuth credentials (Desktop app) → download `credentials.json` → **place in project root**
4. Install: `pip install google-auth-oauthlib google-api-python-client`
5. Set `AUTH_METHOD=oauth` in `.env`
6. First run opens a browser for authorization → generates `token.json`

> **Headless servers**: After the first OAuth run on a local machine, copy `token.json` to your server and set `OAUTH_TOKEN=/path/to/token.json`. Keep this file backed up — losing it requires re-authorization.

---

## Configuration

### Config file format

```jsonc
{
  "banks": {
    "my_bank": {
      "name": "My Bank",                         // display name
      "short_name": "MyBank",                    // used in filename prefix
      "imap_search": {
        "sender_keywords": ["mybank.com"],       // match From header (domain boundary)
        "subject_keywords": ["e-Statement"],     // AND logic with sender
        "exclude_attachment_patterns": ["terms"] // skip attachments matching these
      },
      "doc_type_rules": [                        // first match wins
        {"keyword": "credit card", "type": "CreditCard"},
        {"keyword": "e-Statement", "type": "BankStatement"}
      ],
      "default_doc_type": "Statement",           // fallback doc type
      "subject_date_pattern": "(\\d{4})[-/](\\d{2})", // regex for YYYY/MM from subject
      "pdf_password": "",   // leave empty — use .env instead (see below)
      "zip_password": ""    // leave empty — use .env instead (see below)
    }
  },
  "global_settings": {
    "lookback_days": 60,    // scan window in days
    "retention_days": 180   // dedup record lifetime in days
  }
}
```

> Keys starting with `_` (e.g. `_example_en`) are ignored — use for disabled or template entries.
> See `config.example.json` for ready-to-use Taiwan bank configs.

**Filename format**: `{short_name}_{doc_type}_{YYYY}_{MM}.pdf`

Month is always zero-padded (`_02_` not `_2_`). `subject_date_pattern` captures raw digits; the fetcher normalises them automatically.

### Secret management

**All passwords belong in `.env`, not `config.json`.**

```
# .env example
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Per-bank passwords: {BANK_KEY_UPPERCASED}_{PDF_PASSWORD|ZIP_PASSWORD}
SINOPAC_PDF_PASSWORD=your-sinopac-pdf-password
CTBC_ZIP_PASSWORD=your-ctbc-zip-password
```

Env vars take precedence over `config.json`. Keeping `config.json` password-free means it is safe to share or version-control. The fetcher warns at startup if it detects passwords in `config.json`.

Both `config.json` and `.env` are excluded by `.gitignore`. Only `config.example.json` (no real passwords) should be committed.

---

## ZIP & PDF Password Support

Some banks deliver statements as password-protected ZIPs or PDFs.

**ZIP** (stdlib, no extra install):
```bash
# Set in .env
CTBC_ZIP_PASSWORD=your-zip-password
```

**PDF decryption** (requires pikepdf):
```bash
pip install pikepdf~=9.0
```
```bash
# Set in .env
SINOPAC_PDF_PASSWORD=your-pdf-password
```

Format: `{BANK_KEY_UPPERCASED}_{PDF_PASSWORD|ZIP_PASSWORD}` — takes precedence over `config.json`.

If `pikepdf` is not installed and a PDF password is set, the encrypted PDF is saved as-is with a warning.

---

## CLI Options

```
python fetcher.py [options]

  --config      path to config JSON                    (default: <script dir>/config.json)
  --output-dir  directory to save PDFs                 (default: <script dir>/downloads)
  --state-file  path to UID dedup store JSON           (default: <output-dir>/.processed_uids.json)
  --auth        imap | oauth                           (overrides AUTH_METHOD env var)
  --dry-run     preview matched emails without downloading
  --verbose     enable debug logging
  --version     print version and exit
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Completed successfully |
| 1 | Runtime error (IMAP/OAuth failure, config missing) |

---

## Cron / Scheduling

**Recommended: install python-dotenv**

```bash
pip install python-dotenv
```

The fetcher calls `load_dotenv()` automatically — no manual `export` needed in cron.

```bash
# Run daily at 09:00
0 9 * * * cd /path/to/gmail-statement-fetcher && python3 fetcher.py
```

**Without python-dotenv** (`export $(cat .env | xargs)` breaks on passwords with `$`, spaces, or `#`):

```bash
#!/bin/bash
# run_fetcher.sh
set -a
source "$(dirname "$0")/.env"
set +a
exec python3 "$(dirname "$0")/fetcher.py" "$@"
```

For OAuth on headless servers, set `OAUTH_TOKEN` to the full path of `token.json`.

---

## Security

- **Atomic writes**: all PDF saves use `tempfile.mkstemp` + `os.replace()` — no partial files
- **Privacy-safe dedup**: `.processed_uids.json` stores SHA-256 subject hashes, not raw subjects
- **Secret isolation**: passwords in `.env` only; startup warns if `config.json` contains secrets
- **Token permissions**: `token.json` saved at `0o600`
- **Username masking**: Gmail address logged as first 3 chars + `***`
- **Domain boundary matching**: sender matching uses `@`/`.` prefix to reduce false positives
- **ZIP bomb protection**: decompression capped at 100 MB (streaming guard, ignores header file_size)
- **Log injection prevention**: email subjects sanitised before logging

See [SECURITY.md](SECURITY.md) for the full security policy.

---

## AI Agent Integration

Standard CLI tool — any AI agent framework can invoke it via shell. `SKILL.md` is included for [OpenClaw](https://openclaw.ai/) integration.

```bash
# Dry-run first to confirm matches, then download
python3 fetcher.py --dry-run --verbose
python3 fetcher.py --output-dir ./downloads
```

---

## Part of the notoriouslab Pipeline

```
gmail-statement-fetcher   →  download PDF statements from Gmail
        ↓
   doc-cleaner             →  PDF/DOCX/XLSX → structured Markdown
        ↓
   personal-cfo            →  monthly audit + retirement glide path
```

Each tool works standalone. Together they form a full personal finance automation pipeline.

---

## Contributing

The easiest contribution is adding a bank config entry — no code changes needed:

1. Fork and create a branch: `git checkout -b add-<bank-name>`
2. Add an entry to `config.example.json`
3. Test with `python fetcher.py --dry-run`
4. Open a PR: `config: add <Bank Name>`

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT
