# Contributing

Contributions are welcome — bug reports, bank config additions, and pull requests.

貢獻方式：bug 回報、新增銀行設定、Pull Request。

## Adding a New Bank / 新增銀行

The easiest contribution — no code changes required:

最簡單的貢獻方式 — 不需要改程式碼：

1. Fork the repo and create a branch: `git checkout -b add-<bank-name>`
2. Add an entry to `config.example.json` following the existing format
3. Test with `python fetcher.py --dry-run` to confirm matching works
4. Open a PR with the subject: `config: add <Bank Name>`

## Bug Reports / 回報問題

Please include:
- Python version (`python --version`)
- Auth method (`imap` or `oauth`)
- Anonymised log output:
  ```bash
  python fetcher.py --verbose 2>&1 | sed 's/[a-z0-9._%+-]*@[a-z0-9.-]*/[redacted]/g; s|/Users/[^/]*/|/Users/[redacted]/|g'
  ```
- What you expected vs. what happened

## Pull Requests

- Keep PRs focused — one fix or feature per PR
- `fetcher.py` must pass `python -m py_compile fetcher.py` with no errors
- Do not commit `.env`, `config.json`, `token.json`, or `credentials.json`
- Update `README.md` if you add CLI flags or config fields

## Development Setup / 開發環境

```bash
git clone https://github.com/notoriouslab/gmail-statement-fetcher.git
cd gmail-statement-fetcher
cp config.example.json config.json
cp .env.example .env
# Edit .env — fill in GMAIL_USER and GMAIL_APP_PASSWORD
# 編輯 .env — 填入 GMAIL_USER 和 GMAIL_APP_PASSWORD

# IMAP mode needs no pip install (stdlib only)
# Optional: pip install pikepdf         # PDF decryption
# Optional: pip install python-dotenv   # Auto-load .env

# Test
python fetcher.py --dry-run --verbose
```
