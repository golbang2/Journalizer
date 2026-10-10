"""Prefill review fields from rules so the reviewer mostly confirms instead of typing.

Every value filled here is recorded in `receipt.autofilled` with the reason, only
empty fields are touched, and `checked` (the reviewer's attestation) is never set.
"""
from app.models import BankTransaction, ReceiptRecord, Vendor, Workspace
from app.services import ledger, matching

AUTO_PREFIX = "A"


def _key(value: str) -> str:
    return ledger.normalize_name(value)


def find_vendor(workspace: Workspace, merchant: str, business: str | None = None) -> Vendor | None:
    key = _key(merchant)
    for vendor in workspace.vendors:
        if not vendor.code:
            continue
        if key and key in {_key(vendor.name), _key(vendor.alias)}:
            return vendor
    # Bank statements truncate merchant names ("PAYPAL *ITCH IO" vs "PAYPAL *ITCH IO 4029357733 USA").
    if len(key) >= 8:
        for vendor in workspace.vendors:
            names = [k for k in (_key(vendor.name), _key(vendor.alias)) if len(k) >= 8]
            if vendor.code and any(key.startswith(k) or k.startswith(key) for k in names):
                return vendor
    digits = "".join(ch for ch in (business or "") if ch.isdigit())
    if len(digits) == 10:
        for vendor in workspace.vendors:
            if vendor.code and "".join(ch for ch in vendor.business if ch.isdigit()) == digits:
                return vendor
    return None


def ensure_vendor(workspace: Workspace, merchant: str, business: str | None = None) -> tuple[Vendor | None, bool]:
    """Return the vendor for a merchant, registering a new auto vendor when unknown."""
    if not _key(merchant):
        return None, False
    vendor = find_vendor(workspace, merchant, business)
    if vendor:
        return vendor, False
    used = {v.code for v in workspace.vendors}
    number = 1
    while f"{AUTO_PREFIX}{number:04d}" in used:
        number += 1
    vendor = Vendor(code=f"{AUTO_PREFIX}{number:04d}", name=merchant.strip(), business=business or "", auto=True)
    workspace.vendors.append(vendor)
    return vendor, True


def _fill(r: ReceiptRecord, field: str, value, reason: str) -> None:
    setattr(r, field, value)
    r.autofilled[field] = reason


def autofill(
    r: ReceiptRecord,
    workspace: Workspace,
    receipts: list[ReceiptRecord],
    transactions: list[BankTransaction],
    link: bool = True,
) -> bool:
    """Fill empty fields of an unconfirmed receipt. Returns True if the workspace changed.

    `receipts` must contain `r` itself so payment links made in a batch are seen by later receipts.
    """
    if r.confirmed:
        return False
    ext = r.extraction
    workspace_changed = False

    if not r.posting_date and r.date:
        _fill(r, "posting_date", r.date, "영수증 거래일")

    vendor = find_vendor(workspace, r.merchant, ext.business_number if ext else None) if r.vendor_code == "" else \
        next((v for v in workspace.vendors if v.code == r.vendor_code), None)
    if not r.vendor_code and r.merchant:
        vendor, created = ensure_vendor(workspace, r.merchant, ext.business_number if ext else None)
        if vendor:
            workspace_changed |= created
            _fill(r, "vendor_code", vendor.code, "새 거래처로 자동 등록" if created else f"거래처 {vendor.name}")

    if vendor and vendor.account and not r.account:
        _fill(r, "account", vendor.account, f"{vendor.name}의 기본 계정")
    if not r.account:
        code, reason = ledger.suggest_account(r, receipts)
        if code:
            _fill(r, "account", code, reason)
    if vendor and vendor.purpose and not r.purpose.strip():
        _fill(r, "purpose", vendor.purpose, f"{vendor.name}의 기본 적요")

    card_slip = bool(ext and ext.approval_number)
    if card_slip and r.vat == "pending":
        tax = int(ext.tax)
        if not ext.business_number:
            _fill(r, "vat", "included", "사업자번호 없는 해외 결제 · 공제 대상 아님")
            evidence = f"해외 카드 결제 · 사업자번호 없음 · 승인번호 {ext.approval_number}"
        elif tax > 0:
            _fill(r, "vat", "deductible", f"카드 매출전표 부가세 {tax:,}원 표시")
            _fill(r, "tax", tax, "카드 매출전표 표시 세액")
            evidence = f"카드 매출전표 · 부가세 {tax:,}원 · 승인번호 {ext.approval_number} · 사업자 {ext.business_number}"
        else:
            _fill(r, "vat", "included", "카드 매출전표 부가세 0원")
            evidence = f"카드 매출전표 · 부가세 0원 · 승인번호 {ext.approval_number} · 사업자 {ext.business_number}"
        if not r.evidence.strip():
            _fill(r, "evidence", evidence, "카드 매출전표 내용")

    if link:
        _link_payment(r, workspace, receipts, transactions)
    if link and card_slip and r.payment == "pending" and transactions and "일시불" in ext.payment_method:
        _fill(r, "payment", "unpaid", "신용카드(일시불) 결제 · 같은 날 은행 출금 없음 → 카드대금 결제 전까지 미지급")
    return workspace_changed


def _link_payment(r: ReceiptRecord, workspace: Workspace, receipts: list[ReceiptRecord], transactions: list[BankTransaction]) -> None:
    """Link a same-day, same-amount withdrawal nobody else uses."""
    if r.payment_transaction_id or r.payment_link.strip() or r.payment == "unpaid" or not transactions:
        return
    candidates = [
        c for c in matching.candidates(r, transactions, receipts, workspace, limit=50)
        if c.linked_receipt_id is None and c.transaction.posted_at[:10] == r.date
    ]
    if not candidates:
        return
    best = candidates[0]  # name match first, then earliest; equal-amount same-day rows are interchangeable
    tx = best.transaction
    reason = "같은 날 같은 금액 출금" + (" · 거래처명 일치" if "거래처명 일치" in best.reasons else "")
    _fill(r, "payment", "paid", reason)
    _fill(r, "payment_date", tx.posted_at[:10], reason)
    _fill(r, "bank", tx.bank, reason)
    _fill(r, "payment_transaction_id", tx.id, reason)
    _fill(r, "payment_link", f"{tx.bank_name} {tx.posted_at[:16].replace('T', ' ')} · {tx.description} · {tx.withdrawal:,}원", reason)


def learn(r: ReceiptRecord, workspace: Workspace) -> bool:
    """Remember the confirmed account and purpose as the vendor's defaults."""
    vendor = next((v for v in workspace.vendors if v.code == r.vendor_code), None)
    if vendor is None or (vendor.account == r.account and vendor.purpose == r.purpose):
        return False
    vendor.account, vendor.purpose = r.account, r.purpose
    return True


def duplicates(receipts: list[ReceiptRecord]) -> dict[str, str]:
    """Map each later copy of a receipt to the first one (same card approval, amount and date)."""
    first: dict[tuple, str] = {}
    result: dict[str, str] = {}
    for r in receipts:
        ext = r.extraction
        if ext and ext.approval_number:
            key = ("approval", ext.approval_number, r.total, r.date)
        elif r.total and r.date:
            key = ("file", r.file_name, _key(r.merchant), r.total, r.date)
        else:
            continue
        if key in first:
            result[r.id] = first[key]
        else:
            first[key] = r.id
    return result
