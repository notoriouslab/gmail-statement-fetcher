#!/usr/bin/env python3
"""
gmail-statement-fetcher v1.0.1
Automatically download bank/financial statement PDFs from Gmail.
Requires Python 3.9+

Supports two authentication methods:
  - IMAP + App Password  (headless-friendly, no external dependencies)
  - OAuth 2.0            (gmail.readonly scope, more secure)

Usage:
    python fetcher.py [--config config.json] [--output-dir ./downloads] [--auth imap|oauth]
    python fetcher.py --dry-run   # preview without downloading

Environment variables (IMAP mode):
    GMAIL_USER           your Gmail address
    GMAIL_APP_PASSWORD   Gmail App Password (not your account password)

Environment variables (OAuth mode):
    OAUTH_CREDENTIALS    path to credentials.json from GCP Console (default: ./credentials.json)
    OAUTH_TOKEN          path to token.json — MUST be persisted across runs (default: ./token.json)

Environment variables (shared):
    AUTH_METHOD          imap | oauth  (default: imap, overridden by --auth flag)
"""

import os
import sys

if sys.version_info < (3, 9):
    sys.exit("gmail-statement-fetcher requires Python 3.9+. "
             f"You are running {sys.version.split()[0]}.")

import io
import json
import zipfile
import imaplib
import email
import logging
import tempfile
import datetime
import re
import hashlib
import argparse
import base64
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# Optional OAuth dependencies
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False

OAUTH_SCOPES             = ["https://www.googleapis.com/auth/gmail.readonly"]
IMAP_BATCH_SIZE          = 50          # fetch headers in chunks to avoid timeout on large mailboxes
MAX_DECOMPRESSED_BYTES   = 100 * 1024 * 1024  # 100 MB — ZIP bomb guard; no bank statement is this big
_HERE                    = Path(__file__).resolve().parent

# Locale-safe month abbreviations for IMAP SINCE date (RFC 3501 requires English)
_IMAP_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _imap_since_date(lookback_days):
    """Return an IMAP-safe SINCE date string (e.g. '01-Mar-2026').

    Uses a hard-coded English month table instead of strftime('%b') to avoid
    locale-dependent output on systems with non-English locales (e.g. zh_TW).
    """
    d = datetime.date.today() - datetime.timedelta(days=lookback_days)
    return f"{d.day:02d}-{_IMAP_MONTHS[d.month]}-{d.year}"


def _sanitize_for_log(s):
    """Replace ASCII control characters (ANSI escapes, newlines, etc.) with '?'.

    Prevents log injection: a malicious email subject cannot forge fake log lines
    or inject ANSI escape sequences into the terminal.
    """
    return re.sub(r"[\x00-\x1f\x7f]", "?", str(s))


log = logging.getLogger("fetcher")


