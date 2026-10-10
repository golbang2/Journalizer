"""Rank bank withdrawals that could be the payment for a receipt.

Only exact-amount withdrawals are candidates; date distance and name overlap
order them. The reviewer always makes the final link.
"""
from datetime import date

from app.models import BankTransaction, PaymentCandidate, ReceiptRecord, Workspace
from app.services.ledger import normalize_name
from app.services import invoice_review

MAX_DAYS_BEFORE = 3  # card authorisations can post before the slip date
MAX_DAYS_AFTER = 45  # transfers for invoices are often paid weeks later


def _names(r: ReceiptRecord, workspace: Workspace) -> set[str]:
    names = {r.merchant}
    names.update(v.name for v in workspace.vendors if v.code and v.code == r.vendor_code)
    names.update(v.alias for v in workspace.vendors if v.code and v.code == r.vendor_code)
    for v in workspace.vendors:
        if v.code and v.code == r.vendor_code:
            names.update(v.aliases)
            names.update(v.payment_aliases)
    return {n for n in map(normalize_name, names) if len(n) >= 2}


def candidates(
    r: ReceiptRecord,
    transactions: list[BankTransaction],
    receipts: list[ReceiptRecord],
    workspace: Workspace,
    limit: int = 5,
) -> list[PaymentCandidate]:
    try:
        receipt_day = date.fromisoformat(r.date)
    except ValueError:
        return []
    linked = {x.payment_transaction_id: x.id for x in receipts if x.payment_transaction_id and x.id != r.id}
    names = _names(r, workspace)
    results = []
    for tx in transactions:
        if tx.withdrawal != r.total or r.total <= 0:
            continue
        days = (date.fromisoformat(tx.posted_at[:10]) - receipt_day).days
        is_invoice = invoice_review.invoice(r)
        if is_invoice and not tx.posted_at.startswith(workspace.year + "-"):
            continue
        if not is_invoice and not -MAX_DAYS_BEFORE <= days <= MAX_DAYS_AFTER:
            continue
        score = 0.5
        reasons = ["금액 일치"]
        if is_invoice:
            reasons.append("같은 회계연도 출금")
        elif days == 0:
            score += 0.3
            reasons.append("같은 날 출금")
        else:
            score += 0.3 * max(0.0, 1 - abs(days) / 15)
            reasons.append(f"영수증 {'후' if days > 0 else '전'} {abs(days)}일 출금")
        description = normalize_name(tx.description)
        if any(n in description or description in n for n in names if description):
            score += 0.2
            reasons.append("거래처명 일치")
        if invoice_review.representative_matches(r, tx.description):
            score += 0.2
            reasons.append(f"공급자 대표자명 일치 · {r.extraction.representative}")
        if tx.id in linked:
            score -= 0.4
            reasons.append(f"이미 {linked[tx.id]}에 연결됨")
        results.append(PaymentCandidate(transaction=tx, score=round(score, 3), reasons=reasons, linked_receipt_id=linked.get(tx.id)))
    results.sort(key=lambda c: (-c.score, c.transaction.posted_at))
    return results[:limit]
