"""Extract standard Hometax electronic tax invoices from OCR labels and coordinates.

Keep the printed date, including a misread year; review against the workspace year
happens later. Never use the approval number to silently correct the date.
"""
from datetime import date
from decimal import Decimal
import re

from app.models import ReceiptExtraction, ReceiptLineItem
from app.services.bank_pdf_parser import ReceiptParseError, _anchor, _evidence, _lines, _render, _words


def parse_tax_invoice(annotation):
    words = _words(annotation)
    lines = _lines(words)
    written = _anchor(lines, "작성일자")
    headings = _lines([w for w in words if abs(w.y - written.y) < written.height])
    anchors = {"purchased_at": written, "subtotal": _anchor(headings, "공급가액"),
               "tax": _anchor(headings, "세액"), "total": _anchor(lines, "합계금액")}
    evidence, values = {}, {}
    for key, anchor in anchors.items():
        # Summary values lie below the heading, before the next column heading.
        row = [w for w in words if abs(w.y - anchor.y) < anchor.height and w.left > anchor.right]
        right = min((w.left for w in row), default=anchor.right + anchor.height * 15)
        selected = [w for w in words if anchor.bottom < w.y < anchor.bottom + anchor.height * 5
                    and anchor.left - anchor.height * 3 < (w.left + w.right) / 2 < right]
        candidates = _lines(selected)
        if not candidates:
            raise ReceiptParseError(f"Tax invoice field is missing: {key}")
        chosen = candidates[0]
        raw = re.sub(r"\s+", "", _render(chosen))
        if key == "purchased_at":
            if not re.fullmatch(r"\d{4}[-/.]\d{2}[-/.]\d{2}", raw):
                raise ReceiptParseError("Tax invoice date is ambiguous.")
            try:
                values[key] = date.fromisoformat(raw.replace("/", "-").replace(".", "-"))
            except ValueError as exc:
                raise ReceiptParseError("Tax invoice date is invalid.") from exc
        else:
            chosen = [w for w in chosen if re.fullmatch(r"(?:\d{1,3}(?:[,.]\d{3})+|\d+)", w.text)]
            if len(chosen) != 1:
                raise ReceiptParseError(f"Tax invoice amount is ambiguous: {key}")
            raw = chosen[0].text
            if not re.fullmatch(r"(?:\d{1,3}(?:[,.]\d{3})+|\d+)", raw):
                raise ReceiptParseError(f"Tax invoice amount is ambiguous: {key}")
            values[key] = Decimal(re.sub(r"[,.]", "", raw))
        evidence[key] = _evidence(chosen)
    if values["subtotal"] + values["tax"] != values["total"]:
        raise ReceiptParseError("Tax invoice amounts do not reconcile.")

    approval_label = _anchor(lines, "승인번호")
    approval_words = [w for w in words if w.left > approval_label.right
                      and abs(w.y - approval_label.y) < approval_label.height]
    approval = re.sub(r"\s+", "", _render(approval_words))
    if not re.fullmatch(r"\d{8}-\d{8}-\d{8}", approval):
        raise ReceiptParseError("Tax invoice approval number is ambiguous.")
    # The supplier is the left panel, ending at the approval heading above it.
    supplier = [w for w in words if w.right < approval_label.left and approval_label.bottom < w.y < written.top]
    supplier_lines = _lines(supplier)
    name_label = _anchor(supplier_lines, "상호")
    rep_label = _anchor(supplier_lines, "성명")
    business_words = [w for w in supplier if w.y < name_label.top and re.fullmatch(r"\d{3}-\d{2}-\d{5}", w.text)]
    if len(business_words) != 1:
        raise ReceiptParseError("Tax invoice supplier business number is ambiguous.")
    name_words = [w for w in supplier if business_words[0].left - rep_label.height < w.left < rep_label.left
                  and abs(w.y - rep_label.y) < rep_label.height * .7]
    rep_words = [w for w in supplier if w.left > rep_label.right and abs(w.y - rep_label.y) < rep_label.height * .7
                 and w.height < rep_label.height * 1.5]
    merchant, representative = _render(name_words), _render(rep_words)
    if not merchant or not representative:
        raise ReceiptParseError("Tax invoice supplier name or representative is missing.")
    for key, chosen in (("merchant", name_words), ("representative", rep_words),
                        ("business_number", business_words), ("approval_number", approval_words)):
        evidence[key] = _evidence(chosen)

    items = []
    item_label = _anchor(lines, "품목", optional=True)
    spec_label = _anchor(lines, "규격", optional=True)
    if item_label and spec_label:
        item_words = [w for w in words if written.left + written.height * 4 < w.left < spec_label.left
                      and item_label.bottom < w.y < item_label.bottom + item_label.height * 5]
        item_rows = _lines(item_words)
        if len(item_rows) == 1:
            items = [ReceiptLineItem(description=_render(item_rows[0]), amount=values["subtotal"])]
    return ReceiptExtraction(
        merchant=merchant, representative=representative, document_type="tax_invoice",
        purchased_at=values["purchased_at"], subtotal=values["subtotal"], tax=values["tax"], total=values["total"],
        business_number=business_words[0].text, approval_number=approval, payment_method="청구" if "청구" in annotation.get("text", "") else "세금계산서",
        category_hint="other", confidence=min(e["confidence"] for e in evidence.values()), line_items=items,
        extraction_provider="google_vision", field_evidence=evidence,
    )
