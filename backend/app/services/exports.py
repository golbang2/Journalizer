"""CSV and Excel exports of confirmed vouchers."""
import csv
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Font

from app.models import LedgerView, ReceiptRecord, Workspace
from app.services.ledger import ledger, posted_lines

LEDGER_HEADER = ["날짜", "전표번호", "적요", "거래처 코드", "거래처", "차변", "대변", "잔액"]
JOURNAL_HEADER = ["날짜", "전표번호", "계정 코드", "계정명", "적요", "거래처 코드", "거래처", "차변", "대변"]


def _safe(value):
    """Stop spreadsheet apps from evaluating text as a formula."""
    if isinstance(value, str) and re.match(r"^[=+\-@\t\r]", value):
        return "'" + value
    return value


def _ledger_rows(view: LedgerView, year: str) -> list[list]:
    rows = [[f"{year}-01-01", "", "기초잔액", "", "", "", "", view.account.opening]]
    rows += [[r.date, r.voucher, r.memo, r.vendor_code, r.vendor, r.debit, r.credit, r.balance] for r in view.rows]
    rows.append(["", "", "합계", "", "", view.debit_total, view.credit_total, view.closing])
    return rows


def ledger_csv(view: LedgerView, workspace: Workspace) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([workspace.company, workspace.year, view.account.code, view.account.name])
    writer.writerow(LEDGER_HEADER)
    for row in _ledger_rows(view, workspace.year):
        writer.writerow([_safe(v) for v in row])
    return ("﻿" + out.getvalue()).encode("utf-8")


def workbook(receipts: list[ReceiptRecord], workspace: Workspace) -> bytes:
    names = {a.code: a.name for a in workspace.accounts}
    book = Workbook()
    sheet = book.active
    sheet.title = "분개장"
    sheet.append([f"{workspace.company} {workspace.year} 분개장"])
    sheet.append(JOURNAL_HEADER)
    for line in posted_lines(receipts, workspace):
        sheet.append([_safe(v) for v in [line.date, line.voucher, line.account, names.get(line.account, ""), line.memo, line.vendor_code, line.vendor, line.debit, line.credit]])
    for account in workspace.accounts:
        view = ledger(account, receipts, workspace)
        if not view.rows and not account.opening:
            continue
        ws = book.create_sheet(f"{account.code} {account.name}"[:31])
        ws.append([f"{workspace.company} {workspace.year} 계정별원장 · {account.code} {account.name}"])
        ws.append(LEDGER_HEADER)
        for row in _ledger_rows(view, workspace.year):
            ws.append([_safe(v) for v in row])
    for ws in book.worksheets:
        ws["A1"].font = Font(bold=True)
        for cell in ws[2]:
            cell.font = Font(bold=True)
        for column, width in zip("ABCDEFGHI", [12, 12, 12, 16, 28, 12, 18, 14, 14]):
            ws.column_dimensions[column].width = width
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
