"""Read bank transaction exports (거래내역조회) into withdrawal/deposit rows.

Supported layouts are found by their header row, not by bank name:
- 우리은행 .xlsx: No. | 거래일시 | 적요 | 기재내용 | 지급(원) | 입금(원) | 거래후 잔액(원) ...
- 광주은행 .xls: 거래일시 | 적요 | 거래구분 | 입/출금 | 출금 | 입금 | 통장잔액 ... (merged cells)

Files are read through the bounded readers in spreadsheets.py.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import re

from app.models import BankAccount, BankTransaction
from app.services import spreadsheets


class StatementParseError(ValueError):
    pass


# Header label -> field. Order matters: "입/출금" must not be read as 출금.
_COLUMNS = [
    ("거래일시", "posted_at"),
    ("기재내용", "description"),
    ("적요", "kind"),
    ("거래구분", "category"),
    ("입/출금", "direction"),
    ("지급", "withdrawal"),
    ("출금", "withdrawal"),
    ("입금", "deposit"),
    ("잔액", "balance"),
]


def _xls_text(value) -> str:
    return ("%d" % value if isinstance(value, float) and value.is_integer() else str(value)).strip()


def _rows(filename: str, content: bytes) -> list[list[str]]:
    name = filename.lower()
    try:
        if name.endswith(".xlsx"):
            return spreadsheets.xlsx_sheets(content, first_only=True)[0][1]
        if name.endswith(".xls"):
            return spreadsheets.xls_sheets(content, _xls_text, first_only=True)[0][1]
        if name.endswith(".csv"):
            return spreadsheets.csv_rows(content)
    except spreadsheets.SpreadsheetError as exc:
        raise StatementParseError(str(exc)) from exc
    raise StatementParseError("은행 거래내역은 .xlsx, .xls, .csv 파일만 지원합니다.")


def _header(rows: list[list[str]]) -> tuple[int, dict[str, tuple[int, int]]]:
    """Find the header row and each field's column span (merged cells)."""
    for index, row in enumerate(rows):
        cells = [re.sub(r"\s+", "", c) for c in row]
        if "거래일시" not in cells:
            continue
        labelled = [(col, text) for col, text in enumerate(cells) if text]
        spans: dict[str, tuple[int, int]] = {}
        for pos, (col, text) in enumerate(labelled):
            end = labelled[pos + 1][0] if pos + 1 < len(labelled) else len(row)
            for label, field in _COLUMNS:
                if label in text and field not in spans:
                    spans[field] = (col, end)
                    break
        if {"posted_at", "withdrawal", "deposit"} <= spans.keys():
            return index, spans
        raise StatementParseError("거래일시·출금·입금 열을 찾지 못했습니다.")
    raise StatementParseError("거래내역 머리글(거래일시)을 찾지 못했습니다.")


def _cell(row: list[str], span: tuple[int, int] | None) -> str:
    if span is None:
        return ""
    return next((v.strip() for v in row[span[0]:span[1]] if v.strip()), "")


def _amount(value: str) -> int:
    value = re.sub(r"[,\s원]", "", value)
    if not value:
        return 0
    try:
        return int(float(value))
    except ValueError as exc:
        raise StatementParseError(f"금액을 읽을 수 없습니다: {value}") from exc


