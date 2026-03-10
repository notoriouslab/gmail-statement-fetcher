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

**Sender spoofing**: Email `From` headers can be forged. Sender matching uses
domain boundary checks (`@` or `.` prefix) to reduce false positives, but a
spoofed sender with a matching domain could still pass.
The tool relies on Gmail's spam/phishing filters as the first line of defense.

Mitigations already in place:
- ZIP decompression is capped at 100 MB (streaming guard) to prevent decompression bombs
- `token.json` is saved with `0o600` permissions
- Attachment filenames are sanitised before being logged
- PDF writes use atomic write (`tempfile.mkstemp` + `os.replace`) to prevent partial files
- `.processed_uids.json` stores subject hashes (SHA-256), not raw email subjects
- Gmail username is masked in log output (first 3 chars only)
- IMAP socket timeout (300s) prevents indefinite hangs on network failure
- Config password warning: startup warns if `pdf_password`/`zip_password` are in `config.json`

Do **not** run this tool against mailboxes that have spam filtering disabled,
or point `--output-dir` at a location that auto-executes downloaded files.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email the maintainer directly (see profile) or open a
[private security advisory](https://github.com/notoriouslab/gmail-statement-fetcher/security/advisories/new).

Expect a response within 72 hours.
