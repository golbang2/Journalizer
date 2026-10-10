"""Upload size caps, paid-call quotas, bounded spreadsheet parsing and the parser sandbox."""
import io
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import limits
from app.auth import create_user, get_store
from app.main import app
from app.services import sandbox, spreadsheets
from app.services.bank_statements import StatementParseError, parse_statement
from app.services.store import Store, company_for_user
from db_helpers import fresh_engine

KWANGJU = Path(__file__).resolve().parents[2] / "test_data" / "1-2 광주은행 거래내역.xls"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def xlsx(sheet_body: str, shared: list[str] | None = None, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                                      '<sheets><sheet name="거래내역" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                                 '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{MAIN}"><sheetData>{sheet_body}</sheetData></worksheet>')
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{MAIN}">' + "".join(f"<si><t>{t}</t></si>" for t in shared) + "</sst>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buffer.getvalue()


def rows_of(content: bytes):
    return spreadsheets.xlsx_sheets(content, first_only=True)[0][1]


class SpreadsheetLimitTests(unittest.TestCase):
    def test_reads_cells_by_address_and_inline_strings(self):
        body = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>입금</t></is></c></row><row><c><v>5</v></c><c><v>6</v></c></row>'
        self.assertEqual(rows_of(xlsx(body, ["거래일시"])), [["거래일시", "", "입금"], ["5", "6"]])

    def test_far_right_cell_with_a_value_is_refused_and_empty_one_ignored(self):
        self.assertEqual(rows_of(xlsx('<row><c r="A1"><v>1</v></c><c r="XFD1" s="3"/></row>')), [["1"]])
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "열이 너무"):
            rows_of(xlsx('<row><c r="XFD1"><v>1</v></c></row>'))

    def test_invalid_cell_address_and_dangling_shared_string(self):
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "셀 주소"):
            rows_of(xlsx('<row><c r="1A"><v>1</v></c></row>'))
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "공유 문자열"):
            rows_of(xlsx('<row><c r="A1" t="s"><v>7</v></c></row>', ["only"]))

    def test_row_cell_and_text_limits(self):
        body = "".join(f'<row><c r="A{i}"><v>{i}</v></c><c r="B{i}"><v>{i}</v></c></row>' for i in range(1, 11))
        with patch.object(spreadsheets, "MAX_ROWS", 5), self.assertRaisesRegex(spreadsheets.SpreadsheetError, "행이 너무"):
            rows_of(xlsx(body))
        with patch.object(spreadsheets, "MAX_CELLS", 15), self.assertRaisesRegex(spreadsheets.SpreadsheetError, "셀이 너무"):
            rows_of(xlsx(body))
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "자를 넘는"):
            rows_of(xlsx('<row><c r="A1" t="s"><v>0</v></c></row>', ["x" * 40_000]))
        with patch.object(spreadsheets, "MAX_SHARED_STRINGS", 3), self.assertRaisesRegex(spreadsheets.SpreadsheetError, "공유 문자열이 너무"):
            rows_of(xlsx("", ["a"] * 10))

    def test_decompression_bomb_is_stopped_while_reading(self):
        content = xlsx(" " * 3_000_000)  # 3MB of XML that packs to a few KB
        self.assertLess(len(content), 100_000)
        with patch.object(spreadsheets, "MAX_XML_BYTES", 1024 * 1024), self.assertRaisesRegex(spreadsheets.SpreadsheetError, "압축을 푼"):
            rows_of(content)

    def test_doctype_is_refused(self):
        content = xlsx("", extra={"xl/sharedStrings.xml": b'<?xml version="1.0"?><!DOCTYPE sst [<!ENTITY a "aaaa">]><sst/>'})
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "XML 선언"):
            rows_of(content)

    @unittest.skipUnless(KWANGJU.exists(), "private bank statement sample is not available")
    def test_xls_row_limit(self):
        with patch.object(spreadsheets, "MAX_ROWS", 20), self.assertRaises(StatementParseError):
            parse_statement(KWANGJU.name, KWANGJU.read_bytes(), [])

    def test_csv_row_and_column_limits(self):
        csv_body = "\n".join("a,b" for _ in range(30)).encode()
        with patch.object(spreadsheets, "MAX_ROWS", 20), self.assertRaisesRegex(spreadsheets.SpreadsheetError, "행이 너무"):
            spreadsheets.csv_rows(csv_body)
        with self.assertRaisesRegex(spreadsheets.SpreadsheetError, "열이 너무"):
            spreadsheets.csv_rows(("," * 300 + "x").encode())


