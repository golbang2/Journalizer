"""Journal lines, review gaps, and account ledgers for reviewed receipts.

The rules mirror the bank-payment / 외상매입금 flow designed in the wireframe:
an expense is posted on the posting date, paid either the same day (보통예금)
or later (외상매입금 then settled), optionally clearing an earlier payable.
"""
import re
from app.services import invoice_review, vat_checks

from app.models import (
    Account,
    BankAccount,
    Card,
    LedgerLine,
    LedgerRow,
    LedgerView,
    ReceiptRecord,
    ReceiptView,
    Workspace,
)

CASH_ACCOUNT = "10300"
VAT_ACCOUNT = "13500"
PAYABLE_ACCOUNT = "25100"
CARD_PAYABLE_ACCOUNT = "25300"
VAT_PAYABLE_ACCOUNT = "25500"
FEE_ACCOUNT = "83100"

DEFAULT_ACCOUNTS = [
    ("10300", "보통예금", "debit", "asset"),
    ("13500", "부가세대급금", "debit", "asset"),
    ("25100", "외상매입금", "credit", "liability"),
    ("25300", "미지급금", "credit", "liability"),
    ("81100", "복리후생비", "debit", "expense"),
    ("81200", "여비교통비", "debit", "expense"),
    ("81300", "접대비", "debit", "expense"),
    ("81700", "세금과공과금", "debit", "expense"),
    ("81900", "지급임차료", "debit", "expense"),
    ("83000", "소모품비", "debit", "expense"),
    ("83100", "지급수수료", "debit", "expense"),
    ("85300", "외주개발비", "debit", "expense"),
]

# Accounts that bank-only bookings and the year-end closing need besides the expense set above.
STANDARD_ACCOUNTS = DEFAULT_ACCOUNTS + [
    ("10800", "외상매출금", "debit", "asset"),
    ("12000", "미수금", "debit", "asset"),
    ("25500", "부가세예수금", "credit", "liability"),
    ("26100", "미지급세금", "credit", "liability"),
    ("33100", "자본금", "credit", "equity"),
    ("37500", "이월이익잉여금", "credit", "equity"),
    ("37600", "이월결손금", "debit", "equity"),
    ("37700", "미처분이익잉여금", "credit", "equity"),
    ("37800", "미처리결손금", "debit", "equity"),
    ("40000", "손익", "credit", "other"),
    ("41200", "매출", "credit", "revenue"),
    ("90100", "이자수익", "credit", "revenue"),
    ("93000", "잡이익", "credit", "revenue"),
]

# OCR category hints are only suggestions; the reviewer always picks the account.
CATEGORY_ACCOUNTS = {
    "meals": ("81100", "식음료 영수증입니다. 직원 식대면 복리후생비, 거래처 접대면 접대비로 바꾸세요."),
    "entertainment": ("81300", "접대성 지출로 인식했습니다."),
    "transport": ("81200", "교통비로 인식했습니다."),
    "accommodation": ("81200", "숙박비로 인식했습니다. 출장 여부를 확인하세요."),
    "office": ("83000", "사무용품 구매로 인식했습니다."),
}


def default_workspace() -> Workspace:
    return Workspace(
        accounts=[Account(code=c, name=n, normal=s, kind=k) for c, n, s, k in STANDARD_ACCOUNTS],
        banks=[BankAccount(code="99000", name="우리은행"), BankAccount(code="99001", name="광주은행")],
    )


def missing_standard_accounts(workspace: Workspace) -> list[Account]:
    have = {a.code for a in workspace.accounts}
    return [Account(code=c, name=n, normal=s, kind=k) for c, n, s, k in STANDARD_ACCOUNTS if c not in have]


def account_name(workspace: Workspace, code: str) -> str:
    known = next((a.name for a in workspace.accounts if a.code == code), "")
    return known or next((n for c, n, _, _ in STANDARD_ACCOUNTS if c == code), code)


def card(workspace: Workspace, code: str) -> Card | None:
    return next((c for c in workspace.cards if c.code == code), None) if code else None


def _bank(workspace: Workspace, code: str) -> BankAccount | None:
    return next((b for b in workspace.banks if b.code == code), None)


def journal(r: ReceiptRecord, workspace: Workspace) -> list[LedgerLine]:
    lines: list[LedgerLine] = []
    bank = _bank(workspace, r.bank)
    cash_account = bank.account if bank else CASH_ACCOUNT
    paid_card = card(workspace, r.card) if r.payment == "paid" else None
    via_card = bool(paid_card and paid_card.payable)
    later = r.payment == "paid" and (r.payment_date != r.posting_date or via_card)
    payable = CARD_PAYABLE_ACCOUNT if via_card else PAYABLE_ACCOUNT

    def add(account: str, debit: int, credit: int, day: str = r.posting_date, payment: bool = False) -> None:
        if not (debit or credit):
            return
        if account == cash_account:
            vendor, code = (bank.name if bank else "출금 계좌 미선택"), r.bank
        elif via_card and account == payable:
            vendor, code = paid_card.name, paid_card.code
        else:
            vendor, code = r.merchant, r.vendor_code
        lines.append(LedgerLine(
            receipt_id=r.id,
            voucher=f"{r.id}-{'02' if payment else '01'}",
            date=day,
            memo=f"{r.merchant} 대금 지급" if payment else (r.purpose or "사용 목적 미입력"),
            vendor=vendor,
            vendor_code=code,
            account=account,
            debit=debit,
            credit=credit,
        ))

    tax = r.tax if r.vat == "deductible" else 0
    offset = r.payable_offset if r.payment == "paid" and not later else 0
    add(r.account or "미선택", r.total - tax - offset, 0)
    add(VAT_ACCOUNT, tax, 0)
    add(PAYABLE_ACCOUNT, offset, 0)
    add(cash_account if r.payment == "paid" and not later else payable, 0, r.total)
    if later:
        add(payable, r.total, 0, r.payment_date, True)
        add(cash_account, 0, r.total, r.payment_date, True)
    return lines


