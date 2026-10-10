"""VAT sanity checks against what the law and the filed return say.

- A corporation is always a general taxpayer, so its card slip should show VAT unless it sold
  exempt goods or services (rail, bus, medical, books …). A 0-VAT slip from one is re-read.
- The return's 부속서류 (수령명세서, 세금계산서합계표) list the VAT claimed per supplier; the
  books are reconciled against them by business number.
"""
from collections import defaultdict
from decimal import Decimal
import re

from app.models import ReceiptRecord, ScheduleRow, VatReturn

# 사업자등록번호 middle digits: 81·85-88 영리법인, 84 외국법인 (82 비영리법인 is often VAT-exempt, e.g. 한국철도공사).
CORPORATE = {"81", "84", "85", "86", "87", "88"}
EXEMPT = re.compile(r"철도|코레일|고속버스|버스|여객|운송|택시|지하철|도시철도|교통공사|공항철도|병원|의원|약국|치과|한의원|"
                    r"도서|서점|문고|우체국|우정|보험|은행|금융|학원|교육|대학|법원|등기|세무서|구청|시청|주민센터")
BN = re.compile(r"(\d{3})-?(\d{2})-?(\d{5})")


def business_key(value: str) -> str:
    match = BN.search(value or "")
    return "".join(match.groups()) if match else ""


def corporate(business_number: str) -> bool:
    match = BN.search(business_number or "")
    return bool(match) and match.group(2) in CORPORATE


def needs_vat_check(r: ReceiptRecord) -> bool:
    ext = r.extraction
    return bool(
        ext and ext.approval_number and ext.document_type != "tax_invoice" and not r.vat_checked
        and corporate(ext.business_number or "") and int(ext.tax) == 0 and r.total >= 1000
        and not EXEMPT.search(r.merchant or ext.merchant)
    )


def set_vat(r: ReceiptRecord, tax: int, note: str) -> bool:
    """Record a corrected VAT on the slip. The card-VAT policy is re-applied by the caller."""
    r.vat_checked = note
    ext = r.extraction
    if not ext or tax <= 0 or tax > r.total // 11 + 2 or int(ext.tax) == tax:
        return False
    ext.tax = Decimal(tax)
    ext.subtotal = Decimal(r.total - tax)
    r.original_tax = tax
    return True


def estimate(total: int) -> int:
    """VAT inside a VAT-inclusive total, as Korean POS slips print it: total − ⌈total / 1.1⌉ = ⌊total / 11⌋."""
    return total // 11


# --- Return schedules -----------------------------------------------------------------------------

def _period_receipts(receipts: list[ReceiptRecord], vr: VatReturn) -> list[ReceiptRecord]:
    return [r for r in receipts if vr.period_start <= (r.posting_date or r.date) <= vr.period_end and r.extraction]


def reconcile(vr: VatReturn, receipts: list[ReceiptRecord]) -> dict:
    """Per supplier: what the return claimed against what the books deducted."""
    books: dict[str, dict] = defaultdict(lambda: {"name": "", "count": 0, "tax": 0, "zero": []})
    for r in _period_receipts(receipts, vr):
        key = business_key(r.extraction.business_number or "")
        if not key:
            continue
        row = books[key]
        row["name"] = row["name"] or r.merchant
        if r.vat == "deductible" and r.tax:
            row["count"] += 1
            row["tax"] += r.tax
        elif int(r.extraction.tax) == 0:
            row["zero"].append(r.id)
    filed: dict[str, ScheduleRow] = {}
    for row in vr.schedule:
        key = business_key(row.business_number)
        if key in filed:
            filed[key] = filed[key].model_copy(update={"count": filed[key].count + row.count, "tax": filed[key].tax + row.tax,
                                                       "supply": filed[key].supply + row.supply})
        else:
            filed[key] = row
    rows = []
    for key in sorted(set(books) | set(filed)):
        b, f = books.get(key), filed.get(key)
        book_tax = b["tax"] if b else 0
        filed_tax = f.tax if f else 0
        if f and book_tax == filed_tax:
            status = "일치"
        elif f and not book_tax:
            status = "장부 누락"
        elif not f and book_tax:
            status = "신고 누락"
        elif not f:
            continue  # a supplier with no VAT on either side
        else:
            status = "금액 차이"
        rows.append({
            "business_number": f"{key[:3]}-{key[3:5]}-{key[5:]}", "name": (f.name if f and f.name else (b or {}).get("name", "")),
            "kind": f.kind if f else "", "filed_count": f.count if f else 0, "filed_tax": filed_tax,
            "book_count": b["count"] if b else 0, "book_tax": book_tax, "difference": filed_tax - book_tax,
            "status": status, "zero_vat_receipts": (b or {}).get("zero", []),
        })
    unlisted = sum(r.tax for r in _period_receipts(receipts, vr) if r.vat == "deductible" and not business_key(r.extraction.business_number or ""))
    return {
        "rows": rows,
        "filed_total": sum(r.tax for r in vr.schedule),
        "book_total": sum(r["book_tax"] for r in rows) + unlisted,
        "return_input_tax": vr.input_tax,
        "matched": sum(r["status"] == "일치" for r in rows),
    }


def fixes_from_schedule(vr: VatReturn, receipts: list[ReceiptRecord]) -> dict[str, int]:
    """Receipt id -> VAT, where the filed schedule pins down a slip the OCR read as 0.

    Only when it is unambiguous: one 0-VAT slip for that supplier gets the filed amount, or several
    whose 1/11 estimates add up exactly to the filed amount.
    """
    result = reconcile(vr, receipts)
    by_id = {r.id: r for r in receipts}
    fixes: dict[str, int] = {}
    for row in result["rows"]:
        missing = row["difference"]
        zero = [by_id[i] for i in row["zero_vat_receipts"]]
        if missing <= 0 or not zero:
            continue
        if len(zero) == 1:
            fixes[zero[0].id] = missing
            continue
        estimates = {r.id: estimate(r.total) for r in zero}
        if sum(estimates.values()) == missing:
            fixes.update(estimates)
    return fixes
