"""Import Hometax purchase exports as evidence, never as additional journal entries.

Unknown layouts and malformed detail rows fail the entire import. Amounts are read
from explicit columns, not inferred by an LLM or by dividing a gross amount by 11.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from app.models import HometaxImport, HometaxRow, ReceiptRecord
from app.services import spreadsheets


class HometaxError(ValueError):
    pass


def compact(value: str) -> str:
    return re.sub(r"[\s\-]", "", value or "")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


COMMON = {
    "supply": {"공급가액", "공급가액(원)"},
    "tax": {"세액", "부가세", "부가세액", "부가가치세", "세액(원)", "부가세(원)"},
    "total": {"합계금액", "합계", "거래금액", "승인금액", "이용금액", "합계금액(원)", "공급대가"},
    "approval_number": {"승인번호", "거래승인번호", "전자세금계산서승인번호"},
    "deduction_text": {"공제여부", "공제구분", "공제/불공제", "공제불공제", "매입세액공제여부"},
    "card_number": {"카드번호", "신용카드번호"},
    "status": {"거래구분", "승인구분", "취소여부", "수정사유", "수정세금계산서발급사유"},
    "recipient": {"공급받는자등록번호", "공급받는자사업자등록번호"},
    "service": {"봉사료", "봉사료(원)"},
}
BY_KIND = {
    "card": {
        "date": {"거래일자", "거래일시", "승인일자", "승인일시", "사용일자", "이용일자", "거래일"},
        "business_number": {"가맹점사업자번호", "가맹점사업자등록번호", "사업자등록번호", "공급자사업자등록번호"},
        "merchant": {"가맹점명", "가맹점상호", "상호", "상호명", "공급자상호"},
    },
    "invoice": {
        "date": {"작성일자", "작성일"},
        "business_number": {"공급자등록번호", "공급자사업자등록번호"},
        "merchant": {"공급자상호", "공급자상호명", "상호", "상호명"},
    },
}
REQUIRED = {"date", "business_number", "merchant", "supply", "tax", "total"}


def _header(row, kind):
    aliases = {**COMMON, **BY_KIND[kind]}
    result = {}
    party = ""
    for index, value in enumerate(row):
        label = compact(value)
        if kind == "invoice":
            if label in COMMON["recipient"]:
                party = "recipient"
            elif label in BY_KIND[kind]["business_number"]:
                party = "supplier"
            if label in {"상호", "상호명"} and party == "recipient":
                continue
        for field, names in aliases.items():
            if label in names:
                if field in result:
                    raise HometaxError(f"중복된 열({value})이 있습니다. 공급자·공급받는자 열을 구분해 주세요.")
                result[field] = index
    return result


def _amount(value):
    text = re.sub(r"[,\s원₩]", "", value).replace("−", "-")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        number = Decimal(text)
        if not number.is_finite() or number != number.to_integral_value() or abs(number) > 10**15:
            raise ValueError()
        return int(number)
    except (InvalidOperation, ValueError):
        raise HometaxError(f"금액을 확인해 주세요: {value or '(빈칸)'}") from None


def _date(value):
    text = value.strip()
    match = re.fullmatch(r"(\d{4})[-./년\s]+(\d{1,2})[-./월\s]+(\d{1,2})(?:일|[ T].*)?", text)
    if not match:
        match = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    raise HometaxError(f"날짜를 확인해 주세요: {value}. YYYY-MM-DD 형식으로 저장해 주세요.")


def parse(filename: str, content: bytes, kind: str, company_number: str = "") -> HometaxImport:
    if kind not in BY_KIND:
        raise HometaxError("카드 매입 또는 매입 세금계산서를 선택해 주세요.")
    file_id = hashlib.sha256(kind.encode() + content).hexdigest()
    try:
        if filename.lower().endswith(".xlsx"):
            sheets = spreadsheets.xlsx_sheets(content)
        elif filename.lower().endswith(".xls"):
            sheets = spreadsheets.xls_sheets(content, lambda v: str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
        elif filename.lower().endswith(".csv"):
            sheets = [("CSV", spreadsheets.csv_rows(content))]
        else:
            raise HometaxError("홈택스 자료는 .xlsx, .xls, .csv 파일만 지원합니다.")
    except spreadsheets.SpreadsheetError as exc:
        raise HometaxError(str(exc)) from exc
    result = []
    for sheet, rows in sheets:
        columns = None
        for index, values in enumerate(rows):
            if not any(v.strip() for v in values):
                continue
            # Header rows are recognized only when they contain a date label.
            if any(compact(v) in BY_KIND[kind]["date"] for v in values):
                columns = _header(values, kind)
                if not REQUIRED <= columns.keys():
                    raise HometaxError(f"{sheet} {index + 1}행: 날짜·공급자 사업자번호·상호·공급가액·세액·합계금액 열이 필요합니다.")
                continue
            if columns is None:
                continue  # export title and metadata above the header

            def cell(field):
                pos = columns.get(field)
                return values[pos].strip() if pos is not None and pos < len(values) else ""

            if any(compact(v) in {"합계", "총합계", "총계", "소계"} for v in values) and not cell("approval_number") and not re.fullmatch(r"\d{10}", compact(cell("business_number"))):
                continue
            try:
                bn = compact(cell("business_number"))
                if not re.fullmatch(r"\d{10}", bn):
                    raise HometaxError("공급자 사업자등록번호는 10자리여야 합니다.")
                recipient = compact(cell("recipient"))
                if recipient and company_number and recipient != compact(company_number):
                    raise HometaxError("공급받는자 사업자번호가 회사 기준과 다릅니다. 매입 자료인지 확인해 주세요.")
                if not cell("merchant"):
                    raise HometaxError("상호가 비어 있습니다.")
                supply, tax, total = (_amount(cell(f)) for f in ("supply", "tax", "total"))
                service = _amount(cell("service")) if cell("service") else 0
                if supply + tax + service != total:
                    raise HometaxError("공급가액 + 세액 + 봉사료가 합계금액과 다릅니다.")
                if (total >= 0 and min(supply, tax, service) < 0) or (total < 0 and max(supply, tax, service) > 0):
                    raise HometaxError("공급가액·세액의 부호를 확인해 주세요.")
                day, approval = _date(cell("date")), compact(cell("approval_number"))
                if approval and not re.fullmatch(r"[0-9A-Za-z]+", approval):
                    raise HometaxError("승인번호를 확인해 주세요. 지수 표기나 마스킹된 번호는 지원하지 않습니다.")
                status = cell("status")
                cancelled = total < 0 or "취소" in status or compact(status).upper() in {"Y", "YES", "TRUE"}
                # An identifier without an approval number must not collapse identical purchases.
                identity = [kind, bn, approval, day if kind == "card" else "", compact(cell("card_number")) if kind == "card" else "", "취소" if cancelled else "승인"]
                if not approval:
                    identity += [file_id, sheet, index + 1]
                deduction_text = cell("deduction_text")
                deduction = {"공제": "deductible", "공제대상": "deductible", "불공제": "nondeductible", "불공제대상": "nondeductible"}.get(compact(deduction_text), "unknown")
                adjustment = cancelled or (kind == "invoice" and status not in ("", "일반", "정상", "0", "해당없음"))
                result.append(HometaxRow(id=digest(identity), kind=kind, date=day, business_number=bn,
                    merchant=cell("merchant"), approval_number=approval, card_number=cell("card_number"),
                    supply=supply, tax=tax, total=total, service=service, deduction=deduction, deduction_text=deduction_text,
                    status=status, adjustment=adjustment, sheet=sheet, row_number=index + 1))
            except HometaxError as exc:
                raise HometaxError(f"{sheet} {index + 1}행: {exc}") from exc
        if columns is None and any(any(v.strip() for v in row) for row in rows):
            raise HometaxError(f"{sheet}: 선택한 자료 종류의 거래별 머리글을 찾지 못했습니다. 합계표가 아닌 상세 내역을 올려 주세요.")
    if not result:
        raise HometaxError("가져올 거래가 없습니다.")
    return HometaxImport(id=file_id, kind=kind, file_name=filename, uploaded_at=datetime.now(timezone.utc).isoformat(), rows=result)


def reconcile(imports: list[HometaxImport], receipts: list[ReceiptRecord], links: dict[str, str]) -> list[dict]:
    grouped = defaultdict(list)
    for batch in imports:
        for row in batch.rows:
            grouped[row.id].append((row, batch))
    by_business = defaultdict(list)
    for receipt in receipts:
        if receipt.extraction:
            by_business[compact(receipt.extraction.business_number)].append(receipt)
    evidence = defaultdict(list)
    for row_id, group in grouped.items():
        row = group[0][0]
        evidence[(row.business_number, row.date, abs(row.total))].append(row_id)
    output = []
    for row_id, group in grouped.items():
        row = group[0][0]
        variants = {digest(r.model_dump(exclude={"sheet", "row_number"})) for r, _ in group}
        issues = []
        if len(variants) > 1:
            issues.append("같은 승인번호의 원본 자료가 서로 다릅니다")
        if not row.approval_number:
            issues.append("승인번호 없음 · 수동 확인 필요")
        if row.adjustment:
            issues.append("취소·수정 거래 · 원거래와 귀속기간 확인")
        candidates, exact = [], []
        for r in by_business[row.business_number]:
            ext = r.extraction
            same_kind = (ext.document_type == "tax_invoice") == (row.kind == "invoice")
            same_approval = bool(row.approval_number) and compact(ext.approval_number) == row.approval_number and same_kind
            same_amount_date = r.date == row.date and r.total == abs(row.total)
            if not same_approval and not same_amount_date:
                continue
            candidates.append({"id": r.id, "merchant": r.merchant, "date": r.date, "total": r.total,
                "evidence_tax": int(ext.tax), "book_tax": r.tax if r.vat == "deductible" else 0,
                "vat": r.vat, "confirmed": r.confirmed, "same_kind": same_kind})
            if same_approval and same_amount_date and not row.adjustment:
                exact.append(r.id)
            if same_approval and not same_amount_date:
                issues.append("승인번호 대응 영수증의 날짜·합계금액 차이")
        chosen = links.get(row_id, "")
        manual = bool(chosen)
        if chosen and chosen not in {c["id"] for c in candidates}:
            issues.append("수동 연결한 영수증이 삭제되거나 거래 정보가 변경되었습니다")
            chosen = ""
        if not manual and len(exact) == 1 and len(variants) == 1:
            chosen = exact[0]
        if len(candidates) > 1:
            issues.append("영수증 중복 또는 복수 후보 확인")
        related = [i for i in evidence[(row.business_number, row.date, abs(row.total))] if i != row_id]
        if related:
            issues.append("같은 날짜·거래처·금액의 다른 증빙 있음 · 이중 계상 확인")
        if chosen:
            match = next(c for c in candidates if c["id"] == chosen)
            if match["date"] != row.date or match["total"] != row.total:
                issues.append("날짜·합계금액 차이")
            if match["evidence_tax"] != row.tax:
                issues.append("증빙 세액 차이")
            if match["vat"] == "pending":
                issues.append("공제 판단 미확정")
            elif row.deduction == "nondeductible" and match["vat"] == "deductible":
                issues.append("공제 판단 차이 · 홈택스 불공제")
            elif row.deduction == "deductible" and match["vat"] != "deductible":
                issues.append("공제 판단 차이 · 사업 관련성 확인")
            if match["vat"] == "deductible" and match["book_tax"] != row.tax:
                issues.append("장부 공제세액 차이")
            if not match["confirmed"]:
                issues.append("연결 영수증 미확정")
        elif not candidates:
            issues.append("대응 영수증 없음 · 증빙 누락 확인")
        else:
            issues.append("연결 후보 확인 필요")
        if row.deduction == "unknown":
            issues.append("홈택스 공제 여부 미제공·미확인")
        output.append({**row.model_dump(), "sources": [{"import_id": b.id, "file_name": b.file_name,
            "sheet": r.sheet, "row_number": r.row_number, "tax": r.tax, "total": r.total,
            "deduction_text": r.deduction_text} for r, b in group],
            "issues": list(dict.fromkeys(issues)), "candidates": candidates, "receipt_id": chosen, "manual": manual,
            "related_ids": related, "status_label": "검토 필요" if issues else "일치"})
    # One receipt may appear in several periods/exports or evidence types; never hide that overlap.
    chosen_rows = defaultdict(list)
    for item in output:
        if item["receipt_id"]:
            chosen_rows[item["receipt_id"]].append(item)
    for items in chosen_rows.values():
        if len(items) > 1:
            for item in items:
                item["issues"].append("한 영수증에 여러 홈택스 거래 연결 · 중복 확인")
                item["status_label"] = "검토 필요"
    return sorted(output, key=lambda x: (x["date"], x["id"]), reverse=True)
