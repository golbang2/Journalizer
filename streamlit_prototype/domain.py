"""Local single-user prototype state and journal rules; no OCR or tax service."""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import uuid
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / ".local"
ACCOUNTS = {
    "소모품비": "830", "복리후생비": "811", "회의비": "833", "접대비": "813",
    "여비교통비": "812", "도서인쇄비": "826", "지급수수료": "831",
    "부가세대급금": "135", "미지급금": "253", "보통예금": "103", "현금": "101",
}
PURPOSES = {"직원 식사": "복리후생비", "내부 회의": "회의비", "거래처 미팅": "접대비"}


def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def event(record, action, detail=""):
    record["history"].append({"시각": timestamp(), "작업": action, "내용": detail})


def new_record(merchant="", total=0, category="소모품비", kind="normal"):
    record = {
        "id": uuid.uuid4().hex, "merchant": merchant, "date": "2026-10-08",
        "business_number": "123-45-67890" if merchant else "", "total": total,
        "supply": round(total / 1.1), "vat": total - round(total / 1.1),
        "payment": "신용카드", "approval": "", "category": category,
        "memo": f"{merchant} 비용" if merchant else "", "purpose": "선택 필요",
        "deductible": False, "kind": kind, "status": "검토 필요", "source": "sample",
        "file_path": "", "file_name": "", "issues": [], "reviewed": False,
        "duplicate_ok": False, "lines": [], "history": [], "version": 0,
        "items": [{"품목": "사무용품", "수량": 1, "금액": total}],
    }
    return record


def make_samples():
    specs = [
        ("오피스 문구", 55000, "소모품비", "normal", "사무용품", "A1001"),
        ("정담 식당", 66000, "복리후생비", "meal", "점심 식사 6인", "A1002"),
        ("한빛 서점", 33000, "도서인쇄비", "unclear", "업무 참고 도서", "A1003"),
        ("오피스 문구", 55000, "소모품비", "duplicate", "사무용품", "A1001"),
        ("오피스 문구", 55000, "소모품비", "cancel", "사무용품 취소", "C1001"),
        ("오피스 종합몰", 77000, "소모품비", "split", "문구 및 업무 도서", "A1004"),
    ]
    records = []
    for merchant, total, category, kind, item, approval in specs:
        r = new_record(merchant, total, category, kind)
        r["approval"] = approval
        r["items"] = [{"품목": item, "수량": 1, "금액": total}]
        r["original"] = copy.deepcopy({k: r[k] for k in (
            "merchant", "date", "business_number", "total", "supply", "vat", "payment", "approval", "items")})
        if kind == "unclear":
            r["total"] = 38000
            r["issues"] = ["결제금액이 흐릿합니다. 원본의 33,000원과 추출값을 비교해 수정하세요."]
        if kind == "meal":
            r["issues"] = ["식사 사용 목적을 선택하세요."]
        if kind == "split":
            r["items"] = [{"품목": "문구", "수량": 1, "금액": 44000}, {"품목": "업무 도서", "수량": 1, "금액": 33000}]
            r["original"]["items"] = copy.deepcopy(r["items"])
        if kind == "cancel":
            r["issues"] = ["취소 거래입니다. 차변·대변을 반대로 생성합니다."]
        r["lines"] = recommend(r)
        event(r, "샘플 등록", "가상 영수증 · 외부 OCR 미사용")
        records.append(r)
    return records


def line(side, account, amount, memo):
    return {"차/대": side, "계정과목": account, "금액": amount, "적요": memo}


def recommend(r):
    expense = PURPOSES.get(r["purpose"], r["category"]) if r["kind"] == "meal" else r["category"]
    vat = r["vat"] if r["deductible"] and expense != "접대비" else 0
    cost = max(0, r["total"] - vat)
    if r["kind"] == "split":
        first = round(cost * 4 / 7)
        lines = [line("차변", expense, first, r["memo"]), line("차변", "도서인쇄비", cost - first, r["memo"])]
    else:
        lines = [line("차변", expense, cost, r["memo"])]
    if vat:
        lines.append(line("차변", "부가세대급금", vat, "사용자가 선택한 세액 분리"))
    credit = {"신용카드": "미지급금", "체크카드": "보통예금", "현금": "현금"}[r["payment"]]
    lines.append(line("대변", credit, r["total"], r["memo"]))
    if r["kind"] == "cancel":
        for row in lines:
            row["차/대"] = "대변" if row["차/대"] == "차변" else "차변"
    return lines


def duplicates(r, records):
    return [other for other in records if other["id"] != r["id"] and r["approval"].strip()
            and other["approval"] == r["approval"] and other["date"] == r["date"]
            and other["total"] == r["total"] and other["kind"] != "cancel" and r["kind"] != "cancel"]


def validate(r, records):
    errors = []
    if not r["merchant"].strip():
        errors.append("거래처를 입력하세요.")
    try:
        datetime.strptime(r["date"], "%Y-%m-%d")
    except (ValueError, TypeError):
        errors.append("거래일을 확인하세요.")
    if r["total"] <= 0:
        errors.append("결제금액은 0원보다 커야 합니다.")
    if r["supply"] < 0 or r["vat"] < 0 or r["supply"] + r["vat"] != r["total"]:
        errors.append("공급가액 + 부가세가 결제금액과 일치해야 합니다.")
    if r["kind"] == "meal" and r["purpose"] not in PURPOSES:
        errors.append("식사 사용 목적을 선택하세요.")
    if not r["reviewed"]:
        errors.append("원본과 입력값을 확인한 뒤 검토 완료에 체크하세요.")
    if duplicates(r, records) and not r["duplicate_ok"]:
        errors.append("중복 의심 건을 확인하고 별도 거래 여부를 체크하세요.")
    totals = {"차변": 0, "대변": 0}
    if not r["lines"]:
        errors.append("분개 행을 추가하세요.")
    for row in r["lines"]:
        amount = row.get("금액")
        if row.get("차/대") not in totals or row.get("계정과목") not in ACCOUNTS:
            errors.append("분개 행의 차/대와 계정과목을 선택하세요.")
            continue
        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0 or amount != int(amount):
            errors.append("분개 금액은 양의 정수로 입력하세요.")
            continue
        totals[row["차/대"]] += amount
    if totals["차변"] != totals["대변"]:
        errors.append("차변과 대변 합계가 다릅니다.")
    if totals["차변"] != r["total"] or totals["대변"] != r["total"]:
        errors.append("차변·대변 합계는 각각 영수증 결제금액과 일치해야 합니다.")
    return list(dict.fromkeys(errors))


def save(records, path=None):
    path = path or DATA_DIR / "records.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def load(path=None):
    path = path or DATA_DIR / "records.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    records = make_samples()
    save(records, path)
    return records


def export_csv(records):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["전표ID", "거래일", "거래처", "승인번호", "차/대", "계정코드", "계정과목", "금액", "적요"])
    def safe(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
    for r in records:
        if r["status"] != "확정 완료":
            continue
        for row in r["lines"]:
            writer.writerow([safe(v) for v in [r["id"][:8], r["date"], r["merchant"], r["approval"],
                row["차/대"], ACCOUNTS[row["계정과목"]], row["계정과목"], row["금액"], row["적요"]]])
    return output.getvalue().encode("utf-8-sig")
