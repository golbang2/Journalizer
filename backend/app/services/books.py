"""All posted lines of the year: confirmed receipts, confirmed bank entries, and the year-end closing."""
from app.models import Account, BankEntry, BankTransaction, LedgerLine, ReceiptRecord, SalesInvoice, VatReturn, Workspace
from app.services import bank_entries, ledger
from app.services.autofill import AUTO_PREFIX

RECEIVABLE = "10800"  # 외상매출금
TAX_REFUND, TAX_PAYABLE = "12000", "26100"  # 미수금 (부가세 환급), 미지급세금
VAT_RETURN_PREFIX = "V-"
INCOME_SUMMARY = "40000"  # 손익
RETAINED, RETAINED_CARRIED = "37700", "37500"  # 미처분이익잉여금 -> 이월이익잉여금
DEFICIT, DEFICIT_CARRIED = "37800", "37600"  # 미처리결손금 -> 이월결손금


def lines(receipts: list[ReceiptRecord], transactions: list[BankTransaction], entries: dict[str, BankEntry], workspace: Workspace,
          invoices: list[SalesInvoice] = (), vat_returns: list[VatReturn] = ()) -> list[LedgerLine]:
    body = (ledger.posted_lines(receipts, workspace) + bank_entries.posted_lines(transactions, entries, receipts, workspace)
            + [line for invoice in invoices for line in sales_lines(invoice, workspace)])
    body += [line for r in vat_returns for line in vat_lines(r, body)]
    tx_by_id = {t.id: t for t in transactions}
    bank_memo = {r.id: tx_by_id[r.payment_transaction_id].description for r in receipts
                 if r.payment_transaction_id in tx_by_id and tx_by_id[r.payment_transaction_id].description.strip()}
    body = [_as_printed(x, bank_memo) for x in body if x.date.startswith(workspace.year + "-")]
    # Bank rows keep the statement's time order within a day; receipt vouchers follow by number.
    times = {t.id: t.posted_at for t in transactions}
    body.sort(key=lambda x: (x.date, times.get(x.voucher, x.date + "T99"), x.voucher))
    return body + closing(body, workspace)


def sales_lines(invoice: SalesInvoice, workspace: Workspace) -> list[LedgerLine]:
    """외상매출금 / 매출 + 부가세예수금 on the issue date."""
    memo = f"{invoice.item or '매출'} {invoice.supply:,}"
    out = []
    for account, debit, credit in ((RECEIVABLE, invoice.supply + invoice.tax, 0), (invoice.revenue_account, 0, invoice.supply),
                                   (ledger.VAT_PAYABLE_ACCOUNT, 0, invoice.tax)):
        if debit or credit:
            out.append(LedgerLine(receipt_id=invoice.id, voucher=invoice.id, date=invoice.issued, memo=memo, vendor=invoice.customer,
                                  vendor_code="", account=account, debit=debit, credit=credit))
    return out


def booked_vat(body: list[LedgerLine], start: str, end: str) -> tuple[int, int]:
    """매출세액 and 매입세액 as booked in a filing period: 부가세예수금 credits and 부가세대급금 debits."""
    inside = [x for x in body if start <= x.date <= end and not x.voucher.startswith(VAT_RETURN_PREFIX)]
    output = sum(x.credit for x in inside if x.account == ledger.VAT_PAYABLE_ACCOUNT)
    input_ = sum(x.debit for x in inside if x.account == ledger.VAT_ACCOUNT)
    return output, input_


