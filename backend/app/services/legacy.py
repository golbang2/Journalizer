"""One-time import of the pre-account single-user data (data/journalizer.db + data/uploads/).

Runs for the first account that signs up. The old files are left in place and a marker
file prevents importing them twice.
"""
import mimetypes
import os
from pathlib import Path
import sqlite3

from app.models import BankTransaction, ReceiptRecord, Workspace
from app.services.store import Store


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
