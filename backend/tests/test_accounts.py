import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth
from app.auth import get_db
from db_helpers import fresh_engine
from app.main import app
from app.models import ReceiptRecord, Workspace
from app.services.ledger import default_workspace


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

    def test_signup_validation(self):
        c = self.client()
        self.assertEqual(c.post("/auth/signup", json={"email": "not-an-email", "password": "password123"}).status_code, 422)
        self.assertEqual(c.post("/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code, 422)
        self.signup(c, "a@b.co")
        self.assertEqual(self.client().post("/auth/signup", json={"email": "A@B.co", "password": "password123"}).status_code, 409)

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

    def test_first_member_receives_pre_account_data_once(self):
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

        first = self.client()
        me = self.signup(first, "owner@example.com")
        self.assertEqual(me["imported"], {"receipts": 3, "files": 3, "transactions": 0})
        self.assertEqual((me["company"], me["receipts"]), ("주식회사 더블락", 3))
        self.assertEqual(first.get("/receipts/R-007/file").content, b"%PDF R-007")
        self.assertEqual(self.upload(first)["id"], "R-009")  # never reuses old numbers

        second = self.client()
        self.assertIsNone(self.signup(second, "second@example.com")["imported"])
        self.assertEqual(second.get("/receipts").json(), [])
        self.assertTrue((data / "journalizer.db.imported").exists())

    def test_google_account_links_only_verified_email(self):
        c = self.client()
        self.signup(c, "owner@example.com")
        with self.assertRaises(Exception):
            auth.google_user(self.engine, {"sub": "g-1", "email": "owner@example.com", "email_verified": False})
        linked = auth.google_user(self.engine, {"sub": "g-1", "email": "Owner@example.com", "email_verified": True})
        self.assertEqual((linked.email, linked.google, linked.has_password), ("owner@example.com", True, True))
        fresh = auth.google_user(self.engine, {"sub": "g-2", "email": "new@example.com", "email_verified": True, "name": "New"})
        self.assertEqual((fresh.email, fresh.has_password), ("new@example.com", False))
        self.assertEqual(self.client().get("/auth/config").json(), {"google": auth.google_config() is not None})


class PasswordTests(unittest.TestCase):
    def test_hash_roundtrip(self):
        stored = auth.hash_password("correct horse")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verify_password("correct horse", stored))
        self.assertFalse(auth.verify_password("wrong horse", stored))
        self.assertFalse(auth.verify_password("x", None))
        self.assertNotEqual(stored, auth.hash_password("correct horse"))  # salted
