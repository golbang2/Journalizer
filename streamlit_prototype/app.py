from __future__ import annotations

import base64
import copy
import html
import io
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, UnidentifiedImageError

from domain import (ACCOUNTS, DATA_DIR, PURPOSES, duplicates, event, export_csv,
                    load, make_samples, new_record, recommend, save, validate)

st.set_page_config(page_title="Journalizer · Prototype", page_icon="🧾", layout="wide")
st.markdown("""<style>
.stApp {background:#f5f7fa} [data-testid="stSidebar"] {background:#edf3f2}
h1,h2,h3 {letter-spacing:-.035em} [data-testid="stMetric"] {background:white;padding:16px;border-radius:12px}
.receipt {background:#fffdf5;border:1px solid #e1dac9;border-radius:8px;padding:28px;color:#28352f;
font-family:monospace;line-height:1.9;box-shadow:0 8px 22px #20352d0a}
.receipt h3 {text-align:center} .receipt .field {display:flex;justify-content:space-between;gap:12px;padding:4px}
.receipt .active {background:#fff0a8;border-radius:4px} .receipt hr {border:0;border-top:1px dashed #aaa}
</style>""", unsafe_allow_html=True)

if "records" not in st.session_state:
    try:
        st.session_state.records = load()
    except (OSError, ValueError) as exc:
        st.error(f"저장 데이터를 읽지 못했습니다. .local/records.json을 확인하세요: {exc}")
        st.stop()
if "next_page" in st.session_state:
    st.session_state.page = st.session_state.pop("next_page")
records = st.session_state.records


def commit(record, action, detail=""):
    candidate = copy.deepcopy(records)
    record = copy.deepcopy(record)
    old = next((r for r in candidate if r["id"] == record["id"]), None)
    if old is not None:
        changes = {k: {"이전": old.get(k), "이후": record.get(k)} for k in record
                   if k not in ("history", "version") and old.get(k) != record.get(k)}
        if changes:
            detail += "\n" + json.dumps(changes, ensure_ascii=False, indent=2)
    event(record, action, detail)
    record["version"] += 1
    if old is None:
        candidate.append(record)
    else:
        candidate[candidate.index(old)] = record
    try:
        save(candidate)
    except OSError as exc:
        st.error(f"저장하지 못했습니다: {exc}")
        st.stop()
    st.session_state.records = candidate
    st.session_state.flash = action
    st.rerun()


def open_record(record_id):
    st.session_state.selected = record_id
    st.session_state.page = "검토 작업대"


def won(amount):
    return f"{amount:,.0f}원"


def table_rows(items):
    return [{"번호": r["id"][:8], "거래일": r["date"], "거래처": r["merchant"] or "미입력",
             "결제금액": r["total"], "계정과목": r["category"], "상태": r["status"],
             "구분": "취소" if r["kind"] == "cancel" else "일반"} for r in items]


with st.sidebar:
    st.title("🧾 Journalizer")
    st.caption("영수증에서 분개까지")
    page = st.radio("메뉴", ["대시보드", "영수증함", "검토 작업대", "분개장", "설정 / 데모"], key="page")
    st.divider()
    st.info("프론트엔드 기능 시연용\n\n샘플은 가상 데이터이며 실제 OCR·외부 회계 서비스는 연결하지 않았습니다.")
    st.caption("저장 위치: streamlit_prototype/.local\n\n한 PC에서 사용하는 단일 사용자 프로토타입입니다.")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

if page == "대시보드":
    st.title("오늘의 검토 업무")
    st.write("영수증을 확인하고, 분개를 확정하세요.")
    confirmed = [r for r in records if r["status"] == "확정 완료"]
    pending = [r for r in records if r["status"] == "검토 필요"]
    cols = st.columns(4)
    for col, title, value in zip(cols, ["전체 영수증", "검토 대기", "확정 완료", "확정 순금액"],
        [len(records), len(pending), len(confirmed), won(sum(r["total"] * (-1 if r["kind"] == "cancel" else 1) for r in confirmed))]):
        col.metric(title, value)
    st.caption("현재 저장된 전체 기간 기준 · 확정 순금액은 취소 거래를 차감합니다.")
    left, right = st.columns([3, 2])
    with left:
        st.subheader("검토를 기다리고 있어요")
        for r in pending[:6]:
            with st.container(border=True):
                a, b = st.columns([4, 1])
                a.write(f"**{r['merchant'] or '미입력'}** · {won(r['total'])}")
                a.caption(r["issues"][0] if r["issues"] else "원본과 추천 분개를 확인하세요.")
                b.button("검토", key=f"dash_{r['id']}", on_click=open_record, args=(r["id"],))
        if not pending:
            st.success("모든 검토를 마쳤습니다.")
    with right:
        st.subheader("체험 순서")
        st.write("1. 영수증함에서 샘플을 선택합니다.\n2. 원본과 추출값을 비교합니다.\n3. 목적을 답하고 분개를 수정합니다.\n4. 확정 후 분개장에서 CSV를 받습니다.")
        st.info("추천 시나리오: 정담 식당 → 사용 목적 선택 → 초안 변경 → 확정")
        if st.button("영수증 등록하기", type="primary"):
            st.session_state.next_page = "영수증함"
            st.rerun()