class SandboxTests(unittest.TestCase):
    def test_result_and_exceptions_come_back(self):
        self.assertEqual(sandbox.run(spreadsheets.csv_rows, b"a,b\n1,2"), [["a", "b"], ["1", "2"]])
        with self.assertRaises(spreadsheets.SpreadsheetError):
            sandbox.run(spreadsheets.csv_rows, b"\xff\xfe\xfa" * 3)

    def test_runaway_parser_is_killed(self):
        started = time.monotonic()
        with self.assertRaisesRegex(sandbox.SandboxError, "중단"):
            sandbox.run(time.sleep, 30, timeout=2)
        self.assertLess(time.monotonic() - started, 15)

    def test_crashed_parser_is_reported(self):
        with self.assertRaisesRegex(sandbox.SandboxError, "너무 크거나"):
            sandbox.run(os._exit, 3)


class RequestSizeTests(unittest.TestCase):
    def setUp(self):
        mini = FastAPI()

        @mini.post("/echo")
        async def echo(request: Request) -> dict:
            return {"size": len(await request.body())}

        self.client = TestClient(limits.RequestSizeLimit(mini, limit=1000))

    def test_declared_and_streamed_bodies_are_capped(self):
        self.assertEqual(self.client.post("/echo", content=b"x" * 1000).json(), {"size": 1000})
        self.assertEqual(self.client.post("/echo", content=b"x" * 1001).status_code, 413)

        def chunks():  # no Content-Length: counted while streaming
            for _ in range(5):
                yield b"x" * 400

        self.assertEqual(self.client.post("/echo", content=chunks()).status_code, 413)


class ApiLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = fresh_engine(self.tmp.name)
        user = create_user(self.engine, "owner@example.com", "owner", password="password123")
        self.store = Store(self.engine, company_for_user(self.engine, user.id))
        app.dependency_overrides[get_store] = lambda: self.store
        self.client = TestClient(app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        self.engine.dispose()
        self.tmp.cleanup()

    def upload(self, data=b"%PDF-1.4"):
        return self.client.post("/receipts", files={"file": ("slip.pdf", data, "application/pdf")})

    def test_oversized_file_is_refused_before_ocr(self):
        with patch("app.main.extract_receipt") as ocr:
            response = self.upload(b"x" * (limits.MAX_UPLOAD_BYTES + 1))
        self.assertEqual(response.status_code, 413)
        ocr.assert_not_called()
        self.assertEqual(self.store.receipts(), [])

    def test_per_minute_cap_returns_429_without_calling_ocr(self):
        with patch.object(limits, "CALLS_PER_MINUTE", 2), patch("app.main.extract_receipt", side_effect=RuntimeError("no OCR")) as ocr:
            self.assertEqual([self.upload().status_code for _ in range(3)], [201, 201, 429])
        self.assertEqual(ocr.call_count, 2)
        self.assertEqual(len(self.store.receipts()), 2)

    def test_daily_cap_covers_llm_calls_too(self):
        with patch.object(limits, "COMPANY_CALLS_PER_DAY", 1), patch("app.main.extract_receipt", side_effect=RuntimeError("no OCR")):
            self.assertEqual(self.upload().status_code, 201)
            blocked = self.client.post("/ai/judge", json={"ids": []})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("한도", blocked.json()["detail"])
        self.assertEqual(blocked.headers["Retry-After"], "3600")

    def test_statement_file_count_and_parse_limits(self):
        files = [("files", (f"s{i}.csv", b"a,b", "text/csv")) for i in range(limits.MAX_FILES_PER_REQUEST + 1)]
        self.assertEqual(self.client.post("/workspace/from-statements", files=files).status_code, 413)
        bad = xlsx('<row><c r="XFD1"><v>1</v></c></row>')
        response = self.client.post("/bank-statements", files={"file": ("bomb.xlsx", bad)})
        self.assertEqual(response.status_code, 422)
        self.assertIn("열이 너무", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
