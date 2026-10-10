import base64
import json
import re
import os
from datetime import date
from decimal import Decimal

import anthropic

from app.models import ReceiptExtraction, ReceiptLineItem

_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def _media_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MEDIA_TYPES.get(ext, "image/jpeg")


def _build_content_block(filename: str, content: bytes) -> dict:
    b64 = base64.standard_b64encode(content).decode()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": _media_type(filename), "data": b64},
    }


_SYSTEM = """\
You are an expert at extracting structured data from receipt images and PDFs.
Extract all information accurately. Korean receipts are common — read Korean text as-is.
Return a single JSON object with no extra commentary."""

_PROMPT = """\
Extract the receipt data and return ONLY a valid JSON object:

{
  "merchant": "상호명 (store or restaurant name)",
  "purchased_at": "YYYY-MM-DD",
  "currency": "KRW",
  "subtotal": 25000,
  "tax": 2500,
  "total": 27500,
  "payment_method": "신용카드 / 현금 / Corporate Card / etc.",
  "category_hint": "meals",
  "confidence": 0.95,
  "line_items": [
    {"description": "품목명", "quantity": 1, "amount": 25000}
  ]
}

Rules:
- All monetary values are plain numbers (no currency symbols or commas).
- currency: 3-letter code — KRW for Korean Won, USD for US Dollar, etc.
- category_hint: one of meals / transport / office / accommodation / entertainment / other
- confidence: 0.0–1.0 reflecting extraction certainty.
- If tax is not shown separately, set it to 0.
- If subtotal is missing, compute as total − tax.
- Return only the JSON, nothing else."""


def _strip_markdown(text: str) -> str:
    """Remove ```json ... ``` fences if the model wraps its output."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    return match.group(1) if match else text


def extract_claude_receipt(filename: str, content: bytes, model: str | None = None) -> ReceiptExtraction:
    client = anthropic.Anthropic()

    response = client.messages.create(
        model=model or os.environ.get("CLAUDE_RECEIPT_MODEL", "claude-opus-4-7"),
        max_tokens=2048,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    _build_content_block(filename, content),
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    )

    raw = next(b.text for b in response.content if b.type == "text")
    data = json.loads(_strip_markdown(raw))

    line_items = [
        ReceiptLineItem(
            description=item["description"],
            quantity=Decimal(str(item.get("quantity", 1))),
            amount=Decimal(str(item["amount"])),
        )
        for item in data.get("line_items", [])
    ]

    return ReceiptExtraction(
        merchant=data["merchant"],
        purchased_at=date.fromisoformat(data["purchased_at"]),
        currency=data.get("currency") or "KRW",
        subtotal=Decimal(str(data["subtotal"])),
        tax=Decimal(str(data.get("tax", 0))),
        total=Decimal(str(data["total"])),
        payment_method=data.get("payment_method", "Unknown"),
        category_hint=data.get("category_hint", "other"),
        confidence=float(data.get("confidence", 0.8)),
        line_items=line_items,
        extraction_provider="claude",
    )


def extract_receipt(filename: str, content: bytes) -> ReceiptExtraction:
    is_pdf = filename.lower().endswith(".pdf")
    engine = os.environ.get("RECEIPT_OCR_ENGINE", "vision").lower()
    # Retain the explicitly configured PDF override for existing installations.
    if is_pdf:
        engine = os.environ.get("RECEIPT_PDF_ENGINE", engine).lower()
    if engine not in ("vision", "claude"):
        from app.services.bank_pdf_parser import ReceiptParseError
        raise ReceiptParseError("RECEIPT_OCR_ENGINE / RECEIPT_PDF_ENGINE must be vision or claude.")
    if engine == "vision":
        from app.services.bank_pdf_parser import ReceiptInputError, ReceiptParseError
        from app.services.vision_receipts import extract_vision_pdf, extract_vision_image
        try:
            return extract_vision_pdf(content) if is_pdf else extract_vision_image(content)
        except ReceiptInputError:
            raise
        except ReceiptParseError as exc:
            fallback = os.environ.get("RECEIPT_VISION_FALLBACK", "none").lower()
            if fallback == "none":
                raise
            if fallback != "claude":
                raise ReceiptParseError("RECEIPT_VISION_FALLBACK must be none or claude.") from exc
            receipt = extract_claude_receipt(filename, content, model=os.environ.get("CLAUDE_RECEIPT_MODEL", "claude-haiku-5-5"))
            receipt.warnings.append(f"Google Vision 규칙으로 확인하지 못해 Claude로 처리했습니다. 원본을 확인해 주세요. ({exc})")
            return receipt
    return extract_claude_receipt(filename, content)
