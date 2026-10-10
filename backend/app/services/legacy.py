"""One-time import of the pre-account single-user data (data/journalizer.db + data/uploads/).

Only an administrator's offline command may initiate this import. Public signup
and Google login never call it. Original files are retained and a marker prevents
importing them twice.
"""
import mimetypes
import os
from pathlib import Path
import sqlite3

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app import db
from app.models import BankTransaction, ReceiptRecord, Workspace
from app.services.store import Store, company_for_user


def legacy_dir() -> Path:
    return Path(os.environ.get("JOURNALIZER_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def import_legacy(store: Store, data_dir: Path | None = None) -> dict[str, int] | None:
    data_dir = data_dir or legacy_dir()
    source = data_dir / "journalizer.db"
    marker = data_dir / "journalizer.db.imported"
    if not source.exists() or marker.exists():
        return None
    con = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT data FROM settings WHERE key = 'workspace'").fetchone()
        receipts = [ReceiptRecord.model_validate_json(r[0]) for r in con.execute("SELECT data FROM receipts WHERE data != '' ORDER BY seq")]
        transactions = [BankTransaction.model_validate_json(r[0]) for r in con.execute("SELECT data FROM transactions")]
        last_seq = con.execute("SELECT COALESCE(MAX(seq), 0) FROM receipts").fetchone()[0]
    finally:
        con.close()

    if row:
        store.save_workspace(Workspace.model_validate_json(row[0]))
    uploads = data_dir / "uploads"
    files = 0
    for receipt in receipts:
        store.save_receipt(receipt)
        original = next(iter(uploads.glob(f"{receipt.id}.*")), None)
        if original:
            content_type = receipt.content_type or mimetypes.guess_type(original.name)[0] or "application/octet-stream"
            store.save_receipt_file(receipt.id, receipt.file_name, original.read_bytes(), content_type)
            files += 1
    added = store.add_transactions(transactions) if transactions else 0
    store.set_next_receipt_no(last_seq)
    marker.write_text(f"imported into company {store.company_id}\n", encoding="utf-8")
    return {"receipts": len(receipts), "files": files, "transactions": added}


def import_for_owner(engine: Engine, owner_id: int, expected_email: str, data_dir: Path) -> dict[str, int] | None:
    """Offline only: require an explicit existing owner and an empty destination.

    The operator must verify ownership outside the unverified signup email field
    and stop application workers while importing. Refuse merges/overwrites.
    """
    data_dir = data_dir.resolve()
    if not (data_dir / "journalizer.db").is_file():
        raise ValueError("이관할 journalizer.db 파일이 없습니다.")
    with engine.connect() as connection:
        owner = connection.execute(select(db.users).where(db.users.c.id == owner_id)).first()
        if owner is None or owner.email.lower() != expected_email.strip().lower():
            raise ValueError("대상 회원 ID와 이메일이 일치하지 않습니다.")
    if (data_dir / "journalizer.db.imported").exists():
        return None
    company_id = company_for_user(engine, owner_id)
    with engine.connect() as connection:
        workspace = connection.execute(select(db.companies.c.workspace).where(db.companies.c.id == company_id)).scalar()
        if workspace is not None:
            raise ValueError("대상 회사에 설정이 있습니다. 빈 회사로만 이관할 수 있습니다.")
        for table in (db.receipts, db.transactions, db.entries, db.documents, db.secrets, db.sales_invoices, db.vat_returns):
            if connection.execute(select(table.c.company_id).where(table.c.company_id == company_id).limit(1)).first():
                raise ValueError("대상 회사에 자료가 있습니다. 빈 회사로만 이관할 수 있습니다.")
    return import_legacy(Store(engine, company_id), data_dir)


def main() -> None:
    import argparse
    import json
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="관리자 전용: 서비스를 중지하고 확인한 소유자의 빈 회사로 이전 자료를 이관합니다.")
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--expected-email", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    engine = db.make_engine()
    try:
        result = import_for_owner(engine, args.owner_id, args.expected_email, args.data_dir)
    except ValueError as exc:
        parser.error(str(exc))
    finally:
        engine.dispose()
    print(json.dumps({"imported": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