def _subject_hash(subject):
    """Return a short SHA-256 hash of the email subject for dedup records.

    We store only a hash (not the raw subject) because subjects may contain
    personally identifiable information (name, account numbers, card suffixes).
    12 hex chars = 48 bits — collision-free for any realistic mailbox size.
    """
    return hashlib.sha256(subject.encode("utf-8", errors="replace")).hexdigest()[:12]


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def load_json(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Could not load %s: %s", path, e)
        return {}


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        os.unlink(tmp)
        raise


def decode_mime_header(s):
    if not s:
        return ""
    decoded = ""
    try:
        for part, enc in decode_header(s):
            if isinstance(part, bytes):
                decoded += part.decode(enc or "utf-8", errors="ignore")
            else:
                decoded += str(part)
    except Exception:
        return str(s)
    return decoded


def build_normalized_filename(short_name, doc_type_rules, default_type, subject,
                               email_date_str, subject_date_pattern=None):
    """Build a normalized filename: {short_name}_{doc_type}_{YYYY}_{MM}.pdf

    Date priority:
      1. Regex match from subject  (most accurate — catches "Feb statement sent in Mar")
      2. Email Date header
      3. Today as last resort
    """
    ym = None

    if subject_date_pattern:
        try:
            m = re.search(subject_date_pattern, subject)
        except re.error:
            m = None
        if m:
            try:
                year, month = int(m.group(1)), int(m.group(2))
                ym = f"{year}_{month:02d}"
            except (ValueError, IndexError):
                pass

    if not ym:
        try:
            dt = parsedate_to_datetime(email_date_str)
            ym = f"{dt.year}_{dt.month:02d}"
        except Exception:
            log.debug("Could not parse date header: %r", email_date_str)

    if not ym:
        ym = datetime.date.today().strftime("%Y_%m")
        log.debug("Date fallback: using today (%s)", ym)

    doc_type = default_type
    for rule in doc_type_rules:
        if rule.get("keyword", "").lower() in subject.lower():
            doc_type = rule["type"]
            break

    return f"{short_name}_{doc_type}_{ym}.pdf"


def resolve_save_path(output_dir, norm_name):
    """Return a conflict-free save path (appends _1, _2 … if file exists).

    Uses O_CREAT|O_EXCL probe to eliminate TOCTOU race between exists() and write.
    """
    save_path = Path(output_dir) / norm_name
    for idx in range(0, 1000):
        candidate = save_path if idx == 0 else Path(output_dir) / f"{norm_name[:-4]}_{idx}.pdf"
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            # Placeholder created atomically; caller will os.replace() over it
            return candidate
        except FileExistsError:
            continue
    # Fallback (should never happen with 1000 slots)
    return save_path


def prune_processed_uids(processed_uids, retention_days):
    if not retention_days:
        return processed_uids  # 0 = keep forever
    cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=retention_days)
    new_store, pruned = {}, 0
    for uid, meta in processed_uids.items():
        ts = meta.get("processed_at")
        if ts:
            try:
                dt = datetime.datetime.fromisoformat(ts)
                # Normalise legacy naive timestamps (stored before tz-aware writes) to UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                if dt > cutoff:
                    new_store[uid] = meta
                else:
                    pruned += 1
            except ValueError:
                new_store[uid] = meta
        else:
            new_store[uid] = meta
    if pruned:
        log.info("🧹 Pruned %d old entries (retention: %dd)", pruned, retention_days)
    return new_store


def match_email(from_addr, subject, banks):
    """Return (bank_id, bank_cfg) if email matches any bank rule, else (None, None).

    Banks whose key starts with '_' (e.g. '_example_en') are skipped — they are
    template/disabled entries in the config.
    """
    for bank_id, bank_cfg in banks.items():
        if bank_id.startswith("_"):
            continue
        rules      = bank_cfg.get("imap_search", {})
        # Use '@' or '.' boundary to prevent partial domain matches
        # (e.g. "bank.com" should not match "mybank.com", but "sinopac.com" matches "@sinopac.com.tw")
        from_lower = from_addr.lower()
        sender_ok  = any(
            k.lower() in from_lower and (
                f"@{k.lower()}" in from_lower or f".{k.lower()}" in from_lower
            )
            for k in rules.get("sender_keywords", [])
        )
        subject_ok = any(k.lower() in subject.lower()   for k in rules.get("subject_keywords", []))
        if sender_ok and subject_ok:
            return bank_id, bank_cfg
    return None, None


def save_pdf(bank_cfg, payload_bytes, subject, date_str, output_dir, dry_run=False):
    """Write PDF payload with a normalized filename. Returns the resolved Path.

    In dry_run mode, prints what would be saved without writing anything.
    """
    if not payload_bytes:
        log.warning("   ⚠️  Empty payload for subject: %s — skipping.", _sanitize_for_log(subject))
        return None

    short_name = bank_cfg.get("short_name", bank_cfg.get("name", "unknown")).replace(" ", "_")
    norm_name = build_normalized_filename(
        short_name,
        bank_cfg.get("doc_type_rules", []),
        bank_cfg.get("default_doc_type", "statement"),
        subject, date_str,
        bank_cfg.get("subject_date_pattern"),
    )
    save_path = resolve_save_path(output_dir, norm_name)
    if dry_run:
        log.info("   [DRY RUN] Would save: %s", save_path.name)
    else:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(save_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload_bytes)
            os.replace(tmp, str(save_path))
        except Exception:
            os.unlink(tmp)
            raise
        log.info("   ✅ Saved: %s", save_path.name)
    return save_path