elif page == "영수증함":
    st.title("영수증함")
    st.caption("샘플을 체험하거나 직접 업로드한 증빙을 검토하세요.")
    with st.expander("＋ 영수증 등록 / 샘플 체험", expanded=True):
        sample_tab, upload_tab = st.tabs(["샘플로 체험", "내 파일 업로드"])
        with sample_tab:
            samples = make_samples()
            sample_index = st.selectbox("시연 시나리오", range(len(samples)), format_func=lambda i:
                ["정상 · 사무용품", "추가 질문 · 식사 목적", "흐릿한 금액 · 직접 수정", "중복 의심", "취소 거래", "품목별 분할 분개"][i])
            st.caption("샘플 등록은 아래 목록에 새 영수증을 추가합니다. 동일 샘플을 다시 등록하면 중복으로 표시될 수 있습니다.")
            if st.button("샘플 분석 체험", type="primary"):
                with st.status("샘플 처리 중 (시뮬레이션)", expanded=True) as status:
                    for message in ["가상 영수증 불러오기", "저장된 추출 결과 불러오기", "분개 초안 생성"]:
                        st.write(message)
                        time.sleep(0.2)
                    status.update(label="검토 준비 완료", state="complete")
                r = samples[sample_index]
                st.session_state.selected = r["id"]
                st.session_state.next_page = "검토 작업대"
                commit(r, "샘플 분석 완료")
        with upload_tab:
            st.caption("JPG / PNG / WEBP / PDF · 파일당 10MB · 실제 OCR 대신 원본을 보며 값을 직접 입력합니다.")
            files = st.file_uploader("영수증 파일", type=["jpg", "jpeg", "png", "webp", "pdf"], accept_multiple_files=True)
            if st.button("파일 등록", disabled=not files):
                added = []
                for uploaded in files:
                    data = uploaded.getvalue()
                    ext = Path(uploaded.name).suffix.lower()
                    if not data or len(data) > 10 * 1024 * 1024:
                        st.error(f"{uploaded.name}: 빈 파일이거나 10MB를 초과합니다.")
                        continue
                    try:
                        if ext == ".pdf":
                            if not data.startswith(b"%PDF-"):
                                raise ValueError("PDF 형식이 아닙니다.")
                        else:
                            Image.open(io.BytesIO(data)).verify()
                        r = new_record()
                        r.update(source="upload", file_name=uploaded.name, date=date.today().isoformat(),
                                 issues=["실제 OCR 미연결: 원본을 보며 값을 입력하세요."], items=[])
                        target = DATA_DIR / "uploads" / f"{r['id']}{ext}"
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        r["file_path"] = str(target)
                        event(r, "파일 등록", uploaded.name)
                        added.append(r)
                    except (OSError, ValueError, UnidentifiedImageError) as exc:
                        st.error(f"{uploaded.name}: 등록 실패 — {exc}")
                if added:
                    candidate = records + added
                    save(candidate)
                    st.session_state.records = candidate
                    st.success(f"{len(added)}개 등록 완료. 아래 목록에서 선택하세요.")
    a, b, c = st.columns([3, 2, 2])
    query = a.text_input("거래처 / 파일명 검색")
    status_filter = b.selectbox("처리 상태", ["전체", "검토 필요", "확정 완료"])
    period = c.date_input("거래 기간", value=(date(2026, 1, 1), date(2026, 12, 31)))
    filtered = [r for r in st.session_state.records if
        query.lower() in (r["merchant"] + r["file_name"]).lower()
        and (status_filter == "전체" or r["status"] == status_filter)
        and (len(period) != 2 or period[0].isoformat() <= r["date"] <= period[1].isoformat())]
    if not filtered:
        st.info("표시할 영수증이 없습니다. 필터를 변경하거나 새 영수증을 등록하세요.")
    else:
        st.dataframe(pd.DataFrame(table_rows(filtered)), hide_index=True, width="stretch")
        selected = st.selectbox("검토할 영수증", [r["id"] for r in filtered], format_func=lambda value:
            next(f"{r['merchant'] or r['file_name']} · {won(r['total'])} · {r['id'][:8]}" for r in filtered if r["id"] == value))
        st.button("선택한 영수증 열기", type="primary", on_click=open_record, args=(selected,))

