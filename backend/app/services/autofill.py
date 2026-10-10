"""Prefill review fields from rules so the reviewer mostly confirms instead of typing.

Every value filled here is recorded in `receipt.autofilled` with the reason, only
empty fields are touched, and `checked` (the reviewer's attestation) is never set.
"""
from datetime import date
import re

from app.models import BankTransaction, ReceiptRecord, Vendor, Workspace
from app.services import ledger, matching, invoice_review

AUTO_PREFIX = "A"


def _key(value: str) -> str:
    return ledger.normalize_name(value)


def find_vendor(workspace: Workspace, merchant: str, business: str | None = None) -> Vendor | None:
    key = _key(merchant)
    for vendor in workspace.vendors:
        if not vendor.code:
            continue
        if key and key in {_key(n) for n in invoice_review.names(vendor)}:
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


def fill_vendor_business(workspace: Workspace, vendor: Vendor, merchant: str, business: str | None) -> bool:
    """Backfill a blank number only for an unambiguous exact name/alias match."""
    if vendor.business.strip():
        return False
    compact = re.sub(r"\s+", "", business or "")
    if not re.fullmatch(r"[0-9]{3}-?[0-9]{2}-?[0-9]{5}", compact):
        return False
    key = _key(merchant)
    matches = [v for v in workspace.vendors if key and key in {_key(n) for n in invoice_review.names(v)}]
    if len(matches) != 1 or matches[0].code != vendor.code:
        return False
    digits = compact.replace("-", "")
    if any(v.code != vendor.code and re.sub(r"\D", "", v.business) == digits for v in workspace.vendors):
        return False
    vendor.business = f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return True


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
    if not vendor and not r.vendor_code and invoice_review.invoice(r):
        proposed = {}
        for candidate in matching.candidates(r, transactions, receipts, workspace, limit=len(transactions)):
            if candidate.linked_receipt_id or not invoice_review.representative_matches(r, candidate.transaction.description):
                continue
            for v in workspace.vendors:
                if v.code and _key(candidate.transaction.description) in {_key(n) for n in invoice_review.names(v)}:
                    if not v.business or invoice_review.key(v.business) == invoice_review.key(ext.business_number or ""):
                        proposed[v.code] = v
        if len(proposed) == 1:
            vendor = next(iter(proposed.values()))
            _fill(r, "vendor_code", vendor.code, f"대표자 {ext.representative}·금액 일치 · 거래처 연결 확인 필요")
    if not r.vendor_code and r.merchant:
        vendor, created = ensure_vendor(workspace, r.merchant, ext.business_number if ext else None)
        if vendor:
            workspace_changed |= created
            _fill(r, "vendor_code", vendor.code, "새 거래처로 자동 등록" if created else f"거래처 {vendor.name}")

    if vendor and ext:
        workspace_changed |= fill_vendor_business(workspace, vendor, ext.merchant, ext.business_number)

    if vendor and vendor.account and not r.account:
        _fill(r, "account", vendor.account, f"{vendor.name}의 기본 계정")
    if not r.account:
        code, reason = ledger.suggest_account(r, receipts)
        if code:
            _fill(r, "account", code, reason)
    if vendor and vendor.purpose and not r.purpose.strip():
        _fill(r, "purpose", vendor.purpose, f"{vendor.name}의 기본 적요")

    card_slip = bool(ext and ext.approval_number and not invoice_review.invoice(r))
    if invoice_review.invoice(r):
        if r.vat == "pending":
            _fill(r, "vat", "deductible" if ext.tax > 0 else "included", "전자세금계산서 표시 세액")
            _fill(r, "tax", int(ext.tax), "전자세금계산서 표시 세액")
        if not r.evidence.strip():
            _fill(r, "evidence", f"전자세금계산서 · 승인번호 {ext.approval_number} · 공급자 {ext.business_number}", "전자세금계산서")
        if not r.purpose.strip() and ext.line_items:
            _fill(r, "purpose", ext.line_items[0].description, "전자세금계산서 품목")
    if card_slip and r.vat == "pending":
        tax = int(ext.tax)
        if not ext.business_number:
            _fill(r, "vat", "included", "사업자번호 없는 해외 결제 · 공제 대상 아님")
            evidence = f"해외 카드 결제 · 사업자번호 없음 · 승인번호 {ext.approval_number}"
        elif tax > 0 and workspace.card_vat == "expense":
            _fill(r, "vat", "included", f"회사 기준 · 카드 영수증 부가세 {tax:,}원은 공제하지 않고 비용에 포함")
            evidence = f"카드 매출전표 · 부가세 {tax:,}원 비용 포함 · 승인번호 {ext.approval_number} · 사업자 {ext.business_number}"
        elif tax > 0 and r.account == ENTERTAINMENT:
            _fill(r, "vat", "included", f"회사 기준 · 접대비 부가세 {tax:,}원은 공제받을 수 없어 비용에 포함")
            evidence = f"카드 매출전표 · 접대비 · 부가세 {tax:,}원 불공제 · 승인번호 {ext.approval_number}"
        elif tax > 0:
            _fill(r, "vat", "deductible", f"회사 기준 · 카드 매출전표 부가세 {tax:,}원 공제 (부가세 신고와 맞춤)")
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


