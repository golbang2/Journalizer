"""Journal entries for bank rows, and the cross-check with receipts.

Every company bank row becomes one entry. A row paid for a receipt is booked by that
receipt instead (the receipt carries the evidence), so the same payment is never booked twice.
Mechanical facts are prefilled here by rule: card rows, transfers between company
accounts, and cancellations of an earlier purchase. The accounting judgment itself
(which account, how much VAT) comes from `ai_judge` and is shown for a person to check.
"""
from datetime import date
import re

from app.models import BankAccount, BankEntry, BankTransaction, EntryView, LedgerLine, ReceiptRecord, SalesInvoice, VatReturn, Workspace
from app.services import ledger
from app.services.autofill import find_vendor
from app.services.ledger import CARD_PAYABLE_ACCOUNT, CASH_ACCOUNT, FEE_ACCOUNT, VAT_ACCOUNT, VAT_PAYABLE_ACCOUNT, normalize_name

CARD_ROW = re.compile(r"^(\d{4})\s+(.+)$")
CANCEL = re.compile(r"^취소\s*")
TRANSFER_FEE_LIMIT = 5_000  # an interbank transfer withdraws the amount plus at most this fee
CANCEL_WINDOW_DAYS = 90
# Autofill reasons from the company's own history or policy; the AI never overrides these.
HISTORY_REASONS = ("기본 계정", "이전 확정", "같은 계정", "회사 기준")


def counterparty(tx: BankTransaction) -> str:
    """'2462 취소 한국철도공사' -> '한국철도공사'."""
    match = CARD_ROW.match(tx.description.strip())
    name = match.group(2) if match else tx.description.strip()
    return CANCEL.sub("", name).strip()


def card_of(tx: BankTransaction, workspace: Workspace):
    match = CARD_ROW.match(tx.description.strip())
    if not match:
        return None
    return next((c for c in workspace.cards if c.number == match.group(1) and (not c.bank or c.bank == tx.bank)), None)


def is_cancel(tx: BankTransaction) -> bool:
    return tx.deposit > 0 and "취소" in tx.description


def _bank(workspace: Workspace, code: str) -> BankAccount:
    return next((b for b in workspace.banks if b.code == code), None) or BankAccount(code=code, name=code)


def _day(tx: BankTransaction) -> str:
    return tx.posted_at[:10]


def covering(transactions: list[BankTransaction], entries: dict[str, BankEntry], receipts: list[ReceiptRecord]) -> dict[str, str]:
    """Bank row id -> what books it instead: a confirmed receipt, or the withdrawal side of a transfer.

    A linked receipt that is not confirmed yet does not take the row over, so the bank side is never left unbooked.
    """
    result = {r.payment_transaction_id: r.id for r in receipts if r.payment_transaction_id and r.confirmed}
    for entry in entries.values():
        if entry.transfer_to and not entry.skip:
            result.setdefault(entry.transfer_to, entry.tx_id)
    return result


def _fill(entry: BankEntry, field: str, value, reason: str) -> None:
    setattr(entry, field, value)
    entry.autofilled[field] = reason


# The bank's own charges and interest, not a transfer someone made.
BANK_OWN = re.compile(r"이자|수수료|OTP|인증서|결산")
CARD_KIND = re.compile(r"체크|카드")
# Interbank transfers often add a 500/1,000원 fee to a round amount (200,500 = 200,000 + 500).
TRANSFER_FEES = (500, 1000)
TRANSFER_KINDS = re.compile(r"홈뱅킹|인터넷|전자망|타행|모바일|스마트|폰뱅킹|이체")


def card_like(tx: BankTransaction, entry: BankEntry) -> bool:
    return bool(entry.card or CARD_ROW.match(tx.description.strip()) or CARD_KIND.search(tx.kind) or CARD_KIND.search(tx.description))


def requires_person(tx: BankTransaction, entry: BankEntry, covered_by: str) -> bool:
    """A transfer with no receipt behind it: the reason for the money can only come from a person."""
    if covered_by or entry.skip or entry.transfer_to or card_like(tx, entry) or is_cancel(tx):
        return False
    return not BANK_OWN.search(tx.description)


