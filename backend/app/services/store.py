"""Company-scoped persistence: every query is filtered by the signed-in user's company."""
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from app import db
from app.models import BankTransaction, ReceiptRecord, Workspace
from app.services.ledger import default_workspace


@dataclass
class StoredFile:
    name: str
    content_type: str
    content: bytes


class Store:
    def __init__(self, engine: Engine, company_id: int):
        self.engine = engine
        self.company_id = company_id

    # Workspace ---------------------------------------------------------------
    def workspace(self) -> Workspace:
        with self.engine.connect() as c:
            data = c.execute(select(db.companies.c.workspace).where(db.companies.c.id == self.company_id)).scalar()
        return Workspace.model_validate(data) if data else default_workspace()

    def save_workspace(self, workspace: Workspace) -> Workspace:
        with self.engine.begin() as c:
            c.execute(update(db.companies).where(db.companies.c.id == self.company_id).values(workspace=workspace.model_dump(mode="json")))
        return workspace

    # Receipts ----------------------------------------------------------------
    def reserve_receipt_id(self) -> str:
        """Allocate a never-reused number; the row stays hidden until save_receipt fills it."""
        with self.engine.begin() as c:
            seq = c.execute(
                update(db.companies).where(db.companies.c.id == self.company_id)
                .values(next_receipt_no=db.companies.c.next_receipt_no + 1)
                .returning(db.companies.c.next_receipt_no)
            ).scalar_one()
            receipt_id = f"R-{seq:03d}"
            c.execute(insert(db.receipts).values(company_id=self.company_id, id=receipt_id, seq=seq, data=None))
        return receipt_id

    def _receipts(self):
        return db.receipts.c.company_id == self.company_id

    def receipts(self) -> list[ReceiptRecord]:
        with self.engine.connect() as c:
            rows = c.execute(select(db.receipts.c.data).where(self._receipts(), db.receipts.c.data.is_not(None)).order_by(db.receipts.c.seq)).all()
        return [ReceiptRecord.model_validate(r.data) for r in rows]

    def receipt(self, receipt_id: str) -> ReceiptRecord | None:
        with self.engine.connect() as c:
            data = c.execute(select(db.receipts.c.data).where(self._receipts(), db.receipts.c.id == receipt_id)).scalar()
        return ReceiptRecord.model_validate(data) if data else None

    def save_receipt(self, receipt: ReceiptRecord) -> ReceiptRecord:
        data = receipt.model_dump(mode="json")
        with self.engine.begin() as c:
            done = c.execute(update(db.receipts).where(self._receipts(), db.receipts.c.id == receipt.id).values(data=data)).rowcount
            if not done:
                seq = int(receipt.id.removeprefix("R-")) if receipt.id.removeprefix("R-").isdigit() else 0
                c.execute(insert(db.receipts).values(company_id=self.company_id, id=receipt.id, seq=seq, data=data))
        return receipt

    def delete_receipt(self, receipt_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(db.receipt_files).where(db.receipt_files.c.company_id == self.company_id, db.receipt_files.c.receipt_id == receipt_id))
            c.execute(delete(db.receipts).where(self._receipts(), db.receipts.c.id == receipt_id))

    def receipt_file(self, receipt_id: str) -> StoredFile | None:
        f = db.receipt_files.c
        with self.engine.connect() as c:
            row = c.execute(select(f.file_name, f.content_type, f.content).where(f.company_id == self.company_id, f.receipt_id == receipt_id)).first()
        return StoredFile(row.file_name, row.content_type, bytes(row.content)) if row else None

    def save_receipt_file(self, receipt_id: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        f = db.receipt_files.c
        with self.engine.begin() as c:
            c.execute(delete(db.receipt_files).where(f.company_id == self.company_id, f.receipt_id == receipt_id))
            c.execute(insert(db.receipt_files).values(company_id=self.company_id, receipt_id=receipt_id, file_name=filename, content_type=content_type, content=content))

    # Bank transactions -------------------------------------------------------
    def transactions(self) -> list[BankTransaction]:
        t = db.transactions.c
        with self.engine.connect() as c:
            rows = c.execute(select(t.data).where(t.company_id == self.company_id).order_by(t.posted_at.desc())).all()
        return [BankTransaction.model_validate(r.data) for r in rows]

    def add_transactions(self, transactions: list[BankTransaction]) -> int:
        """Insert new rows; re-importing the same export is a no-op."""
        t = db.transactions.c
        with self.engine.begin() as c:
            existing = set(c.execute(select(t.id).where(t.company_id == self.company_id, t.id.in_([x.id for x in transactions]))).scalars())
            fresh = {x.id: x for x in transactions if x.id not in existing}  # also dedupes inside one file
            if fresh:
                c.execute(insert(db.transactions), [
                    {"company_id": self.company_id, "id": x.id, "bank": x.bank, "posted_at": x.posted_at, "data": x.model_dump(mode="json")}
                    for x in fresh.values()
                ])
        return len(fresh)

    def delete_transactions(self, bank: str) -> int:
        t = db.transactions.c
        with self.engine.begin() as c:
            return c.execute(delete(db.transactions).where(t.company_id == self.company_id, t.bank == bank)).rowcount

    def set_next_receipt_no(self, at_least: int) -> None:
        """Keep numbering after imported receipts so old ids are never reused."""
        with self.engine.begin() as c:
            current = c.execute(select(db.companies.c.next_receipt_no).where(db.companies.c.id == self.company_id)).scalar_one()
            if at_least > current:
                c.execute(update(db.companies).where(db.companies.c.id == self.company_id).values(next_receipt_no=at_least))

    def counts(self) -> dict[str, int]:
        with self.engine.connect() as c:
            receipts = c.execute(select(func.count()).select_from(db.receipts).where(self._receipts(), db.receipts.c.data.is_not(None))).scalar_one()
            txs = c.execute(select(func.count()).select_from(db.transactions).where(db.transactions.c.company_id == self.company_id)).scalar_one()
        return {"receipts": receipts, "transactions": txs}


def company_for_user(engine: Engine, user_id: int) -> int:
    """The user's company id, creating an empty company on first use."""
    with engine.begin() as c:
        company_id = c.execute(select(db.companies.c.id).where(db.companies.c.owner_id == user_id)).scalar()
        if company_id is None:
            company_id = c.execute(insert(db.companies).values(owner_id=user_id, next_receipt_no=0).returning(db.companies.c.id)).scalar_one()
    return company_id