SETTLEMENT_DAYS = 3  # foreign card charges reach the account a day or two after the slip date


def _settled_later(r: ReceiptRecord, candidate) -> bool:
    """A card slip paid by a later withdrawal of the same amount to the same merchant."""
    days = (date.fromisoformat(candidate.transaction.posted_at[:10]) - date.fromisoformat(r.date)).days
    return 0 < days <= SETTLEMENT_DAYS and "거래처명 일치" in candidate.reasons


def _link_payment(r: ReceiptRecord, workspace: Workspace, receipts: list[ReceiptRecord], transactions: list[BankTransaction]) -> None:
    """Link a same-day, same-amount withdrawal nobody else uses."""
    if r.payment_transaction_id or r.payment_link.strip() or r.payment == "unpaid" or not transactions:
        return
    candidates = [
        c for c in matching.candidates(r, transactions, receipts, workspace, limit=50)
        if c.linked_receipt_id is None and (invoice_review.invoice(r) or c.transaction.posted_at[:10] == r.date or _settled_later(r, c))
    ]
    if invoice_review.invoice(r):
        vendor = next((v for v in workspace.vendors if v.code == r.vendor_code), None)
        if not vendor or invoice_review.needs_year_review(r, workspace) or invoice_review.needs_vendor_review(r, workspace):
            return
        # First links always need a person; only reuse an exact previously confirmed bank alias.
        candidates = [c for c in candidates if c.transaction.description in vendor.payment_aliases]
        if len(candidates) != 1:
            return
    if not candidates:
        return
    best = candidates[0]  # name match first, then earliest; equal-amount same-day rows are interchangeable
    tx = best.transaction
    reason = "같은 날 같은 금액 출금" + (" · 거래처명 일치" if "거래처명 일치" in best.reasons else "")
    if tx.posted_at[:10] != r.date and not invoice_review.invoice(r):
        reason = f"영수증 {tx.posted_at[:10]} 정산 출금 · 같은 금액 · 거래처명 일치 (해외 카드 결제는 하루 이틀 뒤 출금)"
    if invoice_review.invoice(r):
        reason = "이전에 확인한 통장 표시명 · 같은 금액 · 같은 회계연도"
    _fill(r, "payment", "paid", reason)
    _fill(r, "payment_date", tx.posted_at[:10], reason)
    _fill(r, "bank", tx.bank, reason)
    _fill(r, "payment_transaction_id", tx.id, reason)
    _fill(r, "payment_link", f"{tx.bank_name} {tx.posted_at[:16].replace('T', ' ')} · {tx.description} · {tx.withdrawal:,}원", reason)
    from app.services.bank_entries import card_of  # bank_entries imports this module

    card = card_of(tx, workspace)
    if card:
        _fill(r, "card", card.code, f"카드 {card.number} 사용 내역으로 출금")
    # A check card's foreign charge is booked when the money leaves, as one entry (no payable in between).
    settled_later = tx.posted_at[:10] != r.date and not invoice_review.invoice(r)
    if settled_later and not (card and card.payable) and r.posting_date in ("", r.date):  # still the default (the slip date)
        _fill(r, "posting_date", tx.posted_at[:10], f"해외 카드 결제 · 통장 출금일 {tx.posted_at[:10]}에 기장")


ENTERTAINMENT = "81300"  # 접대비: its VAT is never deductible


def apply_card_vat(r: ReceiptRecord, workspace: Workspace) -> bool:
    """Re-apply the company's card-slip VAT policy where a rule or the model set the VAT, never a person.

    Returns True when the receipt changed.
    """
    ext = r.extraction
    if not ext or not ext.approval_number or invoice_review.invoice(r) or not ext.business_number:
        return False
    if "vat" not in r.autofilled and r.vat != "pending":
        return False  # a person decided
    tax = int(ext.tax)
    before = (r.vat, r.tax)
    if workspace.card_vat == "deduct" and tax > 0 and r.account != ENTERTAINMENT:
        _fill(r, "vat", "deductible", f"회사 기준 · 카드 매출전표 부가세 {tax:,}원 공제 (부가세 신고와 맞춤)")
        _fill(r, "tax", tax, "카드 매출전표 표시 세액")
    else:
        why = "접대비 부가세는 공제받을 수 없어" if r.account == ENTERTAINMENT else "카드 영수증 부가세는 공제하지 않고"
        _fill(r, "vat", "included", f"회사 기준 · {why} 비용에 포함")
    return (r.vat, r.tax) != before


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
        if invoice_review.invoice(r) and ext.approval_number:
            # A year correction must not make another copy of the same invoice look new.
            key = ("tax_invoice", ext.approval_number, ext.business_number)
        elif ext and ext.approval_number:
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
