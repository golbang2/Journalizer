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
    document_type: Literal["receipt", "tax_invoice"] = "receipt"
    representative: str = ""
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
    kind: Literal["asset", "liability", "equity", "revenue", "expense", "other"] = "other"


class Vendor(BaseModel):
    code: str = ""
    name: str = ""
    alias: str = ""
    aliases: list[str] = Field(default_factory=list)
    payment_aliases: list[str] = Field(default_factory=list)
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


class Card(BaseModel):
    """A company card. Statement rows starting with its number ("2462 스타벅스") are card purchases.

    With `payable`, a purchase is booked through 미지급금 (expense / 미지급금, then 미지급금 / 보통예금).
    A check card takes the money at once, so it books expense / 보통예금 directly (payable off).
    """

    code: str
    name: str
    number: str = ""
    bank: str = ""  # bank account code the card settles from
    payable: bool = False


class CompanyProfile(BaseModel):
    """What the registration documents say about the company; context for the AI's accounting judgment."""

    company: str = ""
    business_number: str = ""
    representative: str = ""
    opened: str = ""
    business_type: str = ""  # 업태 · 종목
    capital: int = 0
    shareholders: list[str] = Field(default_factory=list)
    officers: list[str] = Field(default_factory=list)
    summary: str = ""
    error: str = ""  # the documents could not be read; not retried until they change


class Workspace(BaseModel):
    policies: list["AccountingPolicy"] = Field(default_factory=list)
    profile: CompanyProfile | None = None
    company: str = ""
    year: str = str(date.today().year)
    accounts: list[Account]
    vendors: list[Vendor] = Field(default_factory=list)
    banks: list[BankAccount] = Field(default_factory=list)
    cards: list[Card] = Field(default_factory=list)
    # Card slips: "deduct" separates the deductible VAT into 부가세대급금, as the VAT return claims it;
    # "expense" keeps it in the cost (only for a company that does not claim card-slip VAT).
    card_vat: Literal["expense", "deduct"] = "deduct"
    # Past bookings from an earlier year's ledger, used as examples for the AI's judgment.
    precedents: list["Precedent"] = Field(default_factory=list)
    precedent_source: str = ""
    # Which LLM does the accounting judgment: "anthropic", "openai" or "gemini", and the model picked from its list.
    ai_provider: str = "anthropic"
    ai_model: str = ""


class PrecedentSource(BaseModel):
    file_name: str
    sheet: str
    row_number: int
    date: str = ""  # Preserve the original label; do not invent a year for MM-DD.


class Precedent(BaseModel):
    text: str  # 적요 or 거래처 as it appeared
    account: str
    count: int = 1
    sources: list[PrecedentSource] = Field(default_factory=list)


class VatReturn(BaseModel):
    """A filed 부가세 신고: offsets 부가세예수금 against 부가세대급금 and leaves the refund (미수금) or tax due (미지급세금)."""

    id: str
    period_start: str
    period_end: str
    filed: str  # date of the settlement entry (신고일)
    output_tax: int = 0  # 매출세액
    input_tax: int = 0  # 공제받은 매입세액
    nondeductible_account: str = "83000"  # where VAT booked but not claimed goes
    note: str = ""
    schedule: list["ScheduleRow"] = Field(default_factory=list)  # 부속서류 rows, per supplier
    schedule_file: str = ""


class ScheduleRow(BaseModel):
    """One supplier line of a filed 신용카드매출전표 수령명세서 or 매입처별 세금계산서합계표."""

    kind: Literal["card", "invoice"] = "card"
    business_number: str = ""
    name: str = ""
    count: int = 0
    supply: int = 0
    tax: int = 0


class HometaxRow(BaseModel):
    id: str
    kind: Literal["card", "invoice"]
    date: str
    business_number: str
    merchant: str
    approval_number: str = ""
    card_number: str = ""
    supply: int
    tax: int
    total: int
    service: int = 0
    deduction: Literal["deductible", "nondeductible", "unknown"] = "unknown"
    deduction_text: str = ""
    status: str = ""
    adjustment: bool = False
    sheet: str
    row_number: int


class HometaxImport(BaseModel):
    id: str
    kind: Literal["card", "invoice"]
    file_name: str
    uploaded_at: str
    rows: list[HometaxRow]


class SalesInvoice(BaseModel):
    """매출 세금계산서: 외상매출금 / 매출 + 부가세예수금 on the issue date; the customer's deposit collects it."""

    id: str
    file_name: str = ""
    issued: str = ""
    customer: str = ""
    business_number: str = ""
    representative: str = ""
    item: str = ""
    supply: int = 0
    tax: int = 0
    revenue_account: str = "41200"
    note: str = ""