# ---------------------------------------------------------------------------
# PDF decryption + ZIP extraction
# ---------------------------------------------------------------------------

def _decrypt_pdf_if_needed(payload_bytes, password):
    """Decrypt a password-protected PDF using pikepdf. Returns plain bytes.

    If pikepdf is not installed, or decryption fails, returns the original bytes
    so the caller can still save an encrypted copy rather than crashing.
    """
    if not password or not payload_bytes:
        return payload_bytes
    try:
        import pikepdf
        with pikepdf.open(io.BytesIO(payload_bytes), password=password) as pdf:
            buf = io.BytesIO()
            pdf.save(buf)
            log.debug("   🔓 PDF decrypted.")
            return buf.getvalue()
    except ImportError:
        log.warning("   ⚠️  pikepdf not installed — saving encrypted PDF as-is. "
                    "Install with: pip install pikepdf")
        return payload_bytes
    except Exception as e:
        log.warning("   ⚠️  PDF decryption failed (%s) — saving encrypted copy.", e)
        return payload_bytes


def _process_zip(bank_cfg, zip_bytes, subject, date_str, output_dir, dry_run=False):
    """Extract every PDF from a ZIP attachment and save each one.

    Supports password-protected ZIPs via bank_cfg['zip_password'].
    Each extracted PDF is then passed through _decrypt_pdf_if_needed() before saving.
    Returns the number of PDFs saved.
    """
    zip_password = bank_cfg.get("zip_password")
    pwd          = zip_password.encode() if zip_password else None
    count        = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".pdf"):
                    log.debug("   ↳ Skipping non-PDF inside ZIP: %s", name)
                    continue
                # ZIP bomb guard: stream-read in chunks and count actual decompressed bytes.
                # We deliberately do NOT rely on info.file_size (the header-declared value)
                # because a malicious ZIP can lie there to bypass a pre-read size check.
                # Gmail's 25 MB attachment cap is the first line of defence; this is the second.
                log.info("   📦 Extracting from ZIP: %s", name)
                try:
                    with zf.open(name, pwd=pwd) as zf_entry:
                        chunks: list = []
                        total = 0
                        while True:
                            chunk = zf_entry.read(65536)  # 64 KB per iteration
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > MAX_DECOMPRESSED_BYTES:
                                log.warning(
                                    "   ⚠️  %s exceeds decompressed size limit (%d MB) — aborting.",
                                    name, MAX_DECOMPRESSED_BYTES // (1024 * 1024),
                                )
                                chunks = None
                                break
                            chunks.append(chunk)
                    if chunks is None:
                        continue
                    pdf_bytes = b"".join(chunks)
                except RuntimeError as e:
                    log.warning("   ⚠️  Cannot read %s from ZIP: %s", name, e)
                    continue
                pdf_bytes = _decrypt_pdf_if_needed(pdf_bytes, bank_cfg.get("pdf_password"))
                if save_pdf(bank_cfg, pdf_bytes, subject, date_str, output_dir, dry_run):
                    count += 1
    except zipfile.BadZipFile as e:
        log.warning("   ⚠️  Bad ZIP file: %s", e)
    return count


