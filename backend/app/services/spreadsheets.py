"""Bounded readers for uploaded spreadsheets (.xlsx, .xls, .csv).

A 15MB upload can still expand to gigabytes: zip members decompress far beyond their packed
size and a single cell at column XFD would make a 16,384-wide row. Every reader here counts
what it builds and stops with SpreadsheetError once a limit is passed. Real bank exports and
계정별원장 files are a few thousand rows and under 30 columns, so the limits are generous.

The .xlsx reader parses sheet XML directly because some bank exports ship stylesheets that
openpyxl refuses to load.
"""
import csv
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

MAX_SHEETS = 100
MAX_ROWS = 100_000          # all sheets together
MAX_COLS = 256
MAX_CELLS = 1_000_000       # all sheets together
MAX_TEXT = 32_767           # Excel's own per-cell limit
MAX_SHARED_STRINGS = 500_000
MAX_XML_BYTES = 64 * 1024 * 1024        # one decompressed zip member
MAX_UNZIPPED_BYTES = 128 * 1024 * 1024  # every member read, together

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF = re.compile(r"([A-Z]{1,3})([1-9][0-9]{0,6})")


class SpreadsheetError(ValueError):
    pass


Rows = list[list[str]]


class _Budget:
    def __init__(self):
        self.rows = self.cells = self.unzipped = 0

    def row(self) -> None:
        self.rows += 1
        if self.rows > MAX_ROWS:
            raise SpreadsheetError(f"행이 너무 많습니다 (최대 {MAX_ROWS:,}행).")

    def cell(self, count: int = 1) -> None:
        self.cells += count
        if self.cells > MAX_CELLS:
            raise SpreadsheetError(f"셀이 너무 많습니다 (최대 {MAX_CELLS:,}개).")


def _text(value: str) -> str:
    if len(value) > MAX_TEXT:
        raise SpreadsheetError(f"셀 하나에 {MAX_TEXT:,}자를 넘는 내용이 있습니다.")
    return value


def _column(index: int, filled: bool) -> bool:
    """True if the column is usable; empty cells far to the right are formatting and are skipped."""
    if index < MAX_COLS:
        return True
    if filled:
        raise SpreadsheetError(f"열이 너무 많습니다 (최대 {MAX_COLS}열).")
    return False


# --- .xlsx ----------------------------------------------------------------------

class _Member(io.RawIOBase):
    """A zip member that refuses to decompress past the per-file and whole-archive limits."""

    def __init__(self, archive: zipfile.ZipFile, name: str, budget: _Budget):
        self.stream, self.budget, self.read_bytes = archive.open(name), budget, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self.stream.read(len(buffer))
        if self.read_bytes == 0 and b"<!DOCTYPE" in data[:4096].upper():
            raise SpreadsheetError("엑셀 파일 안에 허용되지 않는 XML 선언이 있습니다.")
        self.read_bytes += len(data)
        self.budget.unzipped += len(data)
        if self.read_bytes > MAX_XML_BYTES or self.budget.unzipped > MAX_UNZIPPED_BYTES:
            raise SpreadsheetError("압축을 푼 엑셀 내용이 너무 큽니다.")
        buffer[:len(data)] = data
        return len(data)


def _events(archive: zipfile.ZipFile, name: str, budget: _Budget):
    return ET.iterparse(io.BufferedReader(_Member(archive, name, budget)), events=("start", "end"))


def _shared_strings(archive: zipfile.ZipFile, budget: _Budget) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    shared: list[str] = []
    for event, elem in _events(archive, "xl/sharedStrings.xml", budget):
        if event == "end" and elem.tag == f"{_NS}si":
            shared.append(_text("".join(t.text or "" for t in elem.iter(f"{_NS}t"))))
            elem.clear()
            if len(shared) > MAX_SHARED_STRINGS:
                raise SpreadsheetError("엑셀 공유 문자열이 너무 많습니다.")
    return shared