class PolicyRule(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=30)
    account: str = Field(min_length=1, max_length=30)
    exceptions: list[str] = Field(default_factory=list, max_length=30)
    direction: Literal["", "출금", "입금"] = ""
    document_type: Literal["", "receipt", "tax_invoice"] = ""
    minimum: int = Field(default=0, ge=0)
    maximum: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_rule(self) -> "PolicyRule":
        self.terms = list(dict.fromkeys(t.strip().lower() for t in self.terms if t.strip()))
        self.exceptions = list(dict.fromkeys(t.strip().lower() for t in self.exceptions if t.strip()))
        if not self.terms or any(len(t) > 100 for t in [*self.terms, *self.exceptions]):
            raise ValueError("검색어는 1~100자로 입력하세요.")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("최대 금액은 최소 금액 이상이어야 합니다.")
        return self


class PolicyInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    series: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    kind: Literal["accounting", "tax"] = "accounting"
    source: str = Field(min_length=1, max_length=500)
    locator: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    effective_from: date
    effective_to: date | None = None
    rules: list[PolicyRule] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_period(self) -> "PolicyInput":
        for field in ("title", "series", "version", "source", "locator", "text"):
            value = getattr(self, field).strip()
            if not value:
                raise ValueError("정책 정보는 공백일 수 없습니다.")
            setattr(self, field, value)
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("적용 종료일은 시작일 이후여야 합니다.")
        return self


class AccountingPolicy(PolicyInput):
    id: str
    status: Literal["draft", "approved", "retired"] = "draft"
    created_at: str
    approved_at: str = ""


class RecommendationEvidence(BaseModel):
    """Server-selected context snapshot, not a claim that the model cited this source."""

    source_type: Literal["receipt", "bank", "precedent", "policy"]
    source_id: str
    description: str
    account: str
    account_name: str = ""
    date: str = ""
    amount: int | None = None
    purpose: str = ""
    reason: str
    path: list[str] = Field(default_factory=list)
    sources: list[PrecedentSource] = Field(default_factory=list)
    policy_version: str = ""
    policy_kind: str = ""
    source: str = ""
    locator: str = ""
    policy_text: str = ""
    exceptions: list[str] = Field(default_factory=list)
    context_truncated: bool = False


class AiSuggestion(BaseModel):
    """One accounting recommendation from the model. Shown highlighted until a person confirms it."""

    account: str = ""
    tax: int = 0
    vendor: str = ""
    purpose: str = ""
    reason: str = ""
    confidence: float = 0
    model: str = ""
    at: str = ""
    evidence: list[RecommendationEvidence] = Field(default_factory=list)
    retrieval_version: str = ""
    review_required: bool = False


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
    confirm_year: bool | None = None
    confirm_vendor: bool | None = None


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
    card: str = ""  # card code when the linked withdrawal is a card purchase
    vat_checked: str = ""  # result of re-reading a corporate slip that showed 0 VAT
    payable_offset: int = 0
    payable_link: str = ""
    evidence: str = ""
    checked: bool = False
    year_confirmed_for: str = ""
    confirmed: bool = False
    confirmed_at: str | None = None
    ai: AiSuggestion | None = None


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


class BankEntry(BaseModel):
    """The journal entry for one bank row. The bank side is implied by the transaction.

    Withdrawal: Dr `account` (amount - tax - fee), Dr 부가세대급금 tax, Dr 지급수수료 fee / Cr bank.
    Deposit:    Dr bank / Cr `account` (amount - tax), Cr 부가세예수금 (revenue) or 부가세대급금 tax.
    `account` equal to the bank's own account with `transfer_to` set is a transfer between company accounts.
    """

    tx_id: str
    account: str = ""
    tax: int = 0
    fee: int = 0
    memo: str = ""
    vendor_code: str = ""
    vendor: str = ""
    card: str = ""
    transfer_to: str = ""  # deposit row in another company account
    skip: bool = False  # not a booking (e.g. a row the bank reversed the same day)
    autofilled: dict[str, str] = Field(default_factory=dict)
    ai: AiSuggestion | None = None
    confirmed: bool = False
    confirmed_at: str | None = None


class EntryReview(BaseModel):
    account: str | None = None
    tax: int | None = Field(default=None, ge=0)
    fee: int | None = Field(default=None, ge=0)
    memo: str | None = None
    vendor_code: str | None = None
    vendor: str | None = None
    card: str | None = None
    transfer_to: str | None = None
    skip: bool | None = None


class LedgerLine(BaseModel):
    receipt_id: str  # source id: a receipt (R-001), a bank row (T-...), or "closing"
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
    year_review_required: bool = False
    vendor_review_required: bool = False


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


class EntryView(BankEntry):
    """A bank row, its entry, and the cross-check against receipts."""

    transaction: BankTransaction
    lines: list[LedgerLine]
    missing: list[str]
    balanced: bool
    covered_by: str = ""  # receipt id, or the withdrawal of a company-account transfer, that books this row
    checks: list[str] = Field(default_factory=list)  # receipt ↔ bank cross-check findings
    requires_person: bool = False  # a transfer with no receipt: never posted automatically


Workspace.model_rebuild()
VatReturn.model_rebuild()