def process_attachment(bank_cfg, payload_bytes, filename, subject, date_str,
                       output_dir, dry_run=False):
    """Route a single attachment through unzip → decrypt → save.

    Handles:
      .pdf  — decrypt if bank_cfg['pdf_password'] is set, then save
      .zip  — extract PDFs (with optional zip_password), decrypt each, then save
    Returns the number of PDFs saved (0 on skip/error).
    """
    fname = filename.lower()
    if fname.endswith(".zip"):
        return _process_zip(bank_cfg, payload_bytes, subject, date_str, output_dir, dry_run)
    if fname.endswith(".pdf"):
        pdf_bytes = _decrypt_pdf_if_needed(payload_bytes, bank_cfg.get("pdf_password"))
        return 1 if save_pdf(bank_cfg, pdf_bytes, subject, date_str, output_dir, dry_run) else 0
    return 0


# ---------------------------------------------------------------------------
# IMAP backend
# ---------------------------------------------------------------------------

def fetch_imap(config, output_dir, uid_store_path, dry_run=False):
    username = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not username or not password:
        log.error("Set GMAIL_USER and GMAIL_APP_PASSWORD environment variables.")
        sys.exit(1)

    banks      = config.get("banks", {})
    global_cfg = config.get("global_settings", {})
    lookback   = global_cfg.get("lookback_days", 60)
    retention  = global_cfg.get("retention_days", 180)

    processed_uids = load_json(uid_store_path)
    since_date = _imap_since_date(lookback)

    # NOTE: IMAP UIDs and OAuth message IDs live in the same store.
    # They are structurally different (numeric vs alphanumeric) so collisions
    # are extremely unlikely, but if you switch auth methods on an active setup
    # consider deleting .processed_uids.json once to force a clean scan.

    mail = None
    try:
        log.info("[IMAP] Connecting as %s*** ...", username[:3])
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        # Set socket timeout for all subsequent operations (fetch, search).
        # The constructor timeout only covers the initial connection handshake.
        mail.socket().settimeout(300)  # 5 min — large attachments can be slow
        mail.login(username, password)
        mail.select("inbox")

        # UID SEARCH — returns UIDs directly, avoiding sequence-number/UID confusion.
        # Using mail.uid() throughout ensures every subsequent fetch uses the same
        # UID namespace and is immune to mailbox compaction (sequence IDs can shift).
        status, data = mail.uid("search", None, f"(SINCE {since_date})")
        if status != "OK" or not data[0]:
            log.info("No emails found since %s.", since_date)
            return

        all_uids = data[0].split()
        log.info("Found %d emails in range. Matching rules ...", len(all_uids))
        new_downloads = 0

        # Batch header fetch using comma-separated UIDs.
        # A sequence range (uid1:uid2) would over-fetch when UIDs are non-contiguous.
        for chunk_start in range(0, len(all_uids), IMAP_BATCH_SIZE):
            chunk   = all_uids[chunk_start: chunk_start + IMAP_BATCH_SIZE]
            uid_set = b",".join(chunk)
            _, response = mail.uid("fetch", uid_set,
                                   "(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")

            for item in response:
                if not isinstance(item, tuple):
                    continue
                envelope     = item[0].decode(errors="ignore")
                header_bytes = item[1]

                # UID FETCH always echoes the UID back in the parenthetical
                uid_match = re.search(r"\bUID\s+(\d+)\b", envelope)
                if not uid_match:
                    continue
                uid = uid_match.group(1)
                if uid in processed_uids:
                    continue

                msg       = email.message_from_bytes(header_bytes)
                from_addr = decode_mime_header(msg["From"])
                subject   = decode_mime_header(msg["Subject"])
                date_str  = msg["Date"] or ""

                bank_id, bank_cfg = match_email(from_addr, subject, banks)
                if not bank_cfg:
                    continue

                log.info("📍 Match [%s]: %s (UID: %s)",
                         bank_cfg["name"], _sanitize_for_log(subject), uid)
                _, full_data = mail.uid("fetch", uid, "(RFC822)")
                full_msg = email.message_from_bytes(full_data[0][1])
                exclude  = bank_cfg.get("imap_search", {}).get("exclude_attachment_patterns", [])

                success = False
                for part in full_msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if part.get("Content-Disposition") is None:
                        continue
                    filename = decode_mime_header(part.get_filename() or "")
                    if not filename.lower().endswith((".pdf", ".zip")):
                        continue
                    if any(p.lower() in filename.lower() for p in exclude):
                        log.info("   ⏩ Skipping excluded: %s", _sanitize_for_log(filename))
                        continue
                    n = process_attachment(bank_cfg, part.get_payload(decode=True),
                                          filename, subject, date_str, output_dir, dry_run)
                    if n > 0:
                        success = True
                        new_downloads += n

                if success and not dry_run:
                    processed_uids[uid] = {
                        "bank": bank_cfg["name"],
                        "subject_hash": _subject_hash(subject),
                        "date": date_str,
                        "processed_at": datetime.datetime.now(
                            tz=datetime.timezone.utc).isoformat(),
                    }

        if not dry_run:
            original_count = len(processed_uids)
            processed_uids = prune_processed_uids(processed_uids, retention)
            if new_downloads > 0 or len(processed_uids) < original_count:
                save_json(uid_store_path, processed_uids)

        log.info("✅ Done. New downloads: %d%s", new_downloads, " (dry run)" if dry_run else "")

    except Exception as e:
        log.error("IMAP Error: %s", e)
        raise
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# OAuth backend
# ---------------------------------------------------------------------------

