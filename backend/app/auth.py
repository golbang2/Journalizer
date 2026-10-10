"""Accounts: email/password and Google sign-in, cookie sessions, and the per-user Store.

Sessions are random tokens in an httpOnly cookie; only their sha256 is stored, so a leaked
database cannot be replayed as logins. Passwords use scrypt from the standard library.
"""
from datetime import timedelta
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlencode

import httpx
from fastapi import Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app import db
from app.services.store import Store, company_for_user

SESSION_COOKIE = "jz_session"
STATE_COOKIE = "jz_oauth_state"
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Passwords --------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- Sessions ---------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(engine: Engine, response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    with engine.begin() as c:
        c.execute(insert(db.sessions).values(token_hash=_token_hash(token), user_id=user_id, expires_at=db.utcnow() + timedelta(days=SESSION_DAYS)))
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true"), path="/",
    )


def end_session(engine: Engine, response: Response, token: str | None) -> None:
    if token:
        with engine.begin() as c:
            c.execute(delete(db.sessions).where(db.sessions.c.token_hash == _token_hash(token)))
    response.delete_cookie(SESSION_COOKIE, path="/")


class User(BaseModel):
    id: int
    email: str
    name: str
    has_password: bool
    google: bool


def _user(row) -> User:
    return User(id=row.id, email=row.email, name=row.name or "", has_password=bool(row.password_hash), google=bool(row.google_sub))


def get_db() -> Engine:
    return db.get_engine()


def current_user(engine: Engine = Depends(get_db), jz_session: str | None = Cookie(default=None)) -> User:
    if not jz_session:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    u, s = db.users.c, db.sessions.c
    with engine.connect() as c:
        row = c.execute(
            select(db.users).join(db.sessions, s.user_id == u.id)
            .where(s.token_hash == _token_hash(jz_session), s.expires_at > db.utcnow())
        ).first()
    if row is None:
        raise HTTPException(status_code=401, detail="로그인이 만료되었습니다. 다시 로그인하세요.")
    return _user(row)


def get_store(user: User = Depends(current_user), engine: Engine = Depends(get_db)) -> Store:
    return Store(engine, company_for_user(engine, user.id))


# --- Accounts ---------------------------------------------------------------

def user_count(engine: Engine) -> int:
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(db.users)).scalar_one()


def create_user(engine: Engine, email: str, name: str, password: str | None = None, google_sub: str | None = None) -> User:
    try:
        with engine.begin() as c:
            row = c.execute(
                insert(db.users).values(email=email, name=name, password_hash=hash_password(password) if password else None, google_sub=google_sub)
                .returning(*db.users.c)
            ).first()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다. 로그인하세요.") from exc
    return _user(row)


def find_user(engine: Engine, email: str):
    with engine.connect() as c:
        return c.execute(select(db.users).where(func.lower(db.users.c.email) == email.lower())).first()


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="이메일 형식이 올바르지 않습니다.")
    return email


class Throttle:
    """Slows password guessing: at most `limit` failures per email in `window` seconds."""

    def __init__(self, limit: int = 5, window: int = 600):
        self.limit, self.window = limit, window
        self.failures: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        recent = self.failures[key]
        while recent and recent[0] < time.monotonic() - self.window:
            recent.popleft()
        if len(recent) >= self.limit:
            raise HTTPException(status_code=429, detail="로그인 시도가 너무 많습니다. 10분 뒤 다시 시도하세요.")

    def fail(self, key: str) -> None:
        self.failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


login_throttle = Throttle()


# --- Google -----------------------------------------------------------------

def google_config() -> dict | None:
    client_id, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    if not (client_id and secret):
        return None
    return {
        "client_id": client_id,
        "client_secret": secret,
        # Goes through the frontend's /api proxy so the session cookie is first-party.
        "redirect_uri": os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:3000/api/auth/google/callback"),
    }


def google_login_redirect() -> RedirectResponse:
    config = google_config()
    if not config:
        raise HTTPException(status_code=404, detail="구글 로그인이 설정되지 않았습니다.")
    state = secrets.token_urlsafe(24)
    query = urlencode({
        "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "response_type": "code",
        "scope": "openid email profile", "state": state, "prompt": "select_account",
    })
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query)
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/")
    return response


def google_profile(code: str) -> dict:
    config = google_config()
    with httpx.Client(timeout=10) as client:
        token = client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": config["client_id"], "client_secret": config["client_secret"],
            "redirect_uri": config["redirect_uri"], "grant_type": "authorization_code",
        })
        token.raise_for_status()
        info = client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token.json()['access_token']}"})
        info.raise_for_status()
    return info.json()


def google_user(engine: Engine, profile: dict) -> User:
    """Sign in by Google id; link to an existing email account only when Google verified the email."""
    with engine.connect() as c:
        row = c.execute(select(db.users).where(db.users.c.google_sub == profile["sub"])).first()
    if row:
        return _user(row)
    email = (profile.get("email") or "").lower()
    if not email or not profile.get("email_verified"):
        raise HTTPException(status_code=403, detail="구글 계정의 이메일이 확인되지 않았습니다.")
    existing = find_user(engine, email)
    if existing:
        with engine.begin() as c:
            c.execute(update(db.users).where(db.users.c.id == existing.id).values(google_sub=profile["sub"]))
        return _user(find_user(engine, email))
    return create_user(engine, email, profile.get("name") or email.split("@")[0], google_sub=profile["sub"])


def check_state(request: Request, state: str | None) -> None:
    expected = request.cookies.get(STATE_COOKIE)
    if not (state and expected and hmac.compare_digest(state, expected)):
        raise HTTPException(status_code=400, detail="로그인 요청이 만료되었습니다. 다시 시도하세요.")