def missing(r: ReceiptRecord, workspace: Workspace) -> list[str]:
    year = workspace.year
    expense_codes = {a.code for a in workspace.accounts if a.kind == "expense"}
    items: list[str] = []
    if not r.merchant.strip():
        items.append("거래처명")
    if r.total <= 0:
        items.append("결제 금액")
    if not r.purpose.strip():
        items.append("사용 목적")
    if not r.vendor_code.strip():
        items.append("거래처 코드")
    if not r.account:
        items.append("비용 계정")
    elif r.account not in expense_codes:
        items.append("회사 계정표에 있는 비용 계정")
    if not r.posting_date.startswith(year + "-"):
        items.append("회계연도에 맞는 전기일")
    if invoice_review.needs_year_review(r, workspace):
        items.append(f"OCR 연도 확인 ({r.extraction.purchased_at.year}년 / 회계연도 {year}년)")
    if invoice_review.needs_vendor_review(r, workspace):
        items.append("거래처 연결 확인 (세금계산서 공급자·통장 표시명)")
    if r.vat == "pending":
        items.append("부가세 처리 판단")
    if r.vat == "deductible" and not 0 <= r.tax <= r.total:
        items.append("유효한 공제 부가세")
    if not r.evidence.strip():
        items.append("증빙·부가세 검토 근거")
    if r.payment == "pending":
        items.append("지급 상태")
    if r.payment == "paid":
        if not r.payment_date or r.payment_date < r.posting_date or not r.payment_date.startswith(year + "-"):
            items.append("지급일 (전기일 이후, 같은 회계연도)")
        if not _bank(workspace, r.bank):
            items.append("출금 계좌")
    tax = r.tax if r.vat == "deductible" else 0
    if r.payable_offset < 0 or r.payable_offset + tax > r.total:
        items.append("유효한 기존 채무 정산액")
    if r.payable_offset > 0 and (r.payment != "paid" or r.payment_date != r.posting_date):
        items.append("기존 채무 정산은 당일 지급에서만 입력")
    if r.payable_offset > 0 and not r.payable_link.strip():
        items.append("기존 채무 전표 연결")
    if not r.checked:
        items.append("원본·거래 내역 확인")
    return items


def notices(r: ReceiptRecord) -> list[str]:
    """Worth checking, but never blocks confirmation."""
    items = []
    if vat_checks.needs_vat_check(r):
        items.append("법인 가맹점 영수증인데 부가세 0원 · 다시 읽기 필요")
    if r.payment == "paid" and not (r.payment_transaction_id or r.payment_link.strip()):
        items.append("출금 거래 미연결 · 통장 출금과 대조하지 않았습니다")
    return items


def view(r: ReceiptRecord, workspace: Workspace, duplicate_of: str = "") -> ReceiptView:
    lines = journal(r, workspace)
    debit = sum(line.debit for line in lines)
    credit = sum(line.credit for line in lines)
    gaps = missing(r, workspace)
    if duplicate_of:
        gaps.insert(0, f"중복 영수증 정리 ({duplicate_of}와 같은 영수증)")
    return ReceiptView(**r.model_dump(), lines=lines, missing=gaps, notices=notices(r), balanced=debit == credit, duplicate_of=duplicate_of,
                       year_review_required=invoice_review.needs_year_review(r, workspace),
                       vendor_review_required=invoice_review.needs_vendor_review(r, workspace))


def normalize_name(value: str) -> str:
    value = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사", "", value)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).upper()


def suggest_account(r: ReceiptRecord, history: list[ReceiptRecord]) -> tuple[str, str]:
    """Prefer what the reviewer already chose for this vendor over OCR hints."""
    key = normalize_name(r.merchant)
    for past in sorted(history, key=lambda x: x.confirmed_at or "", reverse=True):
        if past.id == r.id or not past.confirmed or not past.account:
            continue
        if (r.vendor_code and past.vendor_code == r.vendor_code) or (key and normalize_name(past.merchant) == key):
            return past.account, f"같은 거래처의 이전 확정 전표 {past.id}에서 사용한 계정입니다."
    hint = r.extraction.category_hint if r.extraction else ""
    return CATEGORY_ACCOUNTS.get(hint, ("", ""))


def posted_lines(receipts: list[ReceiptRecord], workspace: Workspace) -> list[LedgerLine]:
    lines = [
        line
        for r in receipts
        if r.confirmed and not missing(r, workspace)
        for line in journal(r, workspace)
    ]
    return sorted(lines, key=lambda line: (line.date, line.voucher))


def ledger(account: Account, receipts: list[ReceiptRecord], workspace: Workspace) -> LedgerView:
    return ledger_view(account, posted_lines(receipts, workspace))


def ledger_view(account: Account, lines: list[LedgerLine]) -> LedgerView:
    balance = account.opening
    rows = []
    for line in lines:
        if line.account != account.code:
            continue
        balance += line.debit - line.credit if account.normal == "debit" else line.credit - line.debit
        rows.append(LedgerRow(**line.model_dump(), balance=balance))
    return LedgerView(
        account=account,
        rows=rows,
        debit_total=sum(row.debit for row in rows),
        credit_total=sum(row.credit for row in rows),
        closing=balance,
    )
