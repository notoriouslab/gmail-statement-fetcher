# Security Policy

## Supported Versions

Only the latest release on the `main` branch receives security fixes.

## Sensitive Files — Never Commit These

| File | Contains |
|---|---|
| `.env` | Gmail credentials |
| `credentials.json` | OAuth client secret |
| `token.json` | OAuth refresh token |
| `config.json` | May contain `pdf_password` / `zip_password` |

All of the above are excluded by `.gitignore`. Double-check before pushing.

## Known Architectural Limitations

**Sender spoofing**: Email `From` headers can be forged. This tool matches senders
by substring (e.g. `sinopac.com`), which a spoofed sender could satisfy.
The tool relies on Gmail's spam/phishing filters as the first line of defense.

Mitigations already in place:
- ZIP decompression is capped at 100 MB to prevent decompression bombs
- `token.json` is saved with `0o600` permissions
- Attachment filenames are sanitised before being logged

Do **not** run this tool against mailboxes that have spam filtering disabled,
or point `--output-dir` at a location that auto-executes downloaded files.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email the maintainer directly (see profile) or open a
[private security advisory](https://github.com/notoriouslab/gmail-statement-fetcher/security/advisories/new).

Expect a response within 72 hours.