def _timestamp(value: str) -> str | None:
    match = re.match(r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?", value)
    if not match:
        return None
    y, m, d, hh, mm, ss = match.groups()
    return datetime(int(y), int(m), int(d), int(hh or 0), int(mm or 0), int(ss or 0)).isoformat()


def detect_bank(filename: str, rows: list[list[str]], banks: list[BankAccount]) -> BankAccount | None:
    """Pick the company bank account by account number, then bank name in content or filename."""
    text = re.sub(r"\s+", "", " ".join(" ".join(r) for r in rows[:12]))
    digits = re.sub(r"\D", "", text)
    for bank in banks:
        number = re.sub(r"\D", "", bank.number)
        if number and number in digits:
            return bank
    for source in (text, re.sub(r"\s+", "", filename)):
        for bank in banks:
            if bank.name and re.sub(r"\s+", "", bank.name) in source:
                return bank
    return None


def _account_number(rows: list[list[str]]) -> str:
    for row in rows[:12]:
        joined = " ".join(row)
        if "계좌번호" in joined:
            match = re.search(r"\d[\d-]{8,}\d", joined)
            if match:
                return match.group()
    return ""


def parse_statement(filename: str, content: bytes, banks: list[BankAccount], bank_code: str | None = None) -> tuple[BankAccount, list[BankTransaction]]:
    rows = _rows(filename, content)
    header, spans = _header(rows)
    bank = next((b for b in banks if b.code == bank_code), None) if bank_code else detect_bank(filename, rows, banks)
    if bank is None:
        raise StatementParseError("어느 회사 계좌의 거래내역인지 알 수 없습니다. 회사 기준에서 은행 계좌를 등록하거나 계좌를 선택하세요.")
    number = _account_number(rows)
    transactions = []
    for row in rows[header + 1:]:
        posted_at = _timestamp(_cell(row, spans.get("posted_at")))
        if posted_at is None:
            continue  # totals, notices, and blank rows
        withdrawal = _amount(_cell(row, spans.get("withdrawal")))
        deposit = _amount(_cell(row, spans.get("deposit")))
        balance_text = _cell(row, spans.get("balance"))
        balance = _amount(balance_text) if balance_text else None
        kind = _cell(row, spans.get("kind"))
        description = _cell(row, spans.get("description")) or kind
        category = _cell(row, spans.get("category"))
        if "description" not in spans and category and category != description:
            kind = category
        if not (withdrawal or deposit):
            continue
        fingerprint = "|".join([bank.code, posted_at, description, str(withdrawal), str(deposit), str(balance)])
        transactions.append(BankTransaction(
            id=f"T-{bank.code}-{hashlib.sha1(fingerprint.encode()).hexdigest()[:10]}",
            bank=bank.code,
            bank_name=bank.name,
            account_number=number,
            posted_at=posted_at,
            kind=kind if kind != description else "",
            description=description,
            withdrawal=withdrawal,
            deposit=deposit,
            balance=balance,
            source_file=filename,
        ))
    if not transactions:
        raise StatementParseError("입출금 거래를 찾지 못했습니다.")
    return bank, transactions


@dataclass
class StatementInfo:
    """What a statement's header says about the account, for setting up the workspace."""

    bank_name: str
    account_number: str
    holder: str
    product: str
    transactions: list[BankTransaction]

    @property
    def opening(self) -> int | None:
        """Balance just before the earliest row (rows can be newest-first)."""
        first = min(self.transactions, key=lambda t: t.posted_at)
        if first.balance is None:
            return None
        return first.balance - first.deposit + first.withdrawal


def _labelled(rows: list[list[str]], label: str) -> str:
    """Read `label : value` inside a cell, or the next filled cell after a `label` cell."""
    for row in rows[:12]:
        for index, cell in enumerate(row):
            text = cell.strip()
            match = re.search(label + r"\s*:\s*(.+?)(?:\s{2,}|$)", text)
            if match:
                return match.group(1).strip()
            if re.sub(r"\s+", "", text) == label:
                return next((c.strip() for c in row[index + 1:] if c.strip()), "")
    return ""


def _bank_name(filename: str, rows: list[list[str]]) -> str:
    for source in (" ".join(" ".join(r) for r in rows[:12]), filename):
        match = re.search(r"([가-힣A-Za-z]{1,8}은행)", source)
        if match:
            return match.group(1)
    return ""


def read_statement(filename: str, content: bytes) -> StatementInfo:
    """Parse a statement without knowing which company account it belongs to yet."""
    rows = _rows(filename, content)
    name = _bank_name(filename, rows)
    placeholder = BankAccount(code="new", name=name or "은행")
    _, transactions = parse_statement(filename, content, [placeholder], placeholder.code)
    return StatementInfo(
        bank_name=name,
        account_number=_account_number(rows),
        holder=_labelled(rows, "예금주"),
        product=_labelled(rows, "상품명"),
        transactions=transactions,
    )


def schedule_table(filename: str, content: bytes, limit: int = 60_000) -> str:
    """A spreadsheet as tab-separated text (non-empty rows only), cut to `limit` characters for the model."""
    rows = _rows(filename, content)
    return "\n".join("\t".join(row) for row in rows if any(c.strip() for c in row))[:limit]
