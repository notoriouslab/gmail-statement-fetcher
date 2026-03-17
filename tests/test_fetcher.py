"""Tests for gmail-statement-fetcher core logic.

Covers the pure-Python functions that don't require a live Gmail connection:
  - _imap_since_date        — locale-safe date formatting
  - _sanitize_for_log       — control-character stripping
  - _subject_hash           — stable SHA-256 dedup key
  - match_email             — sender/subject matching with domain boundary logic
  - build_normalized_filename — date extraction + doc_type rules + filename assembly
  - prune_processed_uids    — retention-based record pruning
  - _resolve_bank_passwords — env var override for pdf/zip passwords
  - _decrypt_pdf_if_needed  — no-password pass-through; pikepdf-unavailable fallback
  - save_pdf                — placeholder cleanup on write failure; dry_run conflict hint
"""

import datetime
import os
import sys
from pathlib import Path

import pytest

# Add project root to path so `import fetcher` works regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetcher


# ---------------------------------------------------------------------------
# _imap_since_date
# ---------------------------------------------------------------------------

class TestImapSinceDate:
    def test_format(self):
        result = fetcher._imap_since_date(0)
        today = datetime.date.today()
        assert result == f"{today.day:02d}-{fetcher._IMAP_MONTHS[today.month]}-{today.year}"

    def test_lookback_30_days(self):
        result = fetcher._imap_since_date(30)
        expected = datetime.date.today() - datetime.timedelta(days=30)
        assert result == f"{expected.day:02d}-{fetcher._IMAP_MONTHS[expected.month]}-{expected.year}"

    def test_always_english_month(self):
        # Regardless of system locale, month must be English (IMAP RFC 3501)
        result = fetcher._imap_since_date(0)
        month_part = result.split("-")[1]
        assert month_part in fetcher._IMAP_MONTHS[1:]


# ---------------------------------------------------------------------------
# _sanitize_for_log
# ---------------------------------------------------------------------------

class TestSanitizeForLog:
    def test_clean_string_unchanged(self):
        assert fetcher._sanitize_for_log("Hello World") == "Hello World"

    def test_newline_replaced(self):
        assert fetcher._sanitize_for_log("line1\nline2") == "line1?line2"

    def test_carriage_return_replaced(self):
        assert fetcher._sanitize_for_log("a\rb") == "a?b"

    def test_ansi_escape_replaced(self):
        # ANSI reset sequence \x1b[0m — \x1b is one control char, replaced by one ?
        assert fetcher._sanitize_for_log("\x1b[0mtext") == "?[0mtext"

    def test_null_byte_replaced(self):
        assert fetcher._sanitize_for_log("a\x00b") == "a?b"

    def test_non_ascii_cjk_untouched(self):
        s = "永豐銀行信用卡對帳單"
        assert fetcher._sanitize_for_log(s) == s


# ---------------------------------------------------------------------------
# _subject_hash
# ---------------------------------------------------------------------------

class TestSubjectHash:
    def test_returns_12_hex_chars(self):
        h = fetcher._subject_hash("test subject")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert fetcher._subject_hash("same") == fetcher._subject_hash("same")

    def test_different_inputs_differ(self):
        assert fetcher._subject_hash("a") != fetcher._subject_hash("b")

    def test_empty_string(self):
        h = fetcher._subject_hash("")
        assert len(h) == 12


# ---------------------------------------------------------------------------
# match_email
# ---------------------------------------------------------------------------

BANKS = {
    "sinopac": {
        "name": "永豐銀行",
        "imap_search": {
            "sender_keywords": ["sinopac.com"],
            "subject_keywords": ["電子綜合對帳單", "信用卡"],
        },
    },
    "ctbc": {
        "name": "中國信託",
        "imap_search": {
            "sender_keywords": ["ctbcbank.com"],
            "subject_keywords": ["電子對帳單"],
        },
    },
    "_disabled": {
        "name": "Disabled Bank",
        "imap_search": {
            "sender_keywords": ["disabled.com"],
            "subject_keywords": ["statement"],
        },
    },
}