def _invoice_total(invoice: SalesInvoice) -> int:
    return invoice.supply + invoice.tax


def _collects(tx: BankTransaction, invoice: SalesInvoice) -> bool:
    name = normalize_name(counterparty(tx))
    names = {normalize_name(x) for x in (invoice.customer, invoice.representative) if x}
    return tx.deposit == _invoice_total(invoice) and tx.posted_at[:10] >= invoice.issued and (
        not name or any(n and (n in name or name in n) for n in names))


TAX_OFFICE = re.compile(r"세무서|국세|부가세|국고")


def _settles(tx: BankTransaction, r: VatReturn) -> str:
    """The account a tax-office row settles for this return: 미수금 (refund in) or 미지급세금 (tax paid out)."""
    due = r.output_tax - r.input_tax
    if not TAX_OFFICE.search(tx.description) or tx.posted_at[:10] < r.filed:
        return ""
    if due < 0 and tx.deposit == -due:
        return "12000"
    if due > 0 and tx.withdrawal == due:
        return "26100"
    return ""


def prefill(transactions: list[BankTransaction], entries: dict[str, BankEntry], workspace: Workspace,
            invoices: list[SalesInvoice] = (), vat_returns: list[VatReturn] = ()) -> list[BankEntry]:
    """Create missing entries and fill rule-based facts on unconfirmed ones. Returns the entries that changed."""
    changed: dict[str, BankEntry] = {}
    ordered = sorted(transactions, key=lambda t: t.posted_at)
    targets = {e.transfer_to for e in entries.values() if e.transfer_to}

    for tx in ordered:
        entry = entries.get(tx.id)
        if entry is None:
            entry = entries[tx.id] = BankEntry(tx_id=tx.id, memo=tx.description)
            changed[tx.id] = entry
        if entry.confirmed:
            continue
        before = entry.model_dump_json()
        name = counterparty(tx)
        if not entry.vendor and name:
            _fill(entry, "vendor", name, "통장 거래 상대방 표시")
        if not entry.vendor_code and entry.vendor:
            vendor = find_vendor(workspace, entry.vendor)
            if vendor:
                _fill(entry, "vendor_code", vendor.code, f"거래처 {vendor.name}")
                if vendor.account and not entry.account and tx.withdrawal:
                    _fill(entry, "account", vendor.account, f"{vendor.name}의 기본 계정")
        card = card_of(tx, workspace)
        if card and tx.withdrawal and not entry.card and "card" not in entry.autofilled:
            _fill(entry, "card", card.code, f"카드 {card.number} 사용 내역")

        if tx.withdrawal and not entry.transfer_to and not entry.account:
            target = _transfer_target(tx, ordered, targets)
            if target:
                bank = _bank(workspace, target.bank)
                _fill(entry, "account", bank.account, f"같은 날 {bank.name} 계좌로 입금된 회사 계좌 간 이체")
                _fill(entry, "transfer_to", target.id, f"{bank.name} {target.posted_at[11:16]} 입금 {target.deposit:,}원")
                _fill(entry, "fee", tx.withdrawal - target.deposit, "이체액과 입금액 차이 · 이체 수수료")
                if not tx.description.strip():
                    _fill(entry, "memo", "계좌간이체", "적요 없는 이체")
                targets.add(target.id)
        if (tx.withdrawal and not entry.transfer_to and not entry.fee and "fee" not in entry.autofilled
                and not card_like(tx, entry) and TRANSFER_KINDS.search(tx.kind)
                and tx.withdrawal >= 10_000 and tx.withdrawal % 10_000 in TRANSFER_FEES):
            _fill(entry, "fee", tx.withdrawal % 10_000, f"{tx.withdrawal:,}원 = {tx.withdrawal - tx.withdrawal % 10_000:,}원 + 이체 수수료 {tx.withdrawal % 10_000:,}원")
        # An issued invoice or a filed VAT return is a fact: it replaces the model's guess, never a person's choice.
        replaceable = not entry.account or (entry.autofilled.get("account", "").startswith("AI") and entry.account != "10800")
        if tx.deposit and replaceable:
            invoice = next((i for i in invoices if _collects(tx, i)), None)
            if invoice:
                _fill(entry, "account", "10800", f"{invoice.issued} {invoice.customer} 매출 {_invoice_total(invoice):,}원 회수")
                _fill(entry, "vendor", invoice.customer, "매출 거래처")
        if replaceable:
            for r in vat_returns:
                account = _settles(tx, r)
                if account and account != entry.account:
                    kind = "환급금 입금" if account == "12000" else "납부"
                    _fill(entry, "account", account, f"{r.period_start[:7]}~{r.period_end[:7]} 부가세 신고 {kind} · 신고 정리 전표와 연결")
                    break
        if entry.model_dump_json() != before:
            changed[tx.id] = entry

    # Cancellations take the account of the purchase they reverse, once that purchase is classified.
    for tx in ordered:
        entry = entries[tx.id]
        if entry.confirmed or not tx.deposit or (entry.account and "account" not in entry.autofilled):
            continue
        original = _cancelled_purchase(tx, ordered)
        if original is None:
            continue
        source = entries.get(original.id)
        if source and source.account and source.account != entry.account:
            _fill(entry, "account", source.account, f"{_day(original)} {counterparty(original)} {original.withdrawal:,}원 결제의 취소 · 원거래와 같은 계정")
            if source.vendor and not entry.vendor_code:
                entry.vendor_code = source.vendor_code
            changed[tx.id] = entry
    return list(changed.values())


