import unittest

from app.models import Vendor
from app.services.autofill import fill_vendor_business
from app.services.ledger import default_workspace


class VendorBusinessTests(unittest.TestCase):
    def setUp(self):
        self.workspace = default_workspace()
        self.vendor = Vendor(code="A0002", name="특허법인아이엠", alias="아이엠특허")
        self.workspace.vendors = [self.vendor]

    def test_exact_alias_and_number_format_are_normalized(self):
        self.assertTrue(fill_vendor_business(self.workspace, self.vendor, "아이엠 특허", "4098532953"))
        self.assertEqual(self.vendor.business, "409-85-32953")
        self.assertFalse(fill_vendor_business(self.workspace, self.vendor, "아이엠 특허", "4098532953"))

    def test_invalid_number_or_fuzzy_name_cannot_fill(self):
        for name, number in [("특허법인아이엠", None), ("특허법인아이엠", "409-85-3295"),
                             ("특허법인아이엠", "409-85-32953 OCR"),
                             ("특허법인아이엠서울지점", "409-85-32953"),
                             ("다른 거래처", "409-85-32953")]:
            with self.subTest(name=name, number=number):
                self.assertFalse(fill_vendor_business(self.workspace, self.vendor, name, number))
                self.assertEqual(self.vendor.business, "")

    def test_duplicate_name_or_number_requires_review(self):
        for other in [Vendor(code="B", name="특허법인아이엠"),
                      Vendor(code="B", name="다른 거래처", business="4098532953")]:
            with self.subTest(other=other):
                self.workspace.vendors = [self.vendor, other]
                self.assertFalse(fill_vendor_business(self.workspace, self.vendor, "특허법인아이엠", "409-85-32953"))
                self.assertEqual(self.vendor.business, "")