def vat_lines(r: VatReturn, body: list[LedgerLine]) -> list[LedgerLine]:
    """부가세 신고 정리 on the filing date.

    The period's booked 매입세액 is first brought to what the return claimed (unclaimed VAT becomes cost,
    VAT claimed but booked as cost comes back), then 부가세예수금 and 부가세대급금 are offset and the
    difference is left as 미수금 (refund) or 미지급세금 (tax due).
    """
    _, booked_input = booked_vat(body, r.period_start, r.period_end)
    memo = f"{r.period_start[:7]}~{r.period_end[:7]} 부가세 신고"
    out: list[LedgerLine] = []

    def add(account: str, debit: int, credit: int, text: str) -> None:
        if debit or credit:
            out.append(LedgerLine(receipt_id=r.id, voucher=r.id, date=r.filed, memo=text, vendor="세무서", vendor_code="",
                                  account=account, debit=debit, credit=credit))

    gap = booked_input - r.input_tax
    add(r.nondeductible_account, max(gap, 0), max(-gap, 0), f"{memo} · 공제받지 않은 매입세액 비용 대체" if gap > 0 else f"{memo} · 공제받은 매입세액 비용에서 환입")
    add(ledger.VAT_ACCOUNT, max(-gap, 0), max(gap, 0), f"{memo} · 매입세액 신고액으로 조정")
    add(ledger.VAT_PAYABLE_ACCOUNT, r.output_tax, 0, f"{memo} · 부가세대급금과 상계")
    add(ledger.VAT_ACCOUNT, 0, r.input_tax, f"{memo} · 부가세예수금과 상계")
    due = r.output_tax - r.input_tax
    add(TAX_PAYABLE, 0, max(due, 0), f"{memo} · 납부할 세액")
    add(TAX_REFUND, max(-due, 0), 0, f"{memo} · 환급받을 세액")
    return out


def _as_printed(line: LedgerLine, bank_memo: dict[str, str]) -> LedgerLine:
    """Ledger wording: a receipt paid from the bank shows the bank's 적요; auto-made vendor codes stay off the ledger."""
    update = {}
    if line.vendor_code.startswith(AUTO_PREFIX):
        update["vendor_code"] = ""
    if line.receipt_id in bank_memo:
        update["memo"] = bank_memo[line.receipt_id]
    return line.model_copy(update=update) if update else line


def closing(body: list[LedgerLine], workspace: Workspace) -> list[LedgerLine]:
    """Close revenue and expense accounts into 손익, then 손익 into retained earnings or deficit."""
    kinds = {a.code: a.kind for a in accounts(workspace, body)}
    balances: dict[str, int] = {}
    for x in body:
        if kinds.get(x.account) in ("revenue", "expense"):
            balances[x.account] = balances.get(x.account, 0) + x.debit - x.credit
    if not any(balances.values()):
        return []
    day = f"{workspace.year}-12-31"
    out: list[LedgerLine] = []

    def add(account: str, debit: int, credit: int, memo: str) -> None:
        if debit or credit:
            out.append(LedgerLine(receipt_id="closing", voucher="결산", date=day, memo=memo, vendor="", vendor_code="",
                                  account=account, debit=debit, credit=credit))

    revenue = expense = 0
    for code in sorted(balances):
        balance = balances[code]  # debit-positive: revenue is normally negative here
        add(code, max(-balance, 0), max(balance, 0), "손익계정에 대체")
        if kinds[code] == "revenue":
            revenue -= balance
        else:
            expense += balance
    add(INCOME_SUMMARY, 0, revenue, "수익에서 대체")
    add(INCOME_SUMMARY, expense, 0, "비용에서 대체")
    net = revenue - expense
    if net >= 0:
        add(INCOME_SUMMARY, net, 0, "당기순이익 잉여금에 대체")
        add(RETAINED, 0, net, "당기순손익 대체")
        add(RETAINED, net, 0, "이월이익잉여금으로 대체")
        add(RETAINED_CARRIED, 0, net, "차기이월미처분이익잉여금")
    else:
        add(INCOME_SUMMARY, 0, -net, "당기순손실 결손금에 대체")
        add(DEFICIT, -net, 0, "당기순손익 대체")
        add(DEFICIT, 0, -net, "이월결손금으로 대체")
        add(DEFICIT_CARRIED, -net, 0, "차기이월미처리결손금")
    return out


def accounts(workspace: Workspace, body: list[LedgerLine]) -> list[Account]:
    """The chart plus any account a line uses that the chart lacks (e.g. closing accounts on an older chart)."""
    chart = list(workspace.accounts)
    have = {a.code for a in chart}
    standard = {c: (n, s, k) for c, n, s, k in ledger.STANDARD_ACCOUNTS}
    for code in sorted({x.account for x in body} - have):
        name, normal, kind = standard.get(code, (code, "debit", "other"))
        chart.append(Account(code=code, name=name, normal=normal, kind=kind))
    return chart