def _transfer_target(tx: BankTransaction, ordered: list[BankTransaction], used: set[str]) -> BankTransaction | None:
    for other in ordered:
        if other.bank == tx.bank or not other.deposit or other.id in used or _day(other) != _day(tx):
            continue
        if 0 <= tx.withdrawal - other.deposit <= TRANSFER_FEE_LIMIT:
            return other
    return None


def _cancelled_purchase(tx: BankTransaction, ordered: list[BankTransaction]) -> BankTransaction | None:
    name = normalize_name(counterparty(tx))
    if not name:
        return None
    day = date.fromisoformat(_day(tx))
    candidates = [
        o for o in ordered
        if o.withdrawal == tx.deposit and o.bank == tx.bank and o.posted_at <= tx.posted_at
        and normalize_name(counterparty(o)) == name and (day - date.fromisoformat(_day(o))).days <= CANCEL_WINDOW_DAYS
    ]
    return candidates[-1] if candidates else None


def lines(entry: BankEntry, tx: BankTransaction, workspace: Workspace, transactions: dict[str, BankTransaction]) -> list[LedgerLine]:
    if entry.skip:
        return []
    bank = _bank(workspace, tx.bank)
    cash = bank.account or CASH_ACCOUNT
    memo = entry.memo or tx.description
    out: list[LedgerLine] = []

    def add(account: str, debit: int, credit: int, vendor: str, code: str) -> None:
        if debit or credit:
            out.append(LedgerLine(receipt_id=tx.id, voucher=tx.id, date=_day(tx), memo=memo, vendor=vendor,
                                  vendor_code=code, account=account, debit=debit, credit=credit))

    account = entry.account or "미선택"
    if tx.withdrawal:
        amount = tx.withdrawal
        target = transactions.get(entry.transfer_to) if entry.transfer_to else None
        if target:
            other = _bank(workspace, target.bank)
            add(other.account or CASH_ACCOUNT, target.deposit, 0, other.name, other.code)
            add(FEE_ACCOUNT, amount - target.deposit, 0, bank.name, bank.code)
        else:
            add(account, amount - entry.tax - entry.fee, 0, entry.vendor, entry.vendor_code)
            add(VAT_ACCOUNT, entry.tax, 0, entry.vendor, entry.vendor_code)
            add(FEE_ACCOUNT, entry.fee, 0, bank.name, bank.code)
        card = ledger.card(workspace, entry.card)
        if card and card.payable and not target:
            add(CARD_PAYABLE_ACCOUNT, 0, amount, card.name, card.code)
            add(CARD_PAYABLE_ACCOUNT, amount, 0, card.name, card.code)
        add(cash, 0, amount, bank.name, bank.code)
    else:
        amount = tx.deposit
        add(cash, amount, 0, bank.name, bank.code)
        add(account, 0, amount - entry.tax, entry.vendor, entry.vendor_code)
        kind = next((a.kind for a in workspace.accounts if a.code == entry.account), "")
        add(VAT_PAYABLE_ACCOUNT if kind == "revenue" else VAT_ACCOUNT, 0, entry.tax, entry.vendor, entry.vendor_code)
    return out


