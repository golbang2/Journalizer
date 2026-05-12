from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ReceiptLineItem(BaseModel):
    description: str
    quantity: Decimal = Field(default=Decimal("1"))
    amount: Decimal


class ReceiptExtraction(BaseModel):
    merchant: str
    purchased_at: date
    currency: str = "KRW"
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    payment_method: str
    category_hint: str
    confidence: float = Field(ge=0, le=1)
    line_items: list[ReceiptLineItem]


class JournalLine(BaseModel):
    side: Literal["debit", "credit"]
    account_code: str
    account_name: str
    amount: Decimal
    memo: str


class JournalEntryDraft(BaseModel):
    receipt: ReceiptExtraction
    description: str
    lines: list[JournalLine]
    warnings: list[str] = []
