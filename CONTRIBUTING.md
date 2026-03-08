# Contributing

Contributions are welcome — bug reports, bank config additions, and pull requests.

## Adding a New Bank

The easiest contribution is adding a config entry for a bank not yet covered:

1. Fork the repo and create a branch: `git checkout -b add-<bank-name>`
2. Add an entry to `config.example.json` following the existing format
3. Test with `python fetcher.py --dry-run` to confirm matching works
4. Open a PR with the subject: `config: add <Bank Name>`

No code changes required for most banks.

## Bug Reports

Please include:
- Python version (`python --version`)
- Auth method (`imap` or `oauth`)
- Anonymised log output (`python fetcher.py --verbose 2>&1 | sed 's/[a-z0-9._%+-]*@[a-z0-9.-]*/[redacted]/g'`)
- What you expected vs. what happened

## Pull Requests

- Keep PRs focused — one fix or feature per PR
- `fetcher.py` must pass `python -m py_compile fetcher.py` with no errors
- Do not commit `.env`, `config.json`, `token.json`, or `credentials.json`
- Update `README.md` if you add CLI flags or config fields

## Development Setup

```bash
git clone https://github.com/notoriouslab/gmail-statement-fetcher.git
cd gmail-statement-fetcher
cp config.example.json config.json
cp .env.example .env
# edit config.json and .env, then:
python fetcher.py --dry-run --verbose
```
