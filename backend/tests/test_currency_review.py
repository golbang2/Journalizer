import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ReceiptExtraction
from app.services.journalizer import build_journal_entry
from app.services.receipts import extract_receipt


def receipt_data(currency="KRW"):
    return {
        "merchant": "PADDLE.NET*CRAFTPIX",
        "purchased_at": "2023-06-24",
        "currency": currency,
        "subtotal": 19896,
        "tax": 0,
        "total": 19896,
        "payment_method": "체크카드",
        "category_hint": "office",
        "confidence": 0.85,
        "line_items": [],
    }


class CurrencyReviewTests(unittest.TestCase):
    def test_foreign_detection_survives_extract_and_draft_as_warning(self):
        response = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(receipt_data("USD")))])
        with patch.dict("os.environ", {"RECEIPT_PDF_ENGINE": "claude"}), patch("app.services.receipts.anthropic.Anthropic") as factory:
            factory.return_value.messages.create.return_value = response
            receipt = extract_receipt("41.pdf", b"%PDF-1.7")
        self.assertEqual(receipt.currency, "KRW")
        self.assertEqual(receipt.total, Decimal("19896"))
        self.assertEqual(len(receipt.warnings), 1)
        self.assertIn("USD", receipt.warnings[0])
        self.assertIn("실제 원화 결제금액", receipt.warnings[0])
        # Simulate the frontend forwarding the extraction JSON to /draft.
        submitted = ReceiptExtraction.model_validate_json(receipt.model_dump_json())
        draft = build_journal_entry(submitted)
        self.assertEqual(draft.warnings, receipt.warnings)
        self.assertEqual(draft.receipt.currency, "KRW")
        self.assertEqual(draft.lines[-1].amount, Decimal("19896"))

    def test_default_and_won_inputs_do_not_warn(self):
        data = receipt_data()
        del data["currency"]
        for candidate in [data, receipt_data("KRW"), receipt_data(" krw "), receipt_data("")]:
            with self.subTest(candidate=candidate):
                receipt = ReceiptExtraction.model_validate(candidate)
                self.assertEqual(receipt.currency, "KRW")
                self.assertEqual(build_journal_entry(receipt).warnings, [])

    def test_direct_draft_foreign_input_is_normalized_and_preserves_other_warnings(self):
        data = receipt_data("EUR")
        data.update(warnings=["원본을 확인해 주세요."], confidence=0.5, subtotal=19000)
        draft = build_journal_entry(ReceiptExtraction.model_validate(data))
        self.assertEqual(draft.receipt.currency, "KRW")
        self.assertEqual(draft.warnings[0], "원본을 확인해 주세요.")
        self.assertIn("EUR", draft.warnings[1])
        self.assertIn("Debit and credit totals do not match.", draft.warnings)
        self.assertIn("Extraction confidence is low. Please review before posting.", draft.warnings)
        # Revalidating already normalized output must not duplicate warnings.
        again = build_journal_entry(ReceiptExtraction.model_validate_json(draft.receipt.model_dump_json()))
        self.assertEqual(again.warnings, draft.warnings)

if __name__ == "__main__":
    unittest.main()
