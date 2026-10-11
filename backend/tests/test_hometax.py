"""Synthetic exports only: no customer data, OCR, or external API calls."""
import csv
import io
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.auth import create_user, get_db, get_store
from app.main import app
from app.models import ReceiptExtraction, ReceiptRecord, VatReturn
from app.services import hometax
from app.services.store import Store, company_for_user
from db_helpers import fresh_engine

CARD_HEADER = ["거래일자", "가맹점사업자등록번호", "가맹점명", "공급가액", "부가세", "합계금액", "승인번호", "공제여부", "거래구분"]
CARD_ROW = ["2026-07-01", "123-45-67890", "테스트 상점", "100000", "10000", "110000", "00123456", "공제", "승인"]
INVOICE_HEADER = ["작성일자", "승인번호", "공급자등록번호", "상호", "공급받는자등록번호", "상호", "공급가액", "세액", "합계금액", "수정사유"]
INVOICE_ROW = ["2026-07-01", "202607010000000000000001", "123-45-67890", "테스트 상점", "987-65-43210", "우리 회사", "100000", "10000", "110000", ""]


def csv_file(rows, header=CARD_HEADER, encoding="utf-8-sig"):
    stream = io.StringIO()
    csv.writer(stream).writerows([header, *rows])
    return stream.getvalue().encode(encoding)


def batch(rows=None):
    return hometax.parse("card.csv", csv_file(rows or [CARD_ROW]), "card")


def receipt(rid="R-001", **changes):
    ext = ReceiptExtraction(merchant="테스트 상점", purchased_at="2026-07-01", subtotal=100000, tax=10000, total=110000,
        payment_method="카드", category_hint="other", confidence=1, line_items=[], approval_number="00123456", business_number="123-45-67890")
    return ReceiptRecord(**{**dict(id=rid, file_name="test.pdf", uploaded_at="2026-07-02", merchant="테스트 상점", date="2026-07-01",
        total=110000, original_tax=10000, extraction=ext, vat="deductible", tax=10000, confirmed=True), **changes})


def test_csv_cp949_and_xlsx_multisheet_preserve_approval_and_explicit_tax():
    parsed = hometax.parse("card.csv", csv_file([CARD_ROW], encoding="cp949"), "card")
    assert parsed.rows[0].approval_number == "00123456"
    wb = Workbook()
    for i, ws in enumerate([wb.active, wb.create_sheet("추가")]):
        ws.append(["사업용 신용카드 상세 내역"])
        ws.append(CARD_HEADER)
        ws.append(CARD_ROW)
    stream = io.BytesIO()
    wb.save(stream)
    parsed = hometax.parse("card.xlsx", stream.getvalue(), "card")
    assert len(parsed.rows) == 2
    assert parsed.rows[0].row_number == 3
    assert parsed.rows[0].tax == 10000


def test_supplier_and_recipient_columns_are_distinguished():
    content = csv_file([INVOICE_ROW], INVOICE_HEADER)
    row = hometax.parse("invoice.csv", content, "invoice", "9876543210").rows[0]
    assert row.merchant == "테스트 상점"
    assert row.business_number == "1234567890"
    with pytest.raises(hometax.HometaxError, match="공급받는자"):
        hometax.parse("invoice.csv", content, "invoice", "1111111111")


@pytest.mark.parametrize("column,value", [(0, "2026-02-30"), (1, "123"), (3, "100000.1"), (4, ""), (5, "120000"), (6, "1.234E+20")])
def test_bad_detail_row_rejects_entire_file_with_location(column, value):
    bad = CARD_ROW.copy()
    bad[column] = value
    with pytest.raises(hometax.HometaxError, match="3행"):
        batch([CARD_ROW, bad])


def test_summary_is_skipped_but_unknown_format_or_wrong_kind_rejected():
    assert len(batch([CARD_ROW, ["합계", "", "", "100000", "10000", "110000"]]).rows) == 1
    with pytest.raises(hometax.HometaxError, match="머리글"):
        hometax.parse("card.csv", csv_file([CARD_ROW]), "invoice")


