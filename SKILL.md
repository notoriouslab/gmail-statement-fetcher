---
name: gmail-statement-fetcher
description: Automatically download bank/financial statement PDFs from Gmail via IMAP or OAuth. Config-driven bank rules, UID dedup, ZIP extraction, PDF decryption.
metadata: {"openclaw":{"emoji":"📬","version":"1.0.1","homepage":"https://github.com/notoriouslab/gmail-statement-fetcher","requires":{"bins":["python3"],"env":["GMAIL_USER","GMAIL_APP_PASSWORD"]}}}
---

# gmail-statement-fetcher

Automatically download bank/financial statement PDFs from Gmail.

## When to use

- User asks to download bank statements from Gmail
- User wants to set up automated statement fetching
- User asks to fetch encrypted/password-protected PDF statements
- User wants to extract PDFs from ZIP attachments in email

## Commands

### Fetch statements (IMAP mode, default)
```bash
GMAIL_USER="user@gmail.com" GMAIL_APP_PASSWORD="xxxx" python3 {baseDir}/fetcher.py
```

### Dry run — preview matches without downloading
```bash
GMAIL_USER="user@gmail.com" GMAIL_APP_PASSWORD="xxxx" python3 {baseDir}/fetcher.py --dry-run --verbose
```

### Custom config and output directory
```bash
python3 {baseDir}/fetcher.py --config "{{config_path}}" --output-dir "{{output_dir}}"
```

### OAuth mode
```bash
AUTH_METHOD=oauth python3 {baseDir}/fetcher.py
```

## Options

| Flag | Description |
|---|---|
| `--config` | Path to config JSON (default: `<script dir>/config.json`) |
| `--output-dir` | Directory to save PDFs (default: `<script dir>/downloads`) |
| `--state-file` | Path to UID dedup store (default: `<output-dir>/.processed_uids.json`) |
| `--auth` | `imap` or `oauth` (overrides `AUTH_METHOD` env var) |
| `--dry-run` | Preview matched emails without downloading |
| `--verbose` | Enable debug logging |

## Environment variables

| Variable | Mode | Description |
|---|---|---|
| `GMAIL_USER` | IMAP | Gmail address |
| `GMAIL_APP_PASSWORD` | IMAP | Gmail App Password |
| `AUTH_METHOD` | Both | `imap` (default) or `oauth` |
| `OAUTH_CREDENTIALS` | OAuth | Path to `credentials.json` from GCP Console |
| `OAUTH_TOKEN` | OAuth | Path to `token.json` (auto-generated) |
| `{BANKID}_PDF_PASSWORD` | Both | PDF decryption password for a specific bank (e.g. `SINOPAC_PDF_PASSWORD`). Takes precedence over `pdf_password` in config.json. |
| `{BANKID}_ZIP_PASSWORD` | Both | ZIP extraction password for a specific bank (e.g. `CTBC_ZIP_PASSWORD`). Takes precedence over `zip_password` in config.json. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Completed successfully |
| 1 | Runtime error (IMAP/OAuth failure, config missing) |

## Notes

- IMAP mode uses Python stdlib only — no pip install needed
- Bank matching rules are config-driven — add banks via `config.json`, no code changes
- UID dedup prevents re-downloading already-processed emails across runs
- Supports password-protected PDFs (via pikepdf) and ZIP attachments
- ZIP extraction includes streaming bomb guard (100 MB cap)
- `.processed_uids.json` stores hashed subjects only (no PII)