class TestMatchEmail:
    def test_exact_match(self):
        bank_id, cfg = fetcher.match_email(
            "noreply@sinopac.com", "永豐銀行電子綜合對帳單 2026年2月", BANKS
        )
        assert bank_id == "sinopac"
        assert cfg["name"] == "永豐銀行"

    def test_subject_keyword_second(self):
        bank_id, cfg = fetcher.match_email(
            "statement@sinopac.com", "信用卡帳單通知", BANKS
        )
        assert bank_id == "sinopac"

    def test_no_match_wrong_domain(self):
        bank_id, cfg = fetcher.match_email(
            "noreply@notabank.com", "電子綜合對帳單", BANKS
        )
        assert bank_id is None
        assert cfg is None

    def test_no_match_wrong_subject(self):
        bank_id, cfg = fetcher.match_email(
            "noreply@sinopac.com", "無關主旨 promotion", BANKS
        )
        assert bank_id is None

    def test_domain_boundary_prevents_partial_match(self):
        # "evil-sinopac.com" should NOT match "sinopac.com" keyword
        bank_id, _ = fetcher.match_email(
            "noreply@evil-sinopac.com", "電子綜合對帳單", BANKS
        )
        assert bank_id is None

    def test_subdomain_matches(self):
        # "mail.sinopac.com" has ".sinopac.com" so should match
        bank_id, _ = fetcher.match_email(
            "noreply@mail.sinopac.com", "電子綜合對帳單", BANKS
        )
        assert bank_id == "sinopac"

    def test_template_entry_skipped(self):
        # Keys starting with "_" must be ignored
        bank_id, _ = fetcher.match_email(
            "noreply@disabled.com", "statement", BANKS
        )
        assert bank_id is None

    def test_both_banks_distinguish(self):
        _, cfg1 = fetcher.match_email(
            "noreply@sinopac.com", "電子綜合對帳單", BANKS
        )
        _, cfg2 = fetcher.match_email(
            "noreply@ctbcbank.com", "中國信託銀行電子對帳單", BANKS
        )
        assert cfg1["name"] != cfg2["name"]


# ---------------------------------------------------------------------------
# build_normalized_filename
# ---------------------------------------------------------------------------

BANK_CFG = {
    "short_name": "永豐銀行",
    "doc_type_rules": [
        {"keyword": "信用卡", "type": "信用卡對帳單"},
        {"keyword": "電子綜合對帳單", "type": "銀行對帳單"},
    ],
    "default_doc_type": "對帳單",
    "subject_date_pattern": r"(\d{4})年(\d{1,2})月",
}


class TestBuildNormalizedFilename:
    def test_date_from_subject_pattern(self):
        name = fetcher.build_normalized_filename(
            "永豐銀行",
            BANK_CFG["doc_type_rules"],
            BANK_CFG["default_doc_type"],
            "永豐銀行電子綜合對帳單 2026年2月",
            "Mon, 10 Mar 2026 08:00:00 +0800",
            BANK_CFG["subject_date_pattern"],
        )
        assert "2026_02" in name

    def test_month_zero_padded(self):
        name = fetcher.build_normalized_filename(
            "永豐銀行",
            BANK_CFG["doc_type_rules"],
            BANK_CFG["default_doc_type"],
            "永豐銀行電子綜合對帳單 2026年3月",
            "Mon, 10 Apr 2026 08:00:00 +0800",
            BANK_CFG["subject_date_pattern"],
        )
        assert "_03" in name

    def test_doc_type_from_subject(self):
        name = fetcher.build_normalized_filename(
            "永豐銀行",
            BANK_CFG["doc_type_rules"],
            BANK_CFG["default_doc_type"],
            "信用卡帳單通知",
            "Mon, 10 Mar 2026 08:00:00 +0800",
            None,
        )
        assert "信用卡對帳單" in name

    def test_default_doc_type_fallback(self):
        name = fetcher.build_normalized_filename(
            "永豐銀行",
            BANK_CFG["doc_type_rules"],
            "對帳單",
            "沒有匹配關鍵字的主旨",
            "Mon, 10 Mar 2026 08:00:00 +0800",
            None,
        )
        assert "對帳單" in name

    def test_date_fallback_to_email_header(self):
        # No subject_date_pattern, date comes from email header
        name = fetcher.build_normalized_filename(
            "MyBank",
            [],
            "Statement",
            "Monthly Statement",
            "Mon, 03 Feb 2026 12:00:00 +0000",
            None,
        )
        assert "2026_02" in name

    def test_short_name_in_filename(self):
        name = fetcher.build_normalized_filename(
            "永豐銀行",
            [],
            "Statement",
            "subject",
            "Mon, 03 Feb 2026 12:00:00 +0000",
            None,
        )
        assert name.startswith("永豐銀行_")

    def test_filename_ends_with_pdf(self):
        name = fetcher.build_normalized_filename(
            "MyBank", [], "Statement", "subject",
            "Mon, 03 Feb 2026 12:00:00 +0000", None,
        )
        assert name.endswith(".pdf")


