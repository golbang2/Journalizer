"""Company-scoped persistence: every query is filtered by the signed-in user's company."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from app import db
from app.models import AccountingPolicy, BankEntry, BankTransaction, HometaxImport, ReceiptRecord, SalesInvoice, VatReturn, Workspace
from app.services.ledger import default_workspace, missing_standard_accounts


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
        workspace = Workspace.model_validate(data) if data else default_workspace()
        workspace.policies = self.policies()
        # Charts saved before the standard set grew lack 자본금, 매출, 결산 accounts; the AI can only pick listed codes.
        workspace.accounts += missing_standard_accounts(workspace)
        return workspace

    def save_workspace(self, workspace: Workspace) -> Workspace:
        with self.engine.begin() as c:
            c.execute(update(db.companies).where(db.companies.c.id == self.company_id).values(workspace=workspace.model_dump(mode="json", exclude={"policies"})))
        workspace.policies = self.policies()
        return workspace

    def policies(self) -> list[AccountingPolicy]:
        t = db.policies.c
        with self.engine.connect() as c:
            rows = c.execute(select(t.data).where(t.company_id == self.company_id).order_by(t.id)).scalars()
            return [AccountingPolicy.model_validate(row) for row in rows]

    def save_policy(self, policy: AccountingPolicy, expected: AccountingPolicy | None = None) -> AccountingPolicy:
        t = db.policies.c
        # Serialize policy lifecycle changes per company, including PostgreSQL.
        with self.engine.begin() as c:
            c.execute(update(db.companies).where(db.companies.c.id == self.company_id).values(id=self.company_id))
            current = c.execute(select(t.data).where(t.company_id == self.company_id, t.id == policy.id)).scalar()
            if expected is not None and (not current or AccountingPolicy.model_validate(current) != expected):
                raise ValueError("다른 요청에서 정책이 변경되었습니다. 새로고침 후 다시 확인하세요.")
            if current and (current["status"] == "retired" or (current["status"] == "approved" and policy.status != "retired")):
                raise ValueError("정책 상태가 변경되었습니다. 새로고침 후 다시 확인하세요.")
            others = [AccountingPolicy.model_validate(row) for row in c.execute(
                select(t.data).where(t.company_id == self.company_id, t.id != policy.id)).scalars()]
            for other in others:
                if other.series != policy.series:
                    continue
                if other.version == policy.version:
                    raise ValueError("같은 정책의 버전은 중복될 수 없습니다.")
                if policy.status == other.status == "approved" and (
                    policy.effective_from <= (other.effective_to or date.max)
                    and other.effective_from <= (policy.effective_to or date.max)
                ):
                    raise ValueError("같은 정책의 승인된 적용기간이 겹칩니다. 기존 버전을 철회한 뒤 기간을 조정한 새 버전을 등록하세요.")
            c.execute(delete(db.policies).where(t.company_id == self.company_id, t.id == policy.id))
            c.execute(insert(db.policies).values(company_id=self.company_id, id=policy.id, data=policy.model_dump(mode="json")))
        return policy

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
        t, e = db.transactions.c, db.entries.c
        with self.engine.begin() as c:
            c.execute(delete(db.entries).where(e.company_id == self.company_id, e.tx_id.like(f"T-{bank}-%")))
            return c.execute(delete(db.transactions).where(t.company_id == self.company_id, t.bank == bank)).rowcount

    # Bank entries ------------------------------------------------------------
    def entries(self) -> dict[str, BankEntry]:
        e = db.entries.c
        with self.engine.connect() as c:
            rows = c.execute(select(e.data).where(e.company_id == self.company_id)).all()
        found = [BankEntry.model_validate(r.data) for r in rows]
        return {x.tx_id: x for x in found}

    def save_entries(self, items: list[BankEntry]) -> None:
        if not items:
            return
        e = db.entries.c
        with self.engine.begin() as c:
            c.execute(delete(db.entries).where(e.company_id == self.company_id, e.tx_id.in_([x.tx_id for x in items])))
            c.execute(insert(db.entries), [
                {"company_id": self.company_id, "tx_id": x.tx_id, "data": x.model_dump(mode="json")} for x in items
            ])

    # Company documents ---------------------------------------------------------
    def documents(self) -> dict[str, dict]:
        d = db.documents.c
        with self.engine.connect() as c:
            rows = c.execute(select(d.kind, d.file_name, d.uploaded_at).where(d.company_id == self.company_id)).all()
        return {r.kind: {"file_name": r.file_name, "uploaded_at": r.uploaded_at.isoformat(timespec="seconds")} for r in rows}

    def document_file(self, kind: str) -> StoredFile | None:
        d = db.documents.c
        with self.engine.connect() as c:
            row = c.execute(select(d.file_name, d.content_type, d.content).where(d.company_id == self.company_id, d.kind == kind)).first()
        return StoredFile(row.file_name, row.content_type, bytes(row.content)) if row else None

    def save_document(self, kind: str, filename: str, content: bytes, content_type: str) -> None:
        d = db.documents.c
        with self.engine.begin() as c:
            c.execute(delete(db.documents).where(d.company_id == self.company_id, d.kind == kind))
            c.execute(insert(db.documents).values(company_id=self.company_id, kind=kind, file_name=filename, content_type=content_type, content=content))

    # Sales invoices ------------------------------------------------------------
    def sales_invoices(self) -> list[SalesInvoice]:
        t = db.sales_invoices.c
        with self.engine.connect() as c:
            rows = c.execute(select(t.data).where(t.company_id == self.company_id).order_by(t.id)).all()
        return [SalesInvoice.model_validate(r.data) for r in rows]

    def save_sales_invoice(self, invoice: SalesInvoice) -> SalesInvoice:
        t = db.sales_invoices.c
        with self.engine.begin() as c:
            c.execute(delete(db.sales_invoices).where(t.company_id == self.company_id, t.id == invoice.id))
            c.execute(insert(db.sales_invoices).values(company_id=self.company_id, id=invoice.id, data=invoice.model_dump(mode="json")))
        return invoice

    def delete_sales_invoice(self, invoice_id: str) -> int:
        t = db.sales_invoices.c
        with self.engine.begin() as c:
            return c.execute(delete(db.sales_invoices).where(t.company_id == self.company_id, t.id == invoice_id)).rowcount

    # VAT returns ---------------------------------------------------------------
    def vat_returns(self) -> list[VatReturn]:
        t = db.vat_returns.c
        with self.engine.connect() as c:
            rows = c.execute(select(t.data).where(t.company_id == self.company_id).order_by(t.id)).all()
        return [VatReturn.model_validate(r.data) for r in rows]

    def save_vat_return(self, item: VatReturn) -> VatReturn:
        t = db.vat_returns.c
        with self.engine.begin() as c:
            c.execute(delete(db.vat_returns).where(t.company_id == self.company_id, t.id == item.id))
            c.execute(insert(db.vat_returns).values(company_id=self.company_id, id=item.id, data=item.model_dump(mode="json")))
        return item

    def delete_vat_return(self, item_id: str) -> int:
        t = db.vat_returns.c
        with self.engine.begin() as c:
            return c.execute(delete(db.vat_returns).where(t.company_id == self.company_id, t.id == item_id)).rowcount

    # Secrets -------------------------------------------------------------------
    def hometax_imports(self) -> list[HometaxImport]:
        t = db.hometax_imports.c
        with self.engine.connect() as c:
            values = c.execute(select(t.data).where(t.company_id == self.company_id).order_by(t.id)).scalars()
            return [HometaxImport.model_validate(x) for x in values]

    def add_hometax_import(self, item: HometaxImport, content: bytes) -> bool:
        from sqlalchemy.exc import IntegrityError
        try:
            with self.engine.begin() as c:
                c.execute(insert(db.hometax_imports).values(company_id=self.company_id, id=item.id,
                          data=item.model_dump(mode="json"), content=content))
            return True
        except IntegrityError:
            if any(x.id == item.id for x in self.hometax_imports()):
                return False
            raise

    def hometax_file(self, item_id: str) -> StoredFile | None:
        t = db.hometax_imports.c
        with self.engine.connect() as c:
            row = c.execute(select(t.data, t.content).where(t.company_id == self.company_id, t.id == item_id)).first()
        return StoredFile(row.data["file_name"], "application/octet-stream", bytes(row.content)) if row else None

    def delete_hometax_import(self, item_id: str) -> int:
        t = db.hometax_imports.c
        with self.engine.begin() as c:
            removed = c.execute(delete(db.hometax_imports).where(t.company_id == self.company_id, t.id == item_id)).rowcount
            remaining = c.execute(select(t.data).where(t.company_id == self.company_id)).scalars()
            ids = {r["id"] for item in remaining for r in item["rows"]}
            links = db.hometax_links.c
            c.execute(delete(db.hometax_links).where(links.company_id == self.company_id, links.id.not_in(ids)))
        return removed

    def hometax_links(self) -> dict[str, str]:
        t = db.hometax_links.c
        with self.engine.connect() as c:
            return dict(c.execute(select(t.id, t.receipt_id).where(t.company_id == self.company_id)).all())

    def link_hometax(self, row_id: str, receipt_id: str) -> None:
        t = db.hometax_links.c
        with self.engine.begin() as c:
            c.execute(delete(db.hometax_links).where(t.company_id == self.company_id, t.id == row_id))
            if receipt_id:
                c.execute(insert(db.hometax_links).values(company_id=self.company_id, id=row_id, receipt_id=receipt_id))

    def secret(self, name: str) -> str:
        k = db.secrets.c
        with self.engine.connect() as c:
            return c.execute(select(k.value).where(k.company_id == self.company_id, k.name == name)).scalar() or ""

    def save_secret(self, name: str, value: str) -> None:
        k = db.secrets.c
        with self.engine.begin() as c:
            c.execute(delete(db.secrets).where(k.company_id == self.company_id, k.name == name))
            if value:
                c.execute(insert(db.secrets).values(company_id=self.company_id, name=name, value=value))

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