elif page == "검토 작업대":
    st.title("검토 작업대")
    if not records:
        st.info("영수증함에서 샘플 또는 파일을 등록하세요.")
        st.stop()
    ids = [r["id"] for r in records]
    if st.session_state.get("selected") not in ids:
        st.session_state.selected = ids[0]
    selected = st.selectbox("영수증 선택", ids, key="selected", format_func=lambda value:
        next(f"{r['merchant'] or r['file_name']} · {r['status']} · {r['id'][:8]}" for r in records if r["id"] == value))
    r = next(r for r in records if r["id"] == selected)
    locked = r["status"] == "확정 완료"
    key = f"{r['id']}_{r['version']}"
    st.caption(f"{r['status']} · {'가상 샘플' if r['source'] == 'sample' else '사용자 업로드 · 수동 입력'} · 저장 버전 {r['version']}")
    if locked:
        st.success("확정된 전표입니다. 수정하려면 아래에서 확정을 취소하세요.")
    left, right = st.columns([4, 6], gap="large")
    with left:
        st.subheader("원본 증빙")
        if r["source"] == "sample":
            st.caption("시연용 가상 영수증 · 오른쪽 입력값을 수정해도 원본은 유지됩니다.")
            focus = st.selectbox("근거 위치 강조", ["거래처", "거래일", "사업자번호", "공급가액", "부가세", "결제금액", "승인번호"], key=f"focus_{r['id']}")
            original = r["original"]
            mapping = {"거래처": "merchant", "거래일": "date", "사업자번호": "business_number", "공급가액": "supply", "부가세": "vat", "결제금액": "total", "승인번호": "approval"}
            receipt_html = '<div class="receipt"><h3>JOURNALIZER<br>가상 영수증</h3><hr>'
            for label, field in mapping.items():
                value = won(original[field]) if field in ("total", "supply", "vat") else original[field]
                receipt_html += f'<div class="field {"active" if focus == label else ""}"><span>{label}</span><strong>{html.escape(str(value))}</strong></div>'
            receipt_html += "<hr>"
            for item in original["items"]:
                receipt_html += f"<div>{html.escape(item['품목'])} × {item['수량']} · {won(item['금액'])}</div>"
            receipt_html += '<hr><small>실제 결제·세무 증빙이 아닙니다.</small></div>'
            st.markdown(receipt_html, unsafe_allow_html=True)
        else:
            path = Path(r["file_path"])
            if path.is_file():
                data = path.read_bytes()
                if path.suffix.lower() == ".pdf":
                    encoded = base64.b64encode(data).decode()
                    components.html(f'<embed src="data:application/pdf;base64,{encoded}" type="application/pdf" width="100%" height="600">', height=620)
                    st.caption("PDF 미리보기가 지원되지 않는 브라우저에서는 원본을 다운로드하세요.")
                else:
                    rotation = st.select_slider("회전", options=[0, 90, 180, 270], key=f"rotate_{r['id']}")
                    st.image(Image.open(io.BytesIO(data)).rotate(rotation, expand=True), width="stretch")
                st.download_button("원본 다운로드", data, file_name=r["file_name"])
            else:
                st.error("원본 파일을 찾을 수 없습니다.")
        if r["items"]:
            st.caption("추출된 품목 (샘플)")
            st.dataframe(r["items"], hide_index=True, width="stretch")
    with right:
        st.subheader("1. 추출값 확인")
        for issue in r["issues"]:
            st.info(issue)
        with st.form(f"fields_{key}"):
            merchant = st.text_input("거래처", r["merchant"], disabled=locked)
            a, b = st.columns(2)
            transaction_date = a.date_input("거래일", date.fromisoformat(r["date"]), disabled=locked)
            business = b.text_input("사업자번호", r["business_number"], disabled=locked)
            a, b, c = st.columns(3)
            supply = a.number_input("공급가액", min_value=0, value=int(r["supply"]), step=100, disabled=locked)
            vat = b.number_input("부가세", min_value=0, value=int(r["vat"]), step=100, disabled=locked)
            total = c.number_input("결제금액", min_value=0, value=int(r["total"]), step=100, disabled=locked)
            a, b = st.columns(2)
            payment = a.selectbox("결제수단", ["신용카드", "체크카드", "현금"], index=["신용카드", "체크카드", "현금"].index(r["payment"]), disabled=locked)
            approval = b.text_input("승인번호", r["approval"], disabled=locked)
            category = st.selectbox("비용 계정", list(ACCOUNTS)[:7], index=list(ACCOUNTS)[:7].index(r["category"]), disabled=locked)
            purpose = r["purpose"]
            if r["kind"] == "meal":
                purpose = st.selectbox("식사 사용 목적", ["선택 필요"] + list(PURPOSES), index=(["선택 필요"] + list(PURPOSES)).index(r["purpose"]), disabled=locked)
            meal = st.checkbox("식사 사용 목적 질문 사용", value=r["kind"] == "meal", disabled=locked)
            deductible = st.checkbox("부가세를 별도 계정으로 분리", value=r["deductible"], disabled=locked)
            st.caption("세액 분리는 시연용 사용자 선택입니다. 거래처 미팅 샘플은 비용에 포함합니다.")
            memo = st.text_input("적요", r["memo"], disabled=locked)
            st.caption("위 입력값은 아래 버튼으로 저장해야 분개와 확정에 반영됩니다. 저장하면 수동 편집한 분개를 새 초안으로 교체합니다.")
            if st.form_submit_button("입력값 저장 · 분개 다시 추천", disabled=locked, type="primary"):
                updated = copy.deepcopy(r)
                updated.update(merchant=merchant, date=transaction_date.isoformat(), business_number=business,
                    supply=supply, vat=vat, total=total, payment=payment, approval=approval,
                    category=category, purpose=purpose, deductible=deductible, memo=memo,
                    reviewed=False, duplicate_ok=False)
                if meal:
                    updated["kind"] = "meal"
                elif updated["kind"] == "meal":
                    updated["kind"] = "normal"
                updated["lines"] = recommend(updated)
                commit(updated, "입력값 저장 및 재추천")
        st.subheader("2. 분개 검토")
        expense = PURPOSES.get(r["purpose"], r["category"]) if r["kind"] == "meal" else r["category"]
        with st.expander("이렇게 추천했어요", expanded=True):
            st.write(f"• {'사용 목적 ' + r['purpose'] if r['kind'] == 'meal' else '샘플 또는 사용자 지정 계정'} → **{expense}**")
            st.write(f"• 결제수단 {r['payment']} → **{ {'신용카드':'미지급금', '체크카드':'보통예금', '현금':'현금'}[r['payment']] }**")
            st.write("• 부가세는 사용자의 분리 선택과 샘플 규칙을 반영합니다.")
            if r["kind"] == "cancel":
                st.write("• 취소 증빙이므로 일반 거래와 차변·대변을 반대로 생성했습니다.")
            if r["kind"] == "split":
                st.write("• 샘플 품목 금액 비율 4:3에 따라 비용을 두 계정으로 나눴습니다.")
            st.caption("아래에서 직접 수정한 분개는 이 추천 설명과 다를 수 있습니다.")
        frame = pd.DataFrame(r["lines"], columns=["차/대", "계정과목", "금액", "적요"])
        frame["금액"] = pd.to_numeric(frame["금액"]).astype(float)
        edited = st.data_editor(frame, num_rows="dynamic" if not locked else "fixed", hide_index=True,
            width="stretch", disabled=locked, key=f"editor_{key}", column_config={
                "차/대": st.column_config.SelectboxColumn(options=["차변", "대변"], required=True),
                "계정과목": st.column_config.SelectboxColumn(options=list(ACCOUNTS), required=True),
                "금액": st.column_config.NumberColumn(min_value=0, step=1, format="%.0f", required=True),
                "적요": st.column_config.TextColumn(),
            })
        working = copy.deepcopy(r)
        working["lines"] = json.loads(edited.to_json(orient="records", force_ascii=False))
        debit = sum((row.get("금액") or 0) for row in working["lines"] if row.get("차/대") == "차변")
        credit = sum((row.get("금액") or 0) for row in working["lines"] if row.get("차/대") == "대변")
        a, b, c = st.columns(3)
        a.metric("차변 합계", won(debit))
        b.metric("대변 합계", won(credit))
        c.metric("차액", won(debit - credit))
        matches = duplicates(r, records)
        if matches:
            st.warning("중복 의심: 승인번호·거래일·금액이 같은 영수증이 있습니다.")
            st.dataframe(table_rows(matches), hide_index=True, width="stretch")
            working["duplicate_ok"] = st.checkbox("중복 여부를 확인했으며 별도 거래로 처리합니다", value=r["duplicate_ok"], key=f"dup_{key}", disabled=locked)
        working["reviewed"] = st.checkbox("원본·금액·계정과목을 확인했습니다", value=r["reviewed"], key=f"review_{key}", disabled=locked)
        errors = validate(working, records)
        if not locked and errors:
            for error in errors:
                st.warning(error)
        if not locked:
            a, b = st.columns(2)
            if a.button("분개 임시 저장", key=f"save_{key}"):
                commit(working, "분개 임시 저장")
            if b.button("검토 완료 · 확정", type="primary", disabled=bool(errors), key=f"confirm_{key}"):
                working["status"] = "확정 완료"
                commit(working, "분개 확정")
        elif st.button("확정 취소 · 다시 검토", key=f"undo_{key}"):
            updated = copy.deepcopy(r)
            updated.update(status="검토 필요", reviewed=False)
            commit(updated, "확정 취소")
    with st.expander("수정 · 확정 이력"):
        for entry in reversed(r["history"]):
            st.write(f"**{entry['작업']}** · {entry['시각']}")
            if entry["내용"]:
                st.text(entry["내용"])