def test_exact_match_does_not_mutate_receipt_or_tax():
    r = receipt()
    before = r.model_dump()
    result = hometax.reconcile([batch()], [r], {})[0]
    assert result["receipt_id"] == "R-001"
    assert result["issues"] == []
    assert r.model_dump() == before


def test_tax_and_deduction_difference_and_draft_are_visible():
    r = receipt(tax=9000, confirmed=False)
    r.extraction.tax = Decimal(0)
    values = CARD_ROW.copy()
    values[7] = "불공제"
    issues = hometax.reconcile([batch([values])], [r], {})[0]["issues"]
    assert "증빙 세액 차이" in issues
    assert "장부 공제세액 차이" in issues
    assert "공제 판단 차이 · 홈택스 불공제" in issues
    assert "연결 영수증 미확정" in issues


def test_overlapping_exports_deduplicate_but_conflicts_prevent_auto_match():
    a, b = batch(), batch()
    b.id = "other-file"
    result = hometax.reconcile([a, b], [receipt()], {})
    assert len(result) == 1
    assert len(result[0]["sources"]) == 2
    b.rows[0].tax = 9000
    b.rows[0].supply = 101000
    result = hometax.reconcile([a, b], [receipt()], {})[0]
    assert result["receipt_id"] == ""
    assert "같은 승인번호의 원본 자료가 서로 다릅니다" in result["issues"]


def test_missing_approval_does_not_merge_identical_purchases_or_auto_link():
    values = CARD_ROW.copy()
    values[6] = ""
    result = hometax.reconcile([batch([values, values])], [receipt()], {})
    assert len(result) == 2
    assert all(not r["receipt_id"] for r in result)
    assert all(r["candidates"] for r in result)


def test_multiple_receipts_are_not_auto_linked_and_cross_evidence_warns():
    result = hometax.reconcile([batch()], [receipt(), receipt("R-002")], {})[0]
    assert not result["receipt_id"]
    assert len(result["candidates"]) == 2
    invoice = hometax.parse("i.csv", csv_file([INVOICE_ROW], INVOICE_HEADER), "invoice")
    result = hometax.reconcile([batch(), invoice], [receipt()], {})
    assert all(any("이중 계상" in issue for issue in row["issues"]) for row in result)
    assert next(r for r in result if r["kind"] == "invoice")["receipt_id"] == ""


def test_cancellations_and_modified_invoices_require_review():
    negative = CARD_ROW.copy()
    negative[3:6] = ["-100000", "-10000", "-110000"]
    negative[8] = "취소"
    result = hometax.reconcile([batch([CARD_ROW, negative])], [receipt()], {})
    cancel = next(r for r in result if r["total"] < 0)
    assert not cancel["receipt_id"]
    assert cancel["adjustment"]
    modified = INVOICE_ROW.copy()
    modified[-1] = "기재사항 착오정정"
    assert hometax.parse("i.csv", csv_file([modified], INVOICE_HEADER), "invoice").rows[0].adjustment


def test_stale_manual_link_is_visible():
    imported = batch()
    row = hometax.reconcile([imported], [], {imported.rows[0].id: "R-deleted"})[0]
    assert any("삭제되거나" in issue for issue in row["issues"])
    assert not row["receipt_id"]


def test_approval_match_with_different_amount_is_only_a_candidate():
    row = hometax.reconcile([batch()], [receipt(total=120000)], {})[0]
    assert not row["receipt_id"]
    assert "승인번호 대응 영수증의 날짜·합계금액 차이" in row["issues"]


def test_positive_cancellation_flag_is_not_automatically_linked():
    values = CARD_ROW.copy()
    values[-1] = "Y"
    row = hometax.reconcile([batch([values])], [receipt()], {})[0]
    assert row["adjustment"] and not row["receipt_id"]


def test_card_number_prevents_merging_different_cards_with_same_approval():
    content = csv_file([CARD_ROW + ["1234"], CARD_ROW + ["5678"]], CARD_HEADER + ["카드번호"])
    imported = hometax.parse("cards.csv", content, "card")
    assert len({r.id for r in imported.rows}) == 2
    rows = hometax.reconcile([imported], [receipt()], {})
    assert all(any("여러 홈택스 거래" in issue for issue in r["issues"]) for r in rows)


def test_service_amount_is_preserved_and_validated():
    values = CARD_ROW.copy()
    values[5] = "115000"
    content = csv_file([values + ["5000"]], CARD_HEADER + ["봉사료"])
    row = hometax.parse("card.csv", content, "card").rows[0]
    assert row.service == 5000
    assert row.tax == 10000


@pytest.fixture
def api_client(tmp_path):
    engine = fresh_engine(str(tmp_path))
    user = create_user(engine, "hometax@example.com", "owner", password="password123")
    other = create_user(engine, "other@example.com", "other", password="password123")
    store = Store(engine, company_for_user(engine, user.id))
    other_store = Store(engine, company_for_user(engine, other.id))
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_db] = lambda: engine
    try:
        with TestClient(app) as client:
            yield client, store, other_store
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_api_upload_download_reimport_link_delete_and_tenant_isolation(api_client):
    client, store, other = api_client
    content = csv_file([CARD_ROW])
    response = client.post("/hometax/imports", data={"kind": "card"}, files={"file": ("카드.csv", content)})
    assert response.status_code == 200, response.text
    imported = response.json()
    assert imported["added"]
    response = client.post("/hometax/imports", data={"kind": "card"}, files={"file": ("다른이름.csv", content)})
    assert not response.json()["added"]
    url = f"/hometax/imports/{imported['id']}"
    assert client.get(url + "/file").content == content
    r = receipt()
    store.save_receipt(r)
    report_url = "/hometax/reconcile?start=2026-01-01&end=2026-12-31"
    row = client.get(report_url).json()["rows"][0]
    link_url = f"/hometax/rows/{row['id']}/link"
    assert client.put(link_url, json={"receipt_id": r.id}).status_code == 204
    assert client.get(report_url).json()["rows"][0]["manual"]
    assert store.receipt(r.id) == r
    assert client.put(link_url, json={"receipt_id": "R-other"}).status_code == 422
    assert client.get("/hometax/reconcile?start=2026-08-01&end=2026-12-31").json()["rows"] == []
    assert client.get("/hometax/reconcile?start=2026-12-31&end=2026-01-01").status_code == 422
    app.dependency_overrides[get_store] = lambda: other
    assert client.get(report_url).json()["rows"] == []
    assert client.get(url + "/file").status_code == 404
    assert client.delete(url).status_code == 404
    assert client.put(link_url, json={"receipt_id": r.id}).status_code == 404
    app.dependency_overrides[get_store] = lambda: store
    assert client.delete(url).status_code == 204
    assert not store.hometax_links()
    assert store.receipt(r.id) == r
    del app.dependency_overrides[get_store]
    assert client.get(report_url).status_code == 401


def test_api_failed_upload_is_atomic_and_reports_unmatched_receipts(api_client):
    client, store, _ = api_client
    bad = CARD_ROW.copy()
    bad[4] = "wrong"
    response = client.post("/hometax/imports", data={"kind": "card"}, files={"file": ("bad.csv", csv_file([CARD_ROW, bad]))})
    assert response.status_code == 422
    assert not store.hometax_imports()
    imported = batch()
    store.add_hometax_import(imported, csv_file([CARD_ROW]))
    r = receipt(date="2026-08-01")
    r.extraction.approval_number = "99999999"
    store.save_receipt(r)
    report = client.get("/hometax/reconcile?start=2026-01-01&end=2026-12-31").json()
    assert report["unmatched_receipts"][0]["id"] == r.id


def test_aggregate_schedule_upload_never_rewrites_confirmed_receipt(api_client):
    client, store, _ = api_client
    r = receipt(tax=0, original_tax=0, vat="included")
    r.extraction.tax = Decimal(0)
    store.save_receipt(r)
    store.save_vat_return(VatReturn(id="V-1", period_start="2026-01-01", period_end="2026-12-31", filed="2027-01-25"))
    with patch("app.services.ai_judge.read_vat_schedule", return_value=[{"business_number": "1234567890", "tax": 10000, "count": 1}]):
        response = client.post("/vat-returns/V-1/schedule", files={"file": ("schedule.pdf", b"%PDF")})
    assert response.status_code == 200, response.text
    assert response.json()["fixed"] == []
    assert store.receipt(r.id) == r
