# gmail-statement-fetcher

Automatically download bank/financial statement PDFs from Gmail — config-driven, deduplication built-in, dual IMAP/OAuth support.

自動從 Gmail 下載銀行對帳單 PDF，設定驅動、內建去重、支援 IMAP 與 OAuth 雙模式。

**Requires Python 3.9+** · Part of the [notoriouslab](https://github.com/notoriouslab) open-source toolkit.

---

## Why This Tool / 為什麼選這個

Most Gmail-based statement tools are one-off scripts tied to a single bank.
This one is **config-driven**: add any bank without touching code.
大多數 Gmail 對帳單工具都是單一銀行的臨時腳本。這個工具是**設定驅動**的：新增任何銀行都不需要修改程式碼。

| | gmail-statement-fetcher |
|---|---|
| Multi-bank / 多銀行 | ✅ JSON config, no code changes |
| Deduplication / 去重 | ✅ UID-based, never re-downloads |
| IMAP (headless) | ✅ stdlib only, zero install |
| OAuth 2.0 | ✅ `gmail.readonly` scope |
| ZIP extraction / 解壓縮 | ✅ stdlib, ZIP bomb–protected |
| PDF decryption / PDF 解密 | ✅ optional pikepdf |
| Normalized filenames | ✅ `永豐銀行_信用卡對帳單_2026_02.pdf` |
| Dry-run preview | ✅ `--dry-run` |
| Security hardened | ✅ token 0o600, log sanitisation |

---

## Features / 功能特色

- **Dual auth / 雙重認證**: IMAP + App Password (headless servers) or OAuth 2.0 `gmail.readonly` (personal use)
- **Config-driven rules / 設定驅動規則**: add any bank without touching code — sender keywords, subject keywords, doc type rules all in JSON
- **Normalized filenames / 標準化檔名**: `永豐銀行_信用卡對帳單_2026_02.pdf` — readable, sortable, dedup-friendly
- **Smart date extraction / 智慧日期擷取**: extracts statement period from subject line (e.g. `2026年2月`) before falling back to email Date header
- **Deduplication / 去重機制**: UID-based, never downloads the same email twice; pruned automatically after `retention_days`
- **ZIP extraction / ZIP 解壓縮**: stdlib `zipfile`, supports per-bank `zip_password`, ZIP bomb–protected (100 MB cap)
- **PDF decryption / PDF 解密**: optional `pikepdf`; supports per-bank `pdf_password`; skips gracefully if pikepdf not installed
- **Dry-run mode / 預覽模式**: `--dry-run` shows matched emails and filenames without writing anything
- **Security hardened / 安全強化**: `token.json` saved at `0o600`, log injection stripped, ZIP decompression capped at 100 MB
- **Zero stdlib-only for IMAP mode**: no `pip install` needed for basic use

---

## Quick Start / 快速開始

```bash
# 1. Clone
git clone https://github.com/notoriouslab/gmail-statement-fetcher.git
cd gmail-statement-fetcher

# 2. Copy and edit config / 複製並編輯設定
cp config.example.json config.json
# Edit config.json — add your bank's sender domain and subject keywords
# 編輯 config.json — 新增你銀行的寄件人網域與主旨關鍵字

# 3. Set credentials / 設定認證資訊
cp .env.example .env
# Edit .env — fill in GMAIL_USER and GMAIL_APP_PASSWORD
# 編輯 .env — 填入 GMAIL_USER 和 GMAIL_APP_PASSWORD

# 4a. IMAP mode — no extra install needed / IMAP 模式，無需額外安裝
pip install python-dotenv   # optional but recommended / 選裝，裝了就不用手動 export
python3 fetcher.py

# 4b. OAuth mode — install dependencies first / OAuth 模式，先安裝依賴
pip install google-auth-oauthlib google-api-python-client python-dotenv
# Then set AUTH_METHOD=oauth in .env, place credentials.json in the project root
# 在 .env 設定 AUTH_METHOD=oauth，並將 credentials.json 放在專案根目錄
python3 fetcher.py

# Output / 輸出: ./downloads/永豐銀行_銀行對帳單_2026_02.pdf
```

Preview matched emails without downloading / 預覽匹配信件不下載：

```bash
python3 fetcher.py --dry-run --verbose
```

---

## Authentication / 認證方式

### IMAP + App Password — recommended for servers / 伺服器推薦

適合 cron 排程、無頭伺服器，無需瀏覽器，純標準函式庫。

1. Enable 2FA on your Google account / 啟用 Google 帳號的兩步驗證
2. Go to **Google Account → Security → App Passwords** / 前往**安全性 → 應用程式密碼**
3. Create an App Password for "Mail" / 為「郵件」建立應用程式密碼
4. Set in `.env`: `AUTH_METHOD=imap`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`

### OAuth 2.0 — recommended for personal use / 個人使用推薦

使用 `gmail.readonly` 最小權限範圍，更安全但需要第一次瀏覽器授權。

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/) / 在 Google Cloud Console 建立專案
2. Enable the **Gmail API** / 啟用 Gmail API
3. Create OAuth credentials (Desktop app) → download `credentials.json` → **place it in the project root** / 建立 OAuth 憑證（桌面應用程式）→ 下載 `credentials.json` → **放在專案根目錄**
4. Install dependencies / 安裝依賴：`pip install google-auth-oauthlib google-api-python-client`
5. Set `AUTH_METHOD=oauth` in `.env`
6. First run opens a browser for authorization → generates `token.json` / 第一次執行會開啟瀏覽器授權 → 產生 `token.json`

> **Headless servers / 無頭伺服器**: After the first OAuth run on a local machine, copy `token.json` to your server and set `OAUTH_TOKEN=/path/to/token.json`. Keep this file backed up — losing it requires re-authorization.
>
> 在本機完成第一次 OAuth 授權後，將 `token.json` 複製到伺服器，並設定 `OAUTH_TOKEN=/path/to/token.json`。請備份此檔案，遺失後需重新授權。

---

## Config Reference / 設定說明

```jsonc
{
  "banks": {
    "my_bank": {
      "name": "My Bank",                        // display name / 顯示名稱
      "short_name": "MyBank",                   // used in filename / 用於檔名前綴
      "imap_search": {
        "sender_keywords": ["mybank.com"],      // match From header / 比對寄件人
        "subject_keywords": ["e-Statement"],    // AND logic with sender / 與寄件人 AND
        "exclude_attachment_patterns": ["terms"] // skip matching attachments / 跳過匹配附件
      },
      "doc_type_rules": [                       // first match wins / 第一個匹配優先
        {"keyword": "credit card", "type": "CreditCard"},
        {"keyword": "e-Statement", "type": "BankStatement"}
      ],
      "default_doc_type": "Statement",          // fallback / 預設類型
      "subject_date_pattern": "(\\d{4})年(\\d{1,2})月", // regex for YYYY/MM / 擷取日期用 regex
      "pdf_password": "",   // optional / 選填，PDF 密碼
      "zip_password": ""    // optional / 選填，ZIP 密碼
    }
  },
  "global_settings": {
    "lookback_days": 60,    // scan window / 掃描天數
    "retention_days": 180   // dedup record lifetime / 去重記錄保留天數
  }
}
```

> **Bank key naming / 銀行 key 命名**: Keys starting with `_` (e.g. `_example_en`) are ignored by the fetcher — use this for disabled or template entries.
> 以 `_` 開頭的 key（如 `_example_en`）會被忽略，可用於停用或範本條目。
> See `config.example.json` for ready-to-use Taiwan bank configs / 參見 `config.example.json` 內含現成的台灣銀行設定。

**Filename format / 檔名格式**: `{short_name}_{doc_type}_{YYYY}_{MM}.pdf`

Month is always zero-padded (`_02_` not `_2_`). `subject_date_pattern` captures raw digits; the fetcher normalises them automatically.
月份固定補零（`_02_` 而非 `_2_`）。`subject_date_pattern` 擷取原始數字，程式自動補零。

Examples / 範例:
- `永豐銀行_銀行對帳單_2026_02.pdf`
- `永豐銀行_信用卡對帳單_2026_02.pdf`
- `MyBank_CreditCard_2026_02.pdf`

> **Note**: Each `short_name` must be unique across banks, as it is used as the filename prefix.
> If a bank re-sends a statement (e.g. a correction), the replacement email has a different UID and will be downloaded again — this is intentional.
>
> **注意**：每個 `short_name` 必須唯一，因為它用作檔名前綴。
> 若銀行補發對帳單（例如更正版），補發郵件的 UID 不同，會被重新下載 — 此為預期行為。

---

## CLI Options / 命令列選項

```
python fetcher.py [options]

  --config      path to config JSON                    (default: <script dir>/config.json)
                設定檔路徑

  --output-dir  directory to save PDFs                 (default: <script dir>/downloads)
                PDF 儲存目錄

  --state-file  path to UID dedup store JSON           (default: <output-dir>/.processed_uids.json)
                UID 去重狀態檔路徑（output-dir 唯讀時使用此選項）

  --auth        imap | oauth                           (overrides AUTH_METHOD env var)
                認證方式（覆蓋 .env 中的 AUTH_METHOD）

  --dry-run     preview matched emails/filenames without downloading anything
                預覽匹配結果，不實際下載

  --verbose     enable debug logging
                啟用除錯日誌

  --version     print version and exit
                顯示版本並退出
```

---

## Cron / 自動排程

**Recommended: install python-dotenv / 推薦：安裝 python-dotenv**

```bash
pip install python-dotenv
```

The fetcher calls `load_dotenv()` automatically — no manual `export` needed in cron.
安裝後，`fetcher.py` 會自動讀取 `.env`，cron 裡不需要手動 `export`。

```bash
# Run daily at 09:00 (Linux/macOS) / 每天 09:00 自動執行
0 9 * * * cd /path/to/gmail-statement-fetcher && python3 fetcher.py
```

**Without python-dotenv / 未安裝 python-dotenv**

> ⚠️  `export $(cat .env | xargs)` breaks when values contain spaces, `$`, or `#`.
> Use a wrapper script instead / 請改用包裝腳本：

```bash
#!/bin/bash
# run_fetcher.sh
set -a
# shellcheck source=.env
source "$(dirname "$0")/.env"
set +a
exec python3 "$(dirname "$0")/fetcher.py" "$@"
```

```bash
# crontab / 排程
0 9 * * * /path/to/gmail-statement-fetcher/run_fetcher.sh
```

`source .env` honours shell quoting, so passwords with `$`, spaces, or `#` are safe.
`source .env` 遵守 shell 引用規則，密碼包含 `$`、空格或 `#` 都不會出問題。

For Oracle/headless servers using OAuth, set `OAUTH_TOKEN` to the full path of `token.json`.
使用 OAuth 的無頭伺服器請設定 `OAUTH_TOKEN` 指向 `token.json` 的完整路徑。

---

## ZIP & PDF Password Support / ZIP 與 PDF 密碼支援

Some banks deliver statements as password-protected ZIPs or PDFs.
部分銀行會以密碼保護的 ZIP 或 PDF 寄送對帳單。

**ZIP** (stdlib, no install needed / 無需額外安裝):
```jsonc
"zip_password": "your-zip-password"
```

**PDF decryption** (requires pikepdf / 需安裝 pikepdf):
```bash
pip install pikepdf~=9.0
```
```jsonc
"pdf_password": "your-pdf-password"
```

If `pikepdf` is not installed and a `pdf_password` is set, the encrypted PDF is saved as-is with a warning.
若未安裝 `pikepdf` 但設定了 `pdf_password`，加密 PDF 會照原樣儲存並顯示警告。

> ⚠️ **`config.json` contains passwords — do NOT commit it to git.**
> It is already listed in `.gitignore`. Only `config.example.json` (no real passwords) should be version-controlled.
>
> ⚠️ **`config.json` 包含密碼，請勿 commit 到 git。**
> 此檔案已列入 `.gitignore`。只有 `config.example.json`（無真實密碼）應該進版本控制。

---

## Security / 安全性

- `token.json` is saved with `0o600` permissions / 以 `0o600` 權限儲存
- ZIP decompression capped at 100 MB to prevent ZIP bombs / ZIP 解壓縮上限 100 MB，防止 ZIP 炸彈
- Email subjects are sanitised before logging to prevent log injection / 郵件主旨記錄前會清除控制字元，防止 log 注入
- Sender matching relies on Gmail's spam/phishing filters as first-line defense / 寄件人比對依賴 Gmail 的垃圾郵件過濾作為第一道防線

See [SECURITY.md](SECURITY.md) for the full security policy.
完整安全政策請見 [SECURITY.md](SECURITY.md)。

---

## Part of the notoriouslab Pipeline / 組合拳

```
gmail-statement-fetcher   →  PDF downloads / PDF 下載
        ↓
   doc-cleaner             →  PDF/DOCX/XLSX → structured Markdown / 結構化 Markdown
        ↓
   personal-cfo            →  monthly audit + retirement glide path / 月度審計 + 退休滑翔路徑
```

Each tool works standalone. Together they form a full personal finance automation pipeline.
每個工具可獨立使用。合併使用則構成完整的個人財務自動化流水線。

---

## Contributing / 貢獻

The easiest contribution is adding a bank config entry. See [CONTRIBUTING.md](CONTRIBUTING.md).
最簡單的貢獻方式是新增銀行設定條目，詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## License

MIT
