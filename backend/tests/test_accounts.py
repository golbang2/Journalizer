import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import update

from app import auth, db
from app.auth import get_db
from db_helpers import fresh_engine
from app.main import app
from app.models import ReceiptRecord, Workspace
from app.services.ledger import default_workspace
from app.services.legacy import import_for_owner


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = fresh_engine(self.tmp.name)
        app.dependency_overrides[get_db] = lambda: self.engine
        auth.login_throttle.failures.clear()
        # Point the legacy importer at an empty folder unless a test fills it.
        self.env = patch.dict(os.environ, {"JOURNALIZER_DATA_DIR": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def client(self) -> TestClient:
        # Separate clients keep separate cookie jars, i.e. separate browsers.
        c = TestClient(app).__enter__()
        self.addCleanup(c.__exit__, None, None, None)
        return c

    def signup(self, c, email, password="password123"):
        r = c.post("/auth/signup", json={"email": email, "password": password, "name": ""})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def upload(self, c, name="a.pdf"):
        with patch("app.main.extract_receipt", side_effect=RuntimeError("no OCR in tests")):
            r = c.post("/receipts", files={"file": (name, b"%PDF-1.7 " + name.encode(), "application/pdf")})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_everything_requires_login(self):
        c = self.client()
        for method, path in [("get", "/receipts"), ("get", "/workspace"), ("get", "/bank-transactions"), ("get", "/ledger/10300"), ("get", "/auth/me")]:
            self.assertEqual(getattr(c, method)(path).status_code, 401, path)

    def test_signup_login_logout(self):
        c = self.client()
        me = self.signup(c, "Owner@Example.com")
        self.assertEqual((me["email"], me["has_password"], me["receipts"]), ("owner@example.com", True, 0))
        self.assertNotIn("scrypt", json.dumps(me))  # the hash never leaves the server
        self.assertNotIn("password_hash", me)
        self.assertEqual(c.get("/auth/me").json()["email"], "owner@example.com")
        session = c.cookies.get(auth.SESSION_COOKIE)
        with self.engine.connect() as conn:
            stored = conn.exec_driver_sql("SELECT token_hash FROM sessions").scalar()
        self.assertNotEqual(stored, session)  # only a hash of the cookie is stored

        self.assertEqual(c.post("/auth/logout").status_code, 204)
        self.assertEqual(c.get("/auth/me").status_code, 401)
        self.assertEqual(c.post("/auth/login", json={"email": "owner@example.com", "password": "wrong-password"}).status_code, 401)
        self.assertEqual(c.post("/auth/login", json={"email": "OWNER@example.com", "password": "password123"}).status_code, 200)
        self.assertEqual(c.get("/auth/me").status_code, 200)

    def test_new_company_must_upload_documents(self):
        c = self.client()
        me = self.signup(c, "owner@example.com")
        self.assertFalse(me["onboarded"])
        self.assertEqual([d["file_name"] for d in c.get("/company/documents").json()], ["", "", ""])
        self.assertEqual(c.post("/company/documents/registry", files={"file": ("a.txt", b"x")}).status_code, 415)
        self.assertEqual(c.post("/company/documents/other", files={"file": ("a.pdf", b"%PDF")}).status_code, 404)
        for kind in ("registry", "shareholders"):
            self.assertEqual(c.post(f"/company/documents/{kind}", files={"file": (f"{kind}.pdf", b"%PDF-1.7", "application/pdf")}).status_code, 200)
        self.assertFalse(c.get("/auth/me").json()["onboarded"])  # all three are required
        docs = c.post("/company/documents/business", files={"file": ("사업자등록증.jpg", bytes([0xFF, 0xD8]), "image/jpeg")}).json()
        self.assertEqual([d["label"] for d in docs if d["file_name"]], ["등기부등본", "주주명부", "사업자등록증"])
        self.assertTrue(c.get("/auth/me").json()["onboarded"])
        self.assertEqual(c.get("/company/documents/registry/file").content, b"%PDF-1.7")

    def test_signup_validation(self):
        c = self.client()
        self.assertEqual(c.post("/auth/signup", json={"email": "not-an-email", "password": "password123"}).status_code, 422)
        self.assertEqual(c.post("/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code, 422)
        self.signup(c, "a@b.co")
        self.assertEqual(self.client().post("/auth/signup", json={"email": "A@B.co", "password": "password123"}).status_code, 409)

    def test_unreadable_documents_are_not_read_again_until_they_change(self):
        from unittest.mock import patch
        from app.services import ai_judge

        c = self.client()
        self.signup(c, "owner@example.com")
        with patch.dict("os.environ", {"AI_JUDGE": "on"}), patch("app.services.ai_judge.read_profile", side_effect=ai_judge.AiJudgeError("읽지 못함")) as read:
            for kind in ("registry", "shareholders", "business"):
                c.post(f"/company/documents/{kind}", files={"file": (f"{kind}.pdf", b"%PDF-1.7", "application/pdf")})
            self.assertEqual(read.call_count, 1)
            self.assertEqual(c.get("/workspace").json()["profile"]["error"], "읽지 못함")
            with patch("app.services.ai_judge.recommend", return_value={}):
                c.post("/ai/judge", json={"ids": []})
            self.assertEqual(read.call_count, 1)  # not retried on every AI run
            c.post("/company/documents/business", files={"file": ("new.pdf", b"%PDF-1.7", "application/pdf")})
            self.assertEqual(read.call_count, 2)  # new documents are read again

    def test_signup_documents_can_be_read_again_for_company_tab(self):
        from unittest.mock import patch
        from app.models import CompanyProfile
        from app.services import ai_judge

        c = self.client()
        self.signup(c, "owner@example.com")
        with patch.dict("os.environ", {"AI_JUDGE": "on"}):
            self.assertEqual(c.post("/company/profile").status_code, 422)  # documents first
            with patch("app.services.ai_judge.read_profile", side_effect=ai_judge.AiJudgeError("API 키가 없습니다.")):
                for kind in ("registry", "shareholders", "business"):
                    c.post(f"/company/documents/{kind}", files={"file": (f"{kind}.pdf", b"%PDF-1.7", "application/pdf")})
            read = CompanyProfile(company="주식회사 더블락", business_number="123-81-45678", fiscal_start="01-01", fiscal_end="12-31")
            with patch("app.services.ai_judge.read_profile", return_value=read):
                ws = c.post("/company/profile").json()
        self.assertEqual(ws["company"], "주식회사 더블락")
        self.assertEqual((ws["profile"]["business_number"], ws["profile"]["fiscal_end"], ws["profile"]["error"]), ("123-81-45678", "12-31", ""))

    def test_company_ai_key_is_checked_and_masked(self):
        from unittest.mock import patch
        from app.services import llm

        c = self.client()
        self.signup(c, "owner@example.com")
        env = {"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "AI_JUDGE": "on"}
        with patch.dict("os.environ", env):
            status = c.get("/settings/ai").json()
            self.assertEqual((status["provider"], status["source"]), ("anthropic", "none"))
            self.assertEqual([p["id"] for p in status["providers"]], ["anthropic", "openai"])
            with patch("app.services.llm.list_models", side_effect=llm.LlmError("API 키가 올바르지 않습니다.")):
                bad = c.put("/settings/ai", json={"provider": "openai", "api_key": "sk-wrong-key-0000000000000"})
            self.assertEqual((bad.status_code, bad.json()["detail"]), (422, "API 키가 올바르지 않습니다."))
            models = ["gpt-9-pro", "gpt-9-mini", "gpt-8-mini"]
            with patch("app.services.llm.list_models", return_value=models):
                preview = c.post("/settings/ai/models", json={"provider": "openai", "api_key": "sk-proj-secretvalue-wxyz-1234"}).json()
            self.assertEqual((preview["models"], preview["default"]), (models, "gpt-9-mini"))
            self.assertEqual(c.get("/settings/ai").json()["provider"], "anthropic")  # previewing saves nothing
            with patch("app.services.llm.list_models", return_value=models) as listing:
                saved = c.put("/settings/ai", json={"provider": "openai", "api_key": " sk-proj-secretvalue-wxyz-1234 "}).json()
            listing.assert_called_once_with("openai", "sk-proj-secretvalue-wxyz-1234")
            self.assertEqual((saved["provider"], saved["model"], saved["models"]), ("openai", "gpt-9-mini", models))  # a mini is picked
            openai = next(p for p in saved["providers"] if p["id"] == "openai")
            self.assertEqual((openai["source"], openai["masked"]), ("company", "sk-proj…1234"))
            self.assertNotIn("secretvalue", json.dumps(c.get("/settings/ai").json()))
            with patch("app.services.llm.list_models", return_value=models):
                chosen = c.put("/settings/ai", json={"provider": "openai", "model": "gpt-9-pro"}).json()  # change model, keep key
                self.assertEqual(chosen["model"], "gpt-9-pro")
                self.assertEqual(c.put("/settings/ai", json={"provider": "openai", "model": "nope"}).status_code, 422)
            with patch("app.services.ai_judge.recommend", return_value={}) as recommend:
                c.post("/ai/judge", json={"ids": []})
            client = recommend.call_args.args[3]
            self.assertEqual((client.provider, client.api_key, client.model), ("openai", "sk-proj-secretvalue-wxyz-1234", "gpt-9-pro"))
            self.assertEqual(c.delete("/settings/ai?provider=openai").json()["source"], "none")

    def test_members_never_see_each_other(self):
        alice, bob = self.client(), self.client()
        self.signup(alice, "alice@example.com")
        self.signup(bob, "bob@example.com")
        r = self.upload(alice)
        self.assertEqual(r["id"], "R-001")
        ws = alice.get("/workspace").json()
        ws["company"] = "앨리스 상사"
        alice.put("/workspace", json=ws).raise_for_status()

        self.assertEqual(bob.get("/receipts").json(), [])
        self.assertEqual(bob.get("/receipts/R-001").status_code, 404)
        self.assertEqual(bob.get("/receipts/R-001/file").status_code, 404)
        self.assertEqual(bob.delete("/receipts/R-001").status_code, 404)
        self.assertEqual(bob.get("/workspace").json()["company"], "")
        self.assertEqual(self.upload(bob)["id"], "R-001")  # numbering is per company
        self.assertEqual(alice.get("/receipts/R-001/file").content, b"%PDF-1.7 a.pdf")
        self.assertEqual(len(alice.get("/receipts").json()), 1)

    def test_password_guessing_is_throttled(self):
        c = self.client()
        self.signup(c, "owner@example.com")
        for _ in range(5):
            self.assertEqual(c.post("/auth/login", json={"email": "owner@example.com", "password": "nope-nope"}).status_code, 401)
        self.assertEqual(c.post("/auth/login", json={"email": "owner@example.com", "password": "password123"}).status_code, 429)

    def legacy_fixture(self):
        data = Path(self.tmp.name)
        (data / "uploads").mkdir()
        legacy = sqlite3.connect(data / "journalizer.db")
        legacy.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE receipts (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            CREATE TABLE transactions (id TEXT PRIMARY KEY, posted_at TEXT NOT NULL, data TEXT NOT NULL);
        """)
        ws = default_workspace()
        ws.company = "주식회사 더블락"
        legacy.execute("INSERT INTO settings VALUES ('workspace', ?)", (ws.model_dump_json(),))
        for seq in (1, 2, 7):
            r = ReceiptRecord(id=f"R-{seq:03d}", file_name=f"page_{seq}.pdf", uploaded_at="2023-01-01T00:00:00", merchant="크몽", total=1000 * seq)
            legacy.execute("INSERT INTO receipts (seq, id, data) VALUES (?, ?, ?)", (seq, r.id, r.model_dump_json()))
            (data / "uploads" / f"{r.id}.pdf").write_bytes(b"%PDF " + r.id.encode())
        legacy.execute("INSERT INTO receipts (seq, id, data) VALUES (8, 'R-008', '')")  # an upload that never finished
        legacy.commit()
        legacy.close()
        return data

    def test_signup_does_not_import_legacy_data(self):
        data = self.legacy_fixture()
        first = self.client()
        me = self.signup(first, "owner@example.com")
        self.assertIsNone(me["imported"])
        self.assertEqual((me["company"], me["receipts"]), ("", 0))
        self.assertEqual(first.get("/receipts/R-007/file").status_code, 404)
        self.assertFalse((data / "journalizer.db.imported").exists())

        second = self.client()
        self.assertIsNone(self.signup(second, "second@example.com")["imported"])
        self.assertEqual(second.get("/receipts").json(), [])
        self.assertFalse((data / "journalizer.db.imported").exists())

    def google_callback(self, client, subject="g-1", email="owner@example.com", **changes):
        client.cookies.set(auth.STATE_COOKIE, "test-state")
        profile = {"sub": subject, "email": email, "email_verified": True, **changes}
        with patch("app.auth.google_profile", return_value=profile):
            return client.get("/auth/google/callback?code=test-code&state=test-state", follow_redirects=False)

    def test_first_google_signup_does_not_import_legacy_data(self):
        data = self.legacy_fixture()
        client = self.client()
        self.assertEqual(self.google_callback(client).headers["location"], "/")
        me = client.get("/auth/me").json()
        self.assertEqual((me["company"], me["receipts"], me["imported"]), ("", 0, None))
        self.assertEqual(client.get("/receipts/R-007/file").status_code, 404)
        self.assertFalse((data / "journalizer.db.imported").exists())

    def test_admin_import_requires_named_owner_and_preserves_numbering(self):
        data = self.legacy_fixture()
        first, owner = self.client(), self.client()
        self.signup(first, "first@example.com")
        owner_id = self.signup(owner, "owner@example.com")["id"]
        with self.assertRaises(ValueError):
            import_for_owner(self.engine, owner_id, "first@example.com", data)
        with self.assertRaises(ValueError):
            import_for_owner(self.engine, 9999, "owner@example.com", data)
        self.assertFalse((data / "journalizer.db.imported").exists())
        result = import_for_owner(self.engine, owner_id, "owner@example.com", data)
        self.assertEqual(result, {"receipts": 3, "files": 3, "transactions": 0})
        self.assertEqual(owner.get("/auth/me").json()["company"], "주식회사 더블락")
        self.assertEqual(owner.get("/receipts/R-007/file").content, b"%PDF R-007")
        self.assertEqual(first.get("/receipts/R-007/file").status_code, 404)
        self.assertEqual(self.upload(owner)["id"], "R-009")
        self.assertIsNone(import_for_owner(self.engine, owner_id, "owner@example.com", data))
        self.assertTrue((data / "journalizer.db.imported").exists())

    def test_admin_import_refuses_existing_company_data(self):
        data = self.legacy_fixture()
        c = self.client()
        user_id = self.signup(c, "owner@example.com")["id"]
        self.upload(c)
        with self.assertRaises(ValueError):
            import_for_owner(self.engine, user_id, "owner@example.com", data)
        self.assertEqual(len(c.get("/receipts").json()), 1)
        self.assertFalse((data / "journalizer.db.imported").exists())

    def test_google_email_match_never_links_preclaimed_account(self):
        attacker, owner = self.client(), self.client()
        self.signup(attacker, "owner@example.com", password="attacker-password")
        response = self.google_callback(owner, email="Owner@example.com")
        self.assertIn("login_error=", response.headers["location"])
        self.assertNotIn(auth.SESSION_COOKIE, response.cookies)
        self.assertEqual(owner.get("/auth/me").status_code, 401)
        self.assertEqual(owner.get("/workspace").status_code, 401)
        row = auth.find_user(self.engine, "owner@example.com")
        self.assertIsNone(row.google_sub)
        self.assertTrue(auth.verify_password("attacker-password", row.password_hash))
        self.assertFalse(attacker.get("/auth/me").json()["google"])

    def test_google_identity_is_not_replaced_by_matching_email(self):
        first = auth.google_user(self.engine, {"sub": "g-1", "email": "owner@example.com", "email_verified": True})
        with self.assertRaises(HTTPException) as error:
            auth.google_user(self.engine, {"sub": "g-2", "email": "owner@example.com", "email_verified": True})
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(auth.find_user(self.engine, first.email).google_sub, "g-1")

    def test_google_signup_and_repeat_login_require_verified_identity(self):
        profile = {"sub": "g-1", "email": "Owner@example.com", "email_verified": True}
        for changes in ({"email_verified": False}, {"email_verified": "true"}, {"sub": ""}, {"email": ""}):
            with self.assertRaises(HTTPException):
                auth.google_user(self.engine, {**profile, **changes})
        fresh = auth.google_user(self.engine, profile)
        self.assertEqual((fresh.email, fresh.has_password), ("owner@example.com", False))
        client = self.client()
        self.assertEqual(self.google_callback(client).headers["location"], "/")
        self.assertEqual(client.get("/auth/me").json()["id"], fresh.id)
        with self.assertRaises(HTTPException):
            auth.google_user(self.engine, {**profile, "email_verified": False})
        self.assertEqual(self.client().get("/auth/config").json(), {"google": auth.google_config() is not None})

    def test_startup_retires_old_linked_passwords_and_sessions_only_once(self):
        attacker, ordinary, google_only = self.client(), self.client(), self.client()
        linked_id = self.signup(attacker, "owner@example.com", "attacker-password")["id"]
        self.signup(ordinary, "ordinary@example.com")
        self.google_callback(google_only, subject="g-2", email="google@example.com")
        workspace = attacker.get("/workspace").json()
        workspace["company"] = "Preserved company"
        attacker.put("/workspace", json=workspace).raise_for_status()
        # Simulate a database left by the old auto-link implementation.
        with self.engine.begin() as connection:
            connection.execute(update(db.users).where(db.users.c.id == linked_id).values(google_sub="g-1"))
        reopened = db.make_engine(str(self.engine.url))
        reopened.dispose()
        self.assertEqual(attacker.get("/auth/me").status_code, 401)
        self.assertEqual(attacker.post("/auth/login", json={"email": "owner@example.com", "password": "attacker-password"}).status_code, 401)
        self.assertIsNone(auth.find_user(self.engine, "owner@example.com").password_hash)
        self.assertEqual(ordinary.get("/auth/me").status_code, 200)
        self.assertEqual(google_only.get("/auth/me").status_code, 200)
        victim = self.client()
        self.assertEqual(self.google_callback(victim).headers["location"], "/")
        self.assertEqual(victim.get("/workspace").json()["company"], "Preserved company")
        reopened = db.make_engine(str(self.engine.url))
        reopened.dispose()
        self.assertEqual(victim.get("/auth/me").status_code, 200)


class PasswordTests(unittest.TestCase):
    def test_hash_roundtrip(self):
        stored = auth.hash_password("correct horse")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verify_password("correct horse", stored))
        self.assertFalse(auth.verify_password("wrong horse", stored))
        self.assertFalse(auth.verify_password("x", None))
        self.assertNotEqual(stored, auth.hash_password("correct horse"))  # salted