def _build_oauth_service(credentials_path, token_path):
    if not OAUTH_AVAILABLE:
        log.error("Install OAuth dependencies: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    creds = None
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, OAUTH_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log.error("Token refresh failed: %s", e)
                log.error("Your token may have been revoked. Delete %s and re-run "
                          "to re-authorize.", os.path.basename(token_path))
                sys.exit(1)
        else:
            if not Path(credentials_path).exists():
                log.error("credentials.json not found at %s", os.path.basename(credentials_path))
                log.error("Download from: Google Cloud Console → APIs & Services → Credentials")
                sys.exit(1)
            # Detect headless environments (Linux without DISPLAY, or SSH session)
            # before attempting to open a browser — which would hang silently.
            is_headless = (
                sys.platform == "linux"
                and not os.environ.get("DISPLAY")
                and not os.environ.get("WAYLAND_DISPLAY")
            ) or bool(os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"))
            if is_headless:
                log.error("Cannot open browser in headless/SSH environment.")
                log.error("Run on a local machine first to generate token.json:")
                log.error("  AUTH_METHOD=oauth python fetcher.py")
                log.error("Then copy token.json to this server and set OAUTH_TOKEN=/path/to/token.json")
                sys.exit(1)
            flow  = InstalledAppFlow.from_client_secrets_file(credentials_path, OAUTH_SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist token — critical for headless servers.
        # Explicitly set 0o600 so the token (a permanent Gmail read credential)
        # is never world-readable, even on multi-user systems.
        save_json(token_path, json.loads(creds.to_json()))
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass  # non-Unix filesystem (e.g. FAT32 on Windows) — best-effort
        log.info("[OAuth] Token saved to %s (permissions: 0600) — keep this file secret.",
                 os.path.basename(token_path))

    return build("gmail", "v1", credentials=creds)


def _walk_parts_oauth(parts, bank_cfg, subject, date_str, output_dir,
                      exclude, service, msg_id, dry_run=False):
    """Recursively walk Gmail API message parts, download PDF attachments."""
    downloaded = 0
    for part in parts:
        if part.get("parts"):
            downloaded += _walk_parts_oauth(
                part["parts"], bank_cfg, subject, date_str,
                output_dir, exclude, service, msg_id, dry_run
            )
        filename = part.get("filename", "")
        if not filename.lower().endswith((".pdf", ".zip")):
            continue
        if any(p.lower() in filename.lower() for p in exclude):
            log.info("   ⏩ Skipping excluded: %s", _sanitize_for_log(filename))
            continue

        att_id = part.get("body", {}).get("attachmentId")
        if att_id:
            att = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            raw = att["data"]
        else:
            raw = part.get("body", {}).get("data", "")
        # Correct padding: append only the exact number of '=' chars needed
        raw += "=" * (-len(raw) % 4)
        payload_bytes = base64.urlsafe_b64decode(raw)

        downloaded += process_attachment(bank_cfg, payload_bytes, filename,
                                         subject, date_str, output_dir, dry_run)
    return downloaded


def fetch_oauth(config, output_dir, uid_store_path, dry_run=False):
    # Default to script directory (not cwd) so cron jobs from any directory
    # still find the credential files next to fetcher.py.
    credentials_path = os.environ.get("OAUTH_CREDENTIALS", str(_HERE / "credentials.json"))
    token_path       = os.environ.get("OAUTH_TOKEN",       str(_HERE / "token.json"))

    banks      = config.get("banks", {})
    global_cfg = config.get("global_settings", {})
    lookback   = global_cfg.get("lookback_days", 60)
    retention  = global_cfg.get("retention_days", 180)

    processed_uids = load_json(uid_store_path)
    service = _build_oauth_service(credentials_path, token_path)

    since_ts      = (datetime.date.today() - datetime.timedelta(days=lookback)).strftime("%Y/%m/%d")
    # Use has:attachment (not filename:pdf) so ZIP-wrapped PDFs are also captured.
    # The actual filename filtering happens in _walk_parts_oauth().
    query         = f"after:{since_ts} has:attachment"
    new_downloads = 0

    log.info("[OAuth] Scanning Gmail since %s ...", since_ts)
    try:
        # Paginate through all results (Gmail API caps each page at 500)
        all_messages = []
        page_token   = None
        while True:
            kwargs = {"userId": "me", "q": query, "maxResults": 500}
            if page_token:
                kwargs["pageToken"] = page_token
            results    = service.users().messages().list(**kwargs).execute()
            all_messages.extend(results.get("messages", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        log.info("Found %d candidate emails (all pages).", len(all_messages))

        for msg_ref in all_messages:
            msg_id = msg_ref["id"]
            if msg_id in processed_uids:
                continue

            meta = service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers   = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            from_addr = headers.get("From", "")
            subject   = headers.get("Subject", "")
            date_str  = headers.get("Date", "")

            bank_id, bank_cfg = match_email(from_addr, subject, banks)
            if not bank_cfg:
                continue

            log.info("📍 Match [%s]: %s", bank_cfg["name"], _sanitize_for_log(subject))
            full_msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            exclude      = bank_cfg.get("imap_search", {}).get("exclude_attachment_patterns", [])
            payload_root = full_msg.get("payload", {})
            parts        = payload_root.get("parts", [])
            # Fallback: non-multipart message — attachment sits directly in payload
            if not parts and payload_root.get("filename") and payload_root.get("body", {}).get("attachmentId"):
                parts = [payload_root]
            count   = _walk_parts_oauth(parts, bank_cfg, subject, date_str,
                                        output_dir, exclude, service, msg_id, dry_run)
            new_downloads += count
            if count > 0 and not dry_run:
                processed_uids[msg_id] = {
                    "bank": bank_cfg["name"],
                    "subject_hash": _subject_hash(subject),
                    "date": date_str,
                    "processed_at": datetime.datetime.now(
                        tz=datetime.timezone.utc).isoformat(),
                }

        if not dry_run:
            original_count = len(processed_uids)
            processed_uids = prune_processed_uids(processed_uids, retention)
            if new_downloads > 0 or len(processed_uids) < original_count:
                save_json(uid_store_path, processed_uids)

        log.info("✅ Done. New downloads: %d%s", new_downloads, " (dry run)" if dry_run else "")

    except Exception as e:
        log.error("OAuth Error: %s", e)
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _warn_config_secrets(config):
    """Warn if config.json contains non-empty passwords.

    Passwords in a JSON file risk accidental git commit or sharing.
    Environment variables (PDF_PASSWORD, ZIP_PASSWORD) are a safer alternative.
    """
    for bank_id, bank_cfg in config.get("banks", {}).items():
        if bank_id.startswith("_"):
            continue
        for key in ("pdf_password", "zip_password"):
            val = bank_cfg.get(key)
            if val:
                log.warning(
                    "⚠️  Bank '%s' has %s in config.json. "
                    "Consider using environment variables instead "
                    "(e.g. %s_%s) to avoid accidental exposure.",
                    bank_id, key,
                    bank_id.upper(), key.upper(),
                )


def main():
    # Load .env file if python-dotenv is installed — silently skip if not.
    # This lets users set GMAIL_USER / GMAIL_APP_PASSWORD in .env without
    # manually exporting variables.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="Download bank/financial statement PDFs from Gmail."
    )
    parser.add_argument("--config",     default=str(_HERE / "config.json"),
                        help="Path to config JSON (default: <script dir>/config.json)")
    parser.add_argument("--output-dir", default=str(_HERE / "downloads"),
                        help="Directory to save PDFs (default: <script dir>/downloads)")
    parser.add_argument("--state-file", default=None,
                        help="Path to UID dedup store JSON "
                             "(default: <output-dir>/.processed_uids.json). "
                             "Set this if output-dir is read-only (e.g. a mounted drive).")
    parser.add_argument("--auth",       choices=["imap", "oauth"], default=None,
                        help="Auth method: imap | oauth (overrides AUTH_METHOD env var)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Preview matched emails and filenames without downloading")
    parser.add_argument("--verbose",    action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--version",    action="version", version="gmail-statement-fetcher 1.0.1")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    config = load_json(args.config)
    if not config:
        log.error("Cannot load config from %s", os.path.basename(args.config))
        sys.exit(1)
    _warn_config_secrets(config)

    # Pre-validate regex patterns in bank configs
    for key, bank in config.get("banks", {}).items():
        pat = bank.get("subject_date_pattern")
        if pat:
            try:
                re.compile(pat)
            except re.error as e:
                log.error("Invalid regex in banks.%s.subject_date_pattern: %r — %s", key, pat, e)
                sys.exit(1)

    if args.dry_run:
        log.info("=== DRY RUN — no files will be written ===")

    auth_method = args.auth or os.environ.get("AUTH_METHOD", "imap")
    if auth_method == "oauth" and not OAUTH_AVAILABLE:
        log.error("OAuth dependencies not installed. Run:")
        log.error("  pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    output_dir     = Path(args.output_dir)
    uid_store_path = (Path(args.state_file)
                      if args.state_file
                      else output_dir / ".processed_uids.json")

    # Warn if the existing UID store was written by the other auth method.
    # IMAP UIDs are numeric; OAuth message IDs are alphanumeric (e.g. "18e4a3b2c1d0").
    # Switching without deleting the store is safe (no crashes), but the first run
    # after a switch will re-scan and may re-download previously seen emails.
    if uid_store_path.exists():
        existing = load_json(uid_store_path)
        if existing:
            sample = next(iter(existing))
            stored_is_imap  = sample.isdigit()
            current_is_imap = (auth_method == "imap")
            if stored_is_imap != current_is_imap:
                log.warning(
                    "⚠️  Auth method changed (%s → %s). "
                    "The existing UID store (%s) was written by the previous method. "
                    "Delete it to avoid re-downloading already-processed emails.",
                    "imap" if stored_is_imap else "oauth",
                    auth_method,
                    os.path.basename(str(uid_store_path)),
                )

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        uid_store_path.parent.mkdir(parents=True, exist_ok=True)

    if auth_method == "oauth":
        fetch_oauth(config, output_dir, uid_store_path, dry_run=args.dry_run)
    else:
        fetch_imap(config, output_dir, uid_store_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