def missing(entry: BankEntry, tx: BankTransaction, workspace: Workspace, transactions: dict[str, BankTransaction]) -> list[str]:
    if entry.skip:
        return []
    items = []
    codes = {a.code for a in workspace.accounts}
    amount = tx.withdrawal or tx.deposit
    if entry.transfer_to:
        target = transactions.get(entry.transfer_to)
        if target is None or not target.deposit or target.deposit > amount:
            items.append("이체 상대 계좌 입금 거래")
        return items
    if not entry.account:
        items.append("계정")
    elif entry.account not in codes:
        items.append("회사 계정표에 있는 계정")
    elif entry.account == _bank(workspace, tx.bank).account:
        items.append("이체 상대 계좌 입금 거래")
    if entry.tax < 0 or entry.fee < 0 or entry.tax + entry.fee > amount:
        items.append("유효한 부가세·수수료")
    if not tx.posted_at.startswith(workspace.year + "-"):
        items.append("회계연도 안의 거래")
    return items


def checks(tx: BankTransaction, receipt: ReceiptRecord | None, entry: BankEntry) -> list[str]:
    """Receipt ↔ bank cross-check, shown next to the row."""
    if receipt is None:
        if tx.withdrawal and not entry.transfer_to and not entry.skip:
            return ["영수증 없음 · 통장 내역으로 분개"]
        return []
    found = []
    found.append("금액 일치" if receipt.total == tx.withdrawal else f"금액 차이 · 영수증 {receipt.total:,}원 / 출금 {tx.withdrawal:,}원")
    try:
        days = (date.fromisoformat(_day(tx)) - date.fromisoformat(receipt.date)).days
        found.append("같은 날" if days == 0 else f"영수증 {'후' if days > 0 else '전'} {abs(days)}일 출금")
    except ValueError:
        found.append("영수증 날짜 없음")
    a, b = normalize_name(receipt.merchant), normalize_name(counterparty(tx))
    found.append("상호 일치" if a and b and (a in b or b in a) else f"상호 다름 · {receipt.merchant} / {counterparty(tx)}")
    found.append("영수증 확정" if receipt.confirmed else "영수증 검토 중")
    return found


def views(transactions: list[BankTransaction], entries: dict[str, BankEntry], receipts: list[ReceiptRecord], workspace: Workspace) -> list[EntryView]:
    by_id = {t.id: t for t in transactions}
    covered = covering(transactions, entries, receipts)
    linked = {r.payment_transaction_id: r for r in receipts if r.payment_transaction_id}
    result = []
    for tx in sorted(transactions, key=lambda t: t.posted_at):
        entry = entries.get(tx.id) or BankEntry(tx_id=tx.id, memo=tx.description)
        by = covered.get(tx.id, "")
        receipt = linked.get(tx.id)
        body = [] if by else lines(entry, tx, workspace, by_id)
        gaps = [] if by else missing(entry, tx, workspace, by_id)
        result.append(EntryView(
            **entry.model_dump(), transaction=tx, lines=body, missing=gaps,
            balanced=sum(x.debit for x in body) == sum(x.credit for x in body),
            covered_by=by, checks=checks(tx, receipt, entry), requires_person=requires_person(tx, entry, by),
        ))
    return result


def posted_lines(transactions: list[BankTransaction], entries: dict[str, BankEntry], receipts: list[ReceiptRecord], workspace: Workspace) -> list[LedgerLine]:
    return [
        line
        for v in views(transactions, entries, receipts, workspace)
        if v.confirmed and not v.covered_by and not v.missing
        for line in v.lines
    ]


def history_reason(reason: str) -> bool:
    return any(word in reason for word in HISTORY_REASONS)