# ---------------------------------------------------------------------------
# prune_processed_uids
# ---------------------------------------------------------------------------

class TestPruneProcessedUids:
    def _make_record(self, days_ago):
        ts = (datetime.datetime.now(tz=datetime.timezone.utc)
              - datetime.timedelta(days=days_ago)).isoformat()
        return {"processed_at": ts, "bank": "TestBank"}

    def test_recent_records_kept(self):
        store = {"uid1": self._make_record(10)}
        result = fetcher.prune_processed_uids(store, retention_days=30)
        assert "uid1" in result

    def test_old_records_pruned(self):
        store = {"uid1": self._make_record(60)}
        result = fetcher.prune_processed_uids(store, retention_days=30)
        assert "uid1" not in result

    def test_zero_retention_keeps_all(self):
        store = {"uid1": self._make_record(9999)}
        result = fetcher.prune_processed_uids(store, retention_days=0)
        assert "uid1" in result

    def test_missing_timestamp_kept(self):
        store = {"uid1": {"bank": "TestBank"}}  # no processed_at
        result = fetcher.prune_processed_uids(store, retention_days=1)
        assert "uid1" in result

    def test_boundary_recent_kept(self):
        store = {"uid1": self._make_record(29)}
        result = fetcher.prune_processed_uids(store, retention_days=30)
        assert "uid1" in result

    def test_original_not_mutated(self):
        store = {"uid1": self._make_record(60)}
        original_keys = set(store.keys())
        fetcher.prune_processed_uids(store, retention_days=30)
        assert set(store.keys()) == original_keys

    def test_zero_retention_returns_copy_not_same_object(self):
        store = {"uid1": self._make_record(9999)}
        result = fetcher.prune_processed_uids(store, retention_days=0)
        assert result is not store
        assert result == store


# ---------------------------------------------------------------------------
# _resolve_bank_passwords
# ---------------------------------------------------------------------------

class TestResolveBankPasswords:
    def test_no_env_var_returns_same_object(self):
        cfg = {"name": "Test", "pdf_password": "", "zip_password": ""}
        result = fetcher._resolve_bank_passwords("testbank", cfg)
        assert result is cfg

    def test_pdf_password_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("SINOPAC_PDF_PASSWORD", "secret123")
        cfg = {"name": "永豐", "pdf_password": "", "zip_password": ""}
        result = fetcher._resolve_bank_passwords("sinopac", cfg)
        assert result["pdf_password"] == "secret123"

    def test_zip_password_overridden_by_env(self, monkeypatch):
        monkeypatch.setenv("CTBC_ZIP_PASSWORD", "zippass")
        cfg = {"name": "中信", "pdf_password": "", "zip_password": ""}
        result = fetcher._resolve_bank_passwords("ctbc", cfg)
        assert result["zip_password"] == "zippass"

    def test_env_overrides_config_value(self, monkeypatch):
        monkeypatch.setenv("MYBANK_PDF_PASSWORD", "from_env")
        cfg = {"pdf_password": "from_config", "zip_password": ""}
        result = fetcher._resolve_bank_passwords("mybank", cfg)
        assert result["pdf_password"] == "from_env"

    def test_original_dict_not_mutated(self, monkeypatch):
        monkeypatch.setenv("MYBANK_PDF_PASSWORD", "env_val")
        cfg = {"pdf_password": "original", "zip_password": ""}
        fetcher._resolve_bank_passwords("mybank", cfg)
        assert cfg["pdf_password"] == "original"

    def test_bank_key_case_insensitive_lookup(self, monkeypatch):
        # env var is always uppercased; bank_id may be lowercase in config
        monkeypatch.setenv("ESUN_PDF_PASSWORD", "esunpass")
        cfg = {"pdf_password": "", "zip_password": ""}
        result = fetcher._resolve_bank_passwords("esun", cfg)
        assert result["pdf_password"] == "esunpass"

    def test_only_set_env_var_wins(self, monkeypatch):
        # pdf env set, zip not set — only pdf should change
        monkeypatch.setenv("MYBANK_PDF_PASSWORD", "pdfonly")
        cfg = {"pdf_password": "", "zip_password": "original_zip"}
        result = fetcher._resolve_bank_passwords("mybank", cfg)
        assert result["pdf_password"] == "pdfonly"
        assert result["zip_password"] == "original_zip"


# ---------------------------------------------------------------------------
# _decrypt_pdf_if_needed
# ---------------------------------------------------------------------------

class TestDecryptPdfIfNeeded:
    def test_no_password_returns_original(self):
        data = b"%PDF-1.4 fake pdf bytes"
        assert fetcher._decrypt_pdf_if_needed(data, None) is data

    def test_empty_password_returns_original(self):
        data = b"%PDF-1.4 fake pdf bytes"
        assert fetcher._decrypt_pdf_if_needed(data, "") is data

    def test_empty_payload_returns_original(self):
        assert fetcher._decrypt_pdf_if_needed(b"", "password") == b""

    def test_none_payload_returns_none(self):
        assert fetcher._decrypt_pdf_if_needed(None, "password") is None

    def test_pikepdf_unavailable_returns_original(self, monkeypatch):
        # Simulate pikepdf not installed by making import fail
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pikepdf":
                raise ImportError("pikepdf not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        data = b"%PDF-1.4 fake encrypted pdf"
        result = fetcher._decrypt_pdf_if_needed(data, "somepassword")
        assert result is data

    def test_pikepdf_bad_password_returns_original(self, monkeypatch):
        # Simulate pikepdf raising an exception (bad password)
        class FakePikepdf:
            @staticmethod
            def open(*args, **kwargs):
                raise Exception("incorrect password")

        import sys
        monkeypatch.setitem(sys.modules, "pikepdf", FakePikepdf)
        data = b"%PDF-1.4 fake encrypted pdf"
        result = fetcher._decrypt_pdf_if_needed(data, "wrongpassword")
        assert result is data


# ---------------------------------------------------------------------------
# save_pdf — placeholder cleanup + dry_run conflict hint
# ---------------------------------------------------------------------------

DUMMY_BANK_CFG = {
    "short_name": "TestBank",
    "doc_type_rules": [],
    "default_doc_type": "Statement",
    "subject_date_pattern": None,
}


class TestSavePdf:
    def test_placeholder_cleaned_up_on_write_failure(self, tmp_path, monkeypatch):
        """If writing fails after resolve_save_path creates a placeholder,
        both the placeholder and the temp file must be removed."""
        # Patch os.fdopen to raise immediately after mkstemp succeeds
        real_fdopen = os.fdopen

        def fail_fdopen(fd, *args, **kwargs):
            os.close(fd)  # avoid fd leak in test
            raise OSError("simulated write failure")

        monkeypatch.setattr(os, "fdopen", fail_fdopen)

        with pytest.raises(OSError):
            fetcher.save_pdf(
                DUMMY_BANK_CFG,
                b"%PDF-1.4 content",
                "Monthly Statement",
                "Mon, 03 Feb 2026 12:00:00 +0000",
                str(tmp_path),
                dry_run=False,
            )

        # No leftover files (no placeholder, no tmp)
        remaining = list(tmp_path.iterdir())
        assert remaining == [], f"Leftover files: {remaining}"

    def test_dry_run_no_conflict_logs_plain(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            fetcher.save_pdf(
                DUMMY_BANK_CFG,
                b"%PDF content",
                "Monthly Statement",
                "Mon, 03 Feb 2026 12:00:00 +0000",
                str(tmp_path),
                dry_run=True,
            )
        assert any("_N suffix" not in r.message and "Would save" in r.message
                   for r in caplog.records)
        # Nothing written
        assert list(tmp_path.iterdir()) == []

    def test_dry_run_conflict_logs_suffix_hint(self, tmp_path, caplog):
        import logging
        # Pre-create the file so a conflict exists
        norm = "TestBank_Statement_2026_02.pdf"
        (tmp_path / norm).write_bytes(b"existing")

        with caplog.at_level(logging.INFO):
            fetcher.save_pdf(
                DUMMY_BANK_CFG,
                b"%PDF content",
                "Monthly Statement",
                "Mon, 03 Feb 2026 12:00:00 +0000",
                str(tmp_path),
                dry_run=True,
            )
        assert any("_N suffix" in r.message for r in caplog.records)
