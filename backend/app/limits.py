"""Upload size limits and per-user / per-company caps on calls that cost money (OCR, LLM).

Request bodies are capped before FastAPI parses them, so an oversized upload is refused
without being spooled. Usage is counted in the database so every server process shares it.
"""
from datetime import timedelta
import os

from fastapi import Depends, HTTPException, UploadFile
from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

from app import db
from app.auth import get_store
from app.services.store import Store

MB = 1024 * 1024


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MAX_UPLOAD_BYTES = 15 * MB  # one file
MAX_FILES_PER_REQUEST = _env_int("MAX_FILES_PER_REQUEST", 10)
MAX_REQUEST_BYTES = _env_int("MAX_REQUEST_MB", 40) * MB  # whole multipart body, all files together
CALLS_PER_MINUTE = _env_int("PAID_CALLS_PER_MINUTE", 30)
COMPANY_CALLS_PER_DAY = _env_int("PAID_CALLS_PER_COMPANY_DAY", 500)


# --- Request size -------------------------------------------------------------

class RequestSizeLimit:
    """ASGI middleware: 413 for bodies over `limit`, checked against Content-Length and while streaming."""

    def __init__(self, app, limit: int = MAX_REQUEST_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > self.limit:
            return await self._too_large(send)

        received = 0
        exceeded = started = False

        async def counted_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message):
            nonlocal started
            if exceeded:
                return  # the app's error answer (400/500) is replaced by our 413 below
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await self._too_large(send)

    async def _too_large(self, send):
        body = f'{{"detail":"요청이 너무 큽니다. 한 번에 {self.limit // MB}MB 이하로 올리세요."}}'.encode()
        await send({"type": "http.response.start", "status": 413, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close"),
        ]})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


async def read_upload(file: UploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """The file's bytes, or 413 once it passes `limit` (never reads more than limit + 1 bytes)."""
    content = await file.read(limit + 1)
    if len(content) > limit:
        prefix = f"{file.filename}: " if file.filename else ""
        raise HTTPException(status_code=413, detail=f"{prefix}파일은 {limit // MB}MB 이하만 올릴 수 있습니다.")
    return content


def check_file_count(files: list[UploadFile]) -> None:
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(status_code=413, detail=f"한 번에 {MAX_FILES_PER_REQUEST}개 파일까지 올릴 수 있습니다.")


# --- Paid-call quota ------------------------------------------------------------

def charge(engine: Engine, company_id: int, kind: str) -> None:
    """Record one paid call, or raise 429 if the per-minute or daily cap is used up.

    A company has exactly one member (companies.owner_id is unique), so the per-minute cap is
    also the per-user cap. The company row is locked (Postgres) so concurrent requests from
    several servers cannot overshoot.
    """
    now = db.utcnow()
    u = db.usage_events.c
    with engine.begin() as c:
        c.execute(select(db.companies.c.id).where(db.companies.c.id == company_id).with_for_update())
        c.execute(delete(db.usage_events).where(u.company_id == company_id, u.created_at < now - timedelta(days=2)))
        recent = select(func.count()).where(u.company_id == company_id)
        if c.execute(recent.where(u.created_at > now - timedelta(minutes=1))).scalar() >= CALLS_PER_MINUTE:
            raise HTTPException(status_code=429, detail="요청이 너무 잦습니다. 1분 뒤 다시 시도하세요.", headers={"Retry-After": "60"})
        if c.execute(recent.where(u.created_at > now - timedelta(days=1))).scalar() >= COMPANY_CALLS_PER_DAY:
            raise HTTPException(status_code=429, detail=f"오늘 사용 한도({COMPANY_CALLS_PER_DAY}건)를 넘었습니다. 내일 다시 시도하세요.", headers={"Retry-After": "3600"})
        c.execute(insert(db.usage_events).values(company_id=company_id, kind=kind, created_at=now))


class Meter:
    """Handed to endpoints that call OCR or an LLM; they charge right before the paid call."""

    def __init__(self, store: Store):
        self.store = store

    def charge(self, kind: str) -> None:
        charge(self.store.engine, self.store.company_id, kind)


def get_meter(store: Store = Depends(get_store)) -> Meter:
    return Meter(store)
