"""Learn the company's booking habits from an earlier year's 계정별원장 (one sheet per account).

Each dated row gives "this 적요 / 거래처 went to this account". Bank, payable-clearing and
closing accounts say nothing about judgment, so they are skipped.
"""
from collections import Counter
import re

from app.models import Precedent
from app.services import spreadsheets

SKIP = {"10300", "25300", "13500", "25500", "40000", "37500", "37600", "37700", "37800"}
DAY = re.compile(r"^\d{1,2}[-./]\d{1,2}$")
CARD_PREFIX = re.compile(r"^\d{4}\s+")
CODE_IN_TITLE = re.compile(r"\((\d{3,5})\)\s*$")
CODE_IN_HEADER = re.compile(r"\[(\d{3})\]")


class PrecedentError(ValueError):
    pass


def _sheets(filename: str, content: bytes) -> list[tuple[str, list[list[str]]]]:
    """(title, rows) for every sheet, rows as lists of cell text."""
    name = filename.lower()
    try:
        if name.endswith(".xls"):
            return spreadsheets.xls_sheets(content, str)
        if name.endswith(".xlsx"):
            return spreadsheets.xlsx_sheets(content)
    except spreadsheets.SpreadsheetError as exc:
        raise PrecedentError(str(exc)) from exc
    raise PrecedentError("이전 장부는 계정별원장 엑셀(.xls, .xlsx)만 올릴 수 있습니다.")


def _account(title: str, rows: list[list[str]]) -> str:
    match = CODE_IN_TITLE.search(title)
    if match:
        return match.group(1).ljust(5, "0")
    for row in rows[:3]:
        for cell in row:
            found = CODE_IN_HEADER.search(cell)
            if found and "계정" in cell:
                return found.group(1) + "00"
    return ""


def read(filename: str, content: bytes, limit: int = 400) -> list[Precedent]:
    counts: Counter = Counter()
    for title, rows in _sheets(filename, content):
        account = _account(title, rows)
        if not account or account in SKIP:
            continue
        for row in rows:
            if len(row) < 4 or not DAY.match(row[0].strip()):
                continue
            text = CARD_PREFIX.sub("", row[1].strip()) or row[3].strip()
            if text and "대체" not in text:  # closing transfers are not judgments
                counts[(text, account)] += 1
    if not counts:
        raise PrecedentError("계정별원장 형식(계정마다 날짜·적요·코드·거래처·차변·대변)을 찾지 못했습니다.")
    return [Precedent(text=t, account=a, count=n) for (t, a), n in counts.most_common(limit)]