elif page == "분개장":
    st.title("분개장")
    confirmed = [r for r in records if r["status"] == "확정 완료"]
    a, b, c = st.columns(3)
    period = a.date_input("거래 기간", value=(date(2026, 1, 1), date(2026, 12, 31)))
    query = b.text_input("거래처 검색")
    account = c.selectbox("포함 계정과목", ["전체"] + list(ACCOUNTS))
    filtered = [r for r in confirmed if query in r["merchant"]
        and (len(period) != 2 or period[0].isoformat() <= r["date"] <= period[1].isoformat())
        and (account == "전체" or any(row["계정과목"] == account for row in r["lines"]))]
    if not filtered:
        st.info("표시할 확정 분개가 없습니다. 검토 작업대에서 영수증을 확정하세요.")
    else:
        selected_ids = st.multiselect("내보낼 전표 (기본: 현재 필터의 전체)", [r["id"] for r in filtered],
            default=[r["id"] for r in filtered], format_func=lambda value:
                next(f"{r['merchant']} · {r['id'][:8]}" for r in filtered if r["id"] == value))
        export_records = [r for r in filtered if r["id"] in selected_ids]
        rows = [{"전표": r["id"][:8], "거래일": r["date"], "거래처": r["merchant"],
                 "계정코드": ACCOUNTS[row["계정과목"]], **row} for r in filtered for row in r["lines"]]
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("계정 필터에 해당하는 전표의 모든 분개 행을 표시·내보냅니다.")
        st.download_button(f"선택한 {len(export_records)}건 CSV 다운로드", export_csv(export_records),
            file_name="journalizer_journal.csv", mime="text/csv", disabled=not export_records, type="primary")
        target = st.selectbox("원본 증빙 보기", [r["id"] for r in filtered], format_func=lambda value:
            next(f"{r['merchant']} · {r['id'][:8]}" for r in filtered if r["id"] == value))
        st.button("검토 화면으로 이동", on_click=open_record, args=(target,))

else:
    st.title("설정 / 데모")
    st.subheader("프로토타입 범위")
    st.write("샘플 분석, 직접 파일 등록, 추출값 수정, 목적 질문, 분개 편집, 검증, 확정·취소, 이력, CSV 다운로드를 체험할 수 있습니다.")
    st.caption("실제 OCR, 세무 판단, 외부 회계 연동, 사용자 인증, 동시 편집은 포함하지 않습니다.")
    st.subheader("계정과목표 (시연용 · 조회 전용)")
    st.dataframe([{"계정코드": code, "계정과목": name} for name, code in ACCOUNTS.items()], hide_index=True)
    st.subheader("데모 초기화")
    st.write("현재 목록과 편집 이력을 초기 샘플 6건으로 교체합니다. 업로드 파일은 디스크에 남습니다.")
    reset_ok = st.checkbox("현재 프로토타입 데이터를 초기화합니다")
    if st.button("초기 샘플로 되돌리기", disabled=not reset_ok):
        save(make_samples())
        st.session_state.clear()
        st.rerun()
