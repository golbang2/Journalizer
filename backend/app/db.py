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
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    update,
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

entries = Table(
    "bank_entries", metadata,  # the journal entry for one bank row, keyed by the row's id
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("tx_id", String(40), primary_key=True),
    Column("data", Json, nullable=False),
)

documents = Table(
    "company_documents", metadata,  # 등기부등본·주주명부·사업자등록증 uploaded at onboarding
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", String(30), primary_key=True),
    Column("file_name", String(300), nullable=False),
    Column("content_type", String(100), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("uploaded_at", DateTime(timezone=True), nullable=False, default=utcnow),
)

secrets = Table(
    "company_secrets", metadata,  # e.g. the company's Claude API key; never returned to the browser in full
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("name", String(50), primary_key=True),
    Column("value", String(500), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow),
)

sales_invoices = Table(
    "sales_invoices", metadata,  # 매출 세금계산서
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(20), primary_key=True),
    Column("data", Json, nullable=False),
)

vat_returns = Table(
    "vat_returns", metadata,  # 부가세 신고 정리
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(20), primary_key=True),
    Column("data", Json, nullable=False),
)


hometax_imports = Table(
    "hometax_imports", metadata,
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("data", Json, nullable=False),
    Column("content", LargeBinary, nullable=False),
)

hometax_links = Table(
    "hometax_links", metadata,
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("receipt_id", String(20), nullable=False),
)


usage_events = Table(
    "usage_events", metadata,  # one row per OCR/LLM call, for rate limits and daily caps
    Column("id", Id, primary_key=True, autoincrement=True),
    Column("company_id", Id, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(30), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    Index("ix_usage_company_time", "company_id", "created_at"),
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
    retire_legacy_linked_credentials(engine)
    return engine


def retire_legacy_linked_credentials(engine: Engine) -> None:
    """Retire passwords/sessions created by the old unsafe Google auto-link flow.

    These accounts keep their Google identity and business data. The update and
    revocation commit together; once the password is gone, restarts are a no-op.
    """
    with engine.begin() as connection:
        ids = list(connection.execute(
            update(users).where(users.c.google_sub.is_not(None), users.c.password_hash.is_not(None))
            .values(password_hash=None).returning(users.c.id)
        ).scalars())
        if ids:
            connection.execute(delete(sessions).where(sessions.c.user_id.in_(ids)))


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine
