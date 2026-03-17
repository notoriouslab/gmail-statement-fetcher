# gmail-statement-fetcher

自動從 Gmail 下載銀行對帳單 PDF，設定驅動、內建去重、支援 IMAP 與 OAuth 雙模式。

**需要 Python 3.9+** · 屬於 [notoriouslab](https://github.com/notoriouslab) 開源工具組的一員。

> [English README](README.en.md)

---

## 為什麼需要這個工具

大多數 Gmail 對帳單工具都是單一銀行的臨時腳本，換一家銀行就得改程式碼。這個工具是**設定驅動**的：新增任何銀行只要在 JSON 加一個 entry，不需要動程式碼。任何 AI agent 框架都可以透過 shell 呼叫，附帶 `SKILL.md` 讓 [OpenClaw](https://openclaw.ai/) 直接整合使用。

| 特色 | 說明 |
|------|------|
| 多銀行支援 | JSON 設定驅動，新增銀行不改程式碼 |
| 去重機制 | UID-based，同一封信永遠只下載一次 |
| IMAP 模式 | 純標準函式庫，零安裝，適合無頭伺服器 |
| OAuth 2.0 | `gmail.readonly` 最小權限範圍 |
| ZIP 解壓縮 | 標準函式庫，含 ZIP bomb 防護（100 MB 上限） |
| PDF 解密 | 選裝 pikepdf，密碼存 `.env` 不進 config |
| 標準化檔名 | `永豐銀行_信用卡對帳單_2026_02.pdf` |
| 預覽模式 | `--dry-run` 先看匹配結果再下載 |
| 原子寫入 | `tempfile` + `os.replace()`，不產生半殘檔案 |
| 隱私安全去重 | 去重記錄只存主旨 SHA-256 雜湊，不存原始主旨 |
| 安全強化 | token 0o600 權限、日誌遮罩帳號、log injection 防護 |

---

## 快速開始

```bash
# 1. 下載
git clone https://github.com/notoriouslab/gmail-statement-fetcher.git
cd gmail-statement-fetcher

# 2. 複製並編輯設定
cp config.example.json config.json
# 編輯 config.json — 新增你銀行的寄件人網域與主旨關鍵字

# 3. 設定認證資訊
cp .env.example .env
# 編輯 .env — 填入 GMAIL_USER 和 GMAIL_APP_PASSWORD

# 4a. IMAP 模式（不需要額外安裝）
pip install python-dotenv   # 選裝，裝了就不用手動 export
python3 fetcher.py

# 4b. OAuth 模式（需安裝依賴）
pip install google-auth-oauthlib google-api-python-client python-dotenv
# 在 .env 設定 AUTH_METHOD=oauth，並將 credentials.json 放在專案根目錄
python3 fetcher.py

# 輸出：./downloads/永豐銀行_銀行對帳單_2026_02.pdf
```

預覽匹配結果不實際下載：

```bash
python3 fetcher.py --dry-run --verbose
```

---

## 認證方式

### IMAP + App Password — 伺服器推薦

適合 cron 排程、無頭伺服器，無需瀏覽器，純標準函式庫。

1. 啟用 Google 帳號的兩步驗證
2. 前往**安全性 → 應用程式密碼**，為「郵件」建立應用程式密碼
3. 在 `.env` 設定：`AUTH_METHOD=imap`、`GMAIL_USER`、`GMAIL_APP_PASSWORD`

### OAuth 2.0 — 個人使用推薦

使用 `gmail.readonly` 最小權限範圍，更安全但需要第一次瀏覽器授權。

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 建立專案
2. 啟用 Gmail API
3. 建立 OAuth 憑證（桌面應用程式）→ 下載 `credentials.json` → **放在專案根目錄**
4. 安裝依賴：`pip install google-auth-oauthlib google-api-python-client`
5. 在 `.env` 設定 `AUTH_METHOD=oauth`
6. 第一次執行會開啟瀏覽器授權 → 產生 `token.json`

> **無頭伺服器**：在本機完成第一次 OAuth 授權後，將 `token.json` 複製到伺服器並設定 `OAUTH_TOKEN=/path/to/token.json`。請備份此檔案，遺失後需重新授權。

---

## 設定說明

### 設定檔格式

```jsonc
{
  "banks": {
    "my_bank": {
      "name": "My Bank",                         // 顯示名稱
      "short_name": "MyBank",                    // 用於檔名前綴
      "imap_search": {
        "sender_keywords": ["mybank.com"],       // 比對寄件人（域名邊界）
        "subject_keywords": ["e-Statement"],     // 與寄件人 AND
        "exclude_attachment_patterns": ["terms"] // 跳過包含此關鍵字的附件
      },
      "doc_type_rules": [                        // 第一個匹配優先
        {"keyword": "credit card", "type": "CreditCard"},
        {"keyword": "e-Statement", "type": "BankStatement"}
      ],
      "default_doc_type": "Statement",           // 預設文件類型
      "subject_date_pattern": "(\\d{4})年(\\d{1,2})月", // 從主旨擷取年月
      "pdf_password": "",   // 留空，改用 .env（見下方）
      "zip_password": ""    // 留空，改用 .env（見下方）
    }
  },
  "global_settings": {
    "lookback_days": 60,    // 掃描最近幾天的信件
    "retention_days": 180   // 去重記錄保留天數
  }
}
```

> 以 `_` 開頭的 key（如 `_example_en`）會被忽略，可用於停用或範本條目。
> 參見 `config.example.json` 內含現成的台灣銀行設定。

**檔名格式**：`{short_name}_{doc_type}_{YYYY}_{MM}.pdf`

月份固定補零（`_02_` 而非 `_2_`）。`subject_date_pattern` 擷取原始數字，程式自動補零。

### 機密管理

**所有密碼只能放在 `.env`，不可放在 `config.json`**。

```
# .env 範例
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# 銀行 PDF / ZIP 密碼：格式 = {銀行 key 大寫}_{PDF_PASSWORD|ZIP_PASSWORD}
SINOPAC_PDF_PASSWORD=your-sinopac-pdf-password
CTBC_ZIP_PASSWORD=your-ctbc-zip-password
```

env var 優先順序高於 `config.json`。`config.json` 的密碼欄位留空，設定檔就可以安全分享或進版本控制。程式啟動時若偵測到 `config.json` 含密碼會自動警告。

`config.json` 和 `.env` 都已加入 `.gitignore`，只有 `config.example.json`（無真實密碼）應該進版本控制。

---

## ZIP 與 PDF 密碼

部分銀行以密碼保護的 ZIP 或 PDF 寄送對帳單。

**ZIP**（標準函式庫，不需額外安裝）：
```bash
# 在 .env 設定
CTBC_ZIP_PASSWORD=your-zip-password
```

**PDF 解密**（需安裝 pikepdf）：
```bash
pip install pikepdf~=9.0
```
```bash
# 在 .env 設定
SINOPAC_PDF_PASSWORD=your-pdf-password
```

格式：`{銀行 key 大寫}_{PDF_PASSWORD|ZIP_PASSWORD}`，優先於 `config.json` 中的設定。

若未安裝 `pikepdf` 但設定了 PDF 密碼，加密 PDF 會照原樣儲存並顯示警告。

---

## 命令列選項

```
python fetcher.py [選項]

  --config      設定檔路徑（預設：<程式目錄>/config.json）
  --output-dir  PDF 儲存目錄（預設：<程式目錄>/downloads）
  --state-file  UID 去重狀態檔路徑（output-dir 唯讀時使用此選項）
  --auth        imap | oauth（覆蓋 AUTH_METHOD env var）
  --dry-run     預覽匹配結果，不實際下載
  --verbose     啟用除錯日誌
  --version     顯示版本並退出
```

### Exit Code

| Code | 意義 |
|------|------|
| 0 | 執行完成 |
| 1 | 執行錯誤（IMAP/OAuth 失敗、設定遺失） |

---

## Cron 自動排程

**推薦安裝 python-dotenv**：

```bash
pip install python-dotenv
```

安裝後，`fetcher.py` 會自動讀取 `.env`，cron 裡不需要手動 `export`。

```bash
# 每天 09:00 自動執行
0 9 * * * cd /path/to/gmail-statement-fetcher && python3 fetcher.py
```

**未安裝 python-dotenv 時，請用包裝腳本**（`export $(cat .env | xargs)` 遇到特殊字元會爆）：

```bash
#!/bin/bash
# run_fetcher.sh
set -a
source "$(dirname "$0")/.env"
set +a
exec python3 "$(dirname "$0")/fetcher.py" "$@"
```

使用 OAuth 的無頭伺服器請設定 `OAUTH_TOKEN` 指向 `token.json` 的完整路徑。

---

## 安全性

- **原子寫入**：所有 PDF 使用 `tempfile.mkstemp` + `os.replace()`，不產生半殘檔案
- **隱私安全去重**：`.processed_uids.json` 只存主旨 SHA-256 雜湊，不存原始主旨
- **機密隔離**：密碼只在 `.env`，啟動時自動檢查 `config.json` 是否不小心放了 secret
- **token 權限**：`token.json` 以 `0o600` 儲存，防止多人系統上的意外讀取
- **帳號遮罩**：日誌中 Gmail 帳號只顯示前 3 字元
- **域名邊界比對**：寄件人使用 `@`/`.` 邊界防止誤匹配
- **ZIP bomb 防護**：解壓縮上限 100 MB（串流計算，不信任 header 中的 file_size）
- **log injection 防護**：主旨記錄前清除 ASCII 控制字元

詳細安全政策請見 [SECURITY.md](SECURITY.md)。

---

## AI Agent 整合（OpenClaw 等）

標準 CLI 工具，任何 AI agent 框架都可以透過 shell 呼叫。附帶 `SKILL.md` 讓 [OpenClaw](https://openclaw.ai/) 直接整合。

```bash
# Dry-run 先確認匹配，再實際下載
python3 fetcher.py --dry-run --verbose
python3 fetcher.py --output-dir ./downloads
```

---

## notoriouslab 組合拳

```
gmail-statement-fetcher   →  從 Gmail 自動下載 PDF 對帳單
        ↓
   doc-cleaner             →  PDF/DOCX/XLSX → 結構化 Markdown
        ↓
   personal-cfo            →  月度審計 + 退休軌道監控
```

每個工具可獨立使用。合併使用則構成完整的個人財務自動化流水線。

---

## 貢獻

最簡單的貢獻方式是新增銀行設定條目，不需要改程式碼：

1. Fork 並建立分支：`git checkout -b add-<bank-name>`
2. 在 `config.example.json` 加入 entry
3. 用 `python fetcher.py --dry-run` 確認匹配正確
4. 開 PR，標題格式：`config: add <Bank Name>`

詳見 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 授權

MIT