def _sheet_names(archive: zipfile.ZipFile, budget: _Budget) -> list[tuple[str, str]]:
    """(title, zip member) in workbook order; falls back to the worksheet files by name."""
    names = set(archive.namelist())
    found: list[tuple[str, str]] = []
    if {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"} <= names:
        targets = {}
        for event, elem in _events(archive, "xl/_rels/workbook.xml.rels", budget):
            if event == "end" and elem.tag == f"{_PKG_REL_NS}Relationship":
                target = elem.get("Target", "")
                path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
                targets[elem.get("Id")] = path
        for event, elem in _events(archive, "xl/workbook.xml", budget):
            if event == "end" and elem.tag == f"{_NS}sheet":
                path = targets.get(elem.get(f"{_REL_NS}id"))
                if path in names:
                    found.append((elem.get("name", ""), path))
    if not found:
        found = [(n.rsplit("/", 1)[-1][:-4], n) for n in sorted(names) if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
    if len(found) > MAX_SHEETS:
        raise SpreadsheetError(f"시트가 너무 많습니다 (최대 {MAX_SHEETS}개).")
    return found


def _sheet_rows(archive: zipfile.ZipFile, name: str, shared: list[str], budget: _Budget,
                preserve_row_numbers: bool = False) -> Rows:
    rows: Rows = []
    values: list[str] = []
    container = None
    for event, elem in _events(archive, name, budget):
        if event == "start":
            if elem.tag == f"{_NS}sheetData":
                container = elem
            elif elem.tag == f"{_NS}row":
                values = []
                if preserve_row_numbers:
                    # XLSX can omit empty rows entirely. Keep source locations accurate
                    # without letting a sparse row index bypass the allocation budget.
                    position = elem.get("r", str(len(rows) + 1))
                    if not position.isdigit() or not len(rows) < int(position) <= MAX_ROWS:
                        raise SpreadsheetError("엑셀 행 번호가 올바르지 않거나 허용 범위를 넘었습니다.")
                    for _ in range(int(position) - len(rows) - 1):
                        budget.row()
                        rows.append([])
            continue
        if elem.tag == f"{_NS}c":
            budget.cell()
            raw = elem.find(f"{_NS}v")
            text = raw.text if raw is not None and raw.text else ""
            kind = elem.get("t")
            if kind == "s" and text:
                try:
                    text = shared[int(text)]
                except (ValueError, IndexError) as exc:
                    raise SpreadsheetError("엑셀 셀이 없는 공유 문자열을 가리킵니다.") from exc
            elif kind == "inlineStr":
                text = "".join(t.text or "" for t in elem.iter(f"{_NS}t"))
            ref = elem.get("r")
            if ref is None:
                col = len(values)
            else:
                match = _CELL_REF.fullmatch(ref)
                if match is None:
                    raise SpreadsheetError(f"엑셀 셀 주소가 올바르지 않습니다: {ref[:20]}")
                col = 0
                for char in match.group(1):
                    col = col * 26 + ord(char) - 64
                col -= 1
            if _column(col, bool(text)):
                values.extend([""] * (col + 1 - len(values)))
                values[col] = _text(text)
            elem.clear()
        elif elem.tag == f"{_NS}row":
            budget.row()
            rows.append(values)
            values = []
            if container is not None:
                container.clear()  # drop finished rows from the tree
    return rows


def xlsx_sheets(content: bytes, first_only: bool = False, *, preserve_row_numbers: bool = False) -> list[tuple[str, Rows]]:
    budget = _Budget()
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        shared = _shared_strings(archive, budget)
        sheets = _sheet_names(archive, budget)
        if not sheets:
            raise SpreadsheetError("엑셀(.xlsx) 파일에 시트가 없습니다.")
        if first_only:
            sheets = sheets[:1]
        return [(title, _sheet_rows(archive, name, shared, budget, preserve_row_numbers)) for title, name in sheets]
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ET.ParseError, KeyError, EOFError, NotImplementedError) as exc:
        raise SpreadsheetError("엑셀(.xlsx) 파일을 읽을 수 없습니다.") from exc


# --- .xls -----------------------------------------------------------------------

def xls_sheets(content: bytes, cell_text, first_only: bool = False) -> list[tuple[str, Rows]]:
    """`cell_text` turns an xlrd value into text; callers format numbers differently."""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
    except (xlrd.XLRDError, EOFError, AssertionError, IndexError, ValueError) as exc:
        raise SpreadsheetError("엑셀(.xls) 파일을 읽을 수 없습니다.") from exc
    if book.nsheets > MAX_SHEETS:
        raise SpreadsheetError(f"시트가 너무 많습니다 (최대 {MAX_SHEETS}개).")
    budget = _Budget()
    result = []
    for index in range(1 if first_only else book.nsheets):
        sheet = book.sheet_by_index(index)
        if sheet.ncols > MAX_COLS:
            raise SpreadsheetError(f"열이 너무 많습니다 (최대 {MAX_COLS}열).")
        rows = []
        for i in range(sheet.nrows):
            budget.row()
            budget.cell(sheet.row_len(i))
            rows.append([_text(cell_text(v)) for v in sheet.row_values(i)])
        result.append((sheet.name, rows))
        book.unload_sheet(index)
    return result


# --- .csv -----------------------------------------------------------------------

def csv_rows(content: bytes) -> Rows:
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SpreadsheetError("CSV 인코딩을 확인할 수 없습니다.")
    budget = _Budget()
    rows = []
    try:
        for row in csv.reader(io.StringIO(text)):
            budget.row()
            if len(row) > MAX_COLS and any(c.strip() for c in row[MAX_COLS:]):
                raise SpreadsheetError(f"열이 너무 많습니다 (최대 {MAX_COLS}열).")
            row = row[:MAX_COLS]
            budget.cell(len(row))
            rows.append([_text(c.strip()) for c in row])
    except csv.Error as exc:
        raise SpreadsheetError("CSV 파일을 읽을 수 없습니다.") from exc
    return rows
