from copy import deepcopy
import unittest

from app.services.bank_pdf_parser import ReceiptParseError
from app.services.vision_receipts import _parse_annotation


def annotation():
    cells = [
        ("승인번호", .48, .212), ("20231115-10231121-58129813", .55, .212),
        ("123-45-67890", .24, .230), ("987-65-43210", .55, .230),
        ("상호", .19, .244), ("테스트자문", .24, .247), ("성명", .355, .247), ("김테스트", .393, .247),
        ("상호", .50, .244), ("구매회사", .55, .247), ("성명", .662, .247), ("다른대표", .70, .247),
        ("작성일자", .175, .317), ("공급가액", .272, .317), ("세액", .397, .317), ("수정사유", .484, .317),
        ("2028-11-15", .166, .332), ("10,000", .311, .332), ("1,000", .432, .332),
        ("합계금액", .19, .429), ("현금", .297, .429), ("11.000", .225, .442),
    ]
    words = [{"symbols": [{"text": c} for c in text], "confidence": .99,
              "boundingBox": {"normalizedVertices": [{"x": x, "y": y-.003}, {"x": x+.03, "y": y-.003},
                                                        {"x": x+.03, "y": y+.003}, {"x": x, "y": y+.003}]}}
             for text, x, y in cells]
    return {"text": "전자세금계산서 작성일자 청구", "pages": [{"width": 600, "height": 850,
            "blocks": [{"paragraphs": [{"words": words}]}]}]}


def replace_word(data, old, new):
    for word in data["pages"][0]["blocks"][0]["paragraphs"][0]["words"]:
        if "".join(s["text"] for s in word["symbols"]) == old:
            word["symbols"] = [{"text": c} for c in new]


class TaxInvoiceParserTests(unittest.TestCase):
    def test_supplier_not_buyer_and_original_year_preserved(self):
        result = _parse_annotation(annotation())
        self.assertEqual((result.document_type, result.merchant, result.representative),
                         ("tax_invoice", "테스트자문", "김테스트"))
        self.assertEqual(result.business_number, "123-45-67890")
        self.assertEqual(str(result.purchased_at), "2028-11-15")
        self.assertEqual((result.subtotal, result.tax, result.total), (10000, 1000, 11000))
        self.assertEqual(result.field_evidence["purchased_at"]["text"], "2028-11-15")

    def test_month_and_day_are_not_compared_with_approval_number(self):
        data = annotation()
        replace_word(data, "2028-11-15", "2023-02-03")
        result = _parse_annotation(data)
        self.assertEqual(str(result.purchased_at), "2023-02-03")
        self.assertEqual(result.warnings, [])

    def test_unreconciled_amounts_and_missing_supplier_fail(self):
        for old, new in [("11.000", "12,000"), ("123-45-67890", "unknown")]:
            data = deepcopy(annotation())
            replace_word(data, old, new)
            with self.subTest(old=old), self.assertRaises(ReceiptParseError):
                _parse_annotation(data)
