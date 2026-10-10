"""Database schema and engine.

DATABASE_URL picks the database: PostgreSQL in production
(postgresql+psycopg://user:password@host:5432/journalizer), a SQLite file by default for local work.
Business records stay as Pydantic JSON in `data` columns, so model changes need no migration;
the columns next to them exist for ownership, uniqueness and lookups.
"""
from datetime import datetime, timezone
import os
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()
Json = JSON().with_variant(JSONB(), "postgresql")
Id = BigInteger().with_variant(Integer(), "sqlite")  # SQLite only autoincrements INTEGER keys


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


users = Table(
    "users", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("email", String(320), nullable=False, unique=True),
    Column("name", String(200), nullable=False, default=""),
    Column("password_hash", String(300)),  # null for Google-only accounts
    Column("google_sub", String(100), unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),  # sha256 of the cookie value; the raw token is never stored
    Column("user_id", Id, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

companies = Table(
    "companies", metadata,
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("owner_id", Id, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),  # one user, one company
    Column("workspace", Json),
    Column("next_receipt_no", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

receipts = Table(
    "receipts", metadata,
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(20), primary_key=True),  # R-001, numbered per company
    Column("seq", Integer, nullable=False),
    Column("data", Json),  # null while an upload is still being processed
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow),
)

receipt_files = Table(
    "receipt_files", metadata,
    Column("company_id", Id, primary_key=True),
    Column("receipt_id", String(20), primary_key=True),
    Column("file_name", String(300), nullable=False),
    Column("content_type", String(100), nullable=False),
    Column("content", LargeBinary, nullable=False),  # originals live in the DB so any Postgres host works without a disk
    ForeignKeyConstraint(["company_id", "receipt_id"], ["receipts.company_id", "receipts.id"], ondelete="CASCADE"),
)

transactions = Table(
    "bank_transactions", metadata,
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(40), primary_key=True),
    Column("bank", String(20), nullable=False),
    Column("posted_at", String(30), nullable=False),
    Column("data", Json, nullable=False),
)


def default_url() -> str:
    data_dir = Path(os.environ.get("JOURNALIZER_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'app.db').as_posix()}"


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL") or default_url()
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")  # hosted providers hand out plain URLs
    engine = create_engine(url, pool_pre_ping=True, future=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    metadata.create_all(engine)
    return engine


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine
