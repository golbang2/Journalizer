from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    warnings: list[str] = Field(default_factory=list)
    approval_number: str | None = None
    business_number: str | None = None
    extraction_provider: str | None = None
    field_evidence: dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="after")
    def use_won_with_currency_warning(self) -> "ReceiptExtraction":
        detected_currency = self.currency.strip().upper() or "KRW"
        if detected_currency != "KRW":
            warning = (
                f"통화를 {detected_currency}로 인식했습니다. 금액은 원화(KRW) 기준으로 처리했습니다. "
                "실제 원화 결제금액인지 확인해 주세요."
            )
            if warning not in self.warnings:
                self.warnings = [*self.warnings, warning]
        self.currency = "KRW"
        return self


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


# --- Ledger workspace -------------------------------------------------------
# Amounts below are whole KRW, so they are stored as int rather than Decimal.

VatTreatment = Literal["pending", "deductible", "included"]
PaymentStatus = Literal["pending", "paid", "unpaid"]


class Account(BaseModel):
    code: str
    name: str
    normal: Literal["debit", "credit"]
    opening: int = 0
    kind: Literal["asset", "liability", "expense", "other"] = "other"


class Vendor(BaseModel):
    code: str = ""
    name: str = ""
    alias: str = ""
    business: str = ""
    # Defaults learned from confirmed receipts (or set by hand) and prefilled on new ones.
    account: str = ""
    purpose: str = ""
    auto: bool = False  # created automatically from a receipt; reviewer should check it


class BankAccount(BaseModel):
    """A company bank account; `code` is the vendor code used on 보통예금 lines."""

    code: str
    name: str
    number: str = ""
    account: str = "10300"


class Workspace(BaseModel):
    company: str = ""
    year: str = str(date.today().year)
    accounts: list[Account]
    vendors: list[Vendor] = Field(default_factory=list)
    banks: list[BankAccount] = Field(default_factory=list)


class ReceiptReview(BaseModel):
    """Fields a reviewer can edit. All optional so PATCH can send a subset."""

    merchant: str | None = None
    date: str | None = None
    total: int | None = Field(default=None, ge=0)
    purpose: str | None = None
    project: str | None = None
    owner: str | None = None
    vendor_code: str | None = None
    posting_date: str | None = None
    account: str | None = None
    vat: VatTreatment | None = None
    tax: int | None = None
    payment: PaymentStatus | None = None
    payment_date: str | None = None
    bank: str | None = None
    payment_transaction_id: str | None = None
    payment_link: str | None = None
    payable_offset: int | None = None
    payable_link: str | None = None
    evidence: str | None = None
    checked: bool | None = None


class ReceiptRecord(BaseModel):
    id: str
    file_name: str
    content_type: str = "application/octet-stream"
    uploaded_at: str
    extraction: ReceiptExtraction | None = None
    extraction_error: str | None = None
    merchant: str = ""
    date: str = ""
    total: int = 0
    original_tax: int = 0
    purpose: str = ""
    project: str = ""
    owner: str = ""
    vendor_code: str = ""
    posting_date: str = ""
    account: str = ""
    account_suggestion: str = ""
    account_suggestion_reason: str = ""
    # field name -> why it was filled automatically; cleared when the reviewer edits the field
    autofilled: dict[str, str] = Field(default_factory=dict)
    vat: VatTreatment = "pending"
    tax: int = 0
    payment: PaymentStatus = "pending"
    payment_date: str = ""
    bank: str = ""
    payment_transaction_id: str = ""
    payment_link: str = ""
    payable_offset: int = 0
    payable_link: str = ""
    evidence: str = ""
    checked: bool = False
    confirmed: bool = False
    confirmed_at: str | None = None


class BankTransaction(BaseModel):
    id: str
    bank: str
    bank_name: str
    account_number: str = ""
    posted_at: str
    kind: str = ""
    description: str = ""
    withdrawal: int = 0
    deposit: int = 0
    balance: int | None = None
    source_file: str = ""


class LedgerLine(BaseModel):
    receipt_id: str
    voucher: str
    date: str
    memo: str
    vendor: str
    vendor_code: str
    account: str
    debit: int
    credit: int


class ReceiptView(ReceiptRecord):
    """A receipt plus everything the review screen derives from it."""

    lines: list[LedgerLine]
    missing: list[str]
    notices: list[str] = Field(default_factory=list)
    balanced: bool
    duplicate_of: str = ""


class PaymentCandidate(BaseModel):
    transaction: BankTransaction
    score: float
    reasons: list[str]
    linked_receipt_id: str | None = None


class LedgerRow(LedgerLine):
    balance: int


class LedgerView(BaseModel):
    account: Account
    rows: list[LedgerRow]
    debit_total: int
    credit_total: int
    closing: int


class BulkUpdate(BaseModel):
    ids: list[str]
    changes: ReceiptReview


class BulkIds(BaseModel):
    ids: list[str] = Field(default_factory=list)
