"""Accounting judgment by the connected LLM (Claude or GPT): one recommendation per bank row or receipt.

The model picks the counter account (from the company chart only), the deductible VAT,
the vendor name and a short purpose. Recommendations are written into fields a person
has not edited and recorded in `autofilled` with an "AI 추천" reason, so the screen can
highlight them until someone confirms. Confirmed past bookings are sent as examples so
the model follows the company's own classification.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os

from app.models import AiSuggestion, BankEntry, CompanyProfile, EntryView, ReceiptRecord, Workspace
from app.services import bank_entries, llm
from app.services.llm import File, LlmError as AiJudgeError

MODEL = llm.DEFAULT_ANTHROPIC_MODEL
EFFORT = os.environ.get("CLAUDE_JUDGE_EFFORT", "medium")
BATCH = 15
WORKERS = 8
MAX_EXAMPLES = 60
MAX_PRECEDENTS = 250
AI_PREFIX = "AI 추천"

SYSTEM = """\
당신은 한국 법인의 기장 담당자입니다. 회사 통장 거래와 영수증을 하나씩 보고, 회사 계정과목표에서 분개할 상대 계정을 하나만 추천합니다.
결과는 그대로 장부에 자동 반영되므로 회사 정보와 과거 분류에 맞게 신중하게 고르세요. 확실하지 않으면 confidence를 낮게 주고 이유에 확인할 점을 적으세요.

판단 기준
- direction이 "출금"이면 돈이 나간 이유(비용·자산 취득·부채 상환)의 계정을, "입금"이면 돈이 들어온 이유(수익·자본·채권 회수·환급)의 계정을 고릅니다.
- 보통예금 같은 은행 계정 자체와 미지급금(카드 정산)은 시스템이 처리하므로 고르지 마세요. card가 true인 출금도 쓰인 용도의 비용 계정을 고릅니다.
- 회사의 과거 확정 분류(examples)와 거래처별 기본 계정이 있으면 그것을 가장 우선해 같은 방식으로 분류합니다.
- 회사 정보(사업자등록증·등기부등본·주주명부)를 참고합니다. 주주·대표자 이름과 같은 개인 명의 입금은 설립 무렵이면 자본금 납입, 그 밖에는 가수금·차입 여부를 이유에 적습니다.
- 회사 업종에 직접 쓰이는 개발 도구·에셋·플랫폼 결제는 업종에 맞는 개발 관련 비용 계정(예: 외주개발비)을, 일반 사무용 구입은 소모품비를 고릅니다.
- 같은 거래처·같은 성격의 거래는 항상 같은 계정으로 분류합니다.
- 세무서에서 들어온 입금은 부가세 환급이면 부가세대급금(환급받은 매입세액을 줄임)을 고릅니다.
- same_amount_rows에 같은 금액·같은 방향의 다른 거래 적요가 있으면 반복 거래(예: 매달 같은 금액의 개인 이체와 "임대료" 이체)의 성격을 함께 보고 판단합니다.
- 예금 결산이자는 이자수익, 재단·지자체 지원금은 잡이익, 매출처 입금은 외상매출금 회수가 일반적입니다.
- 카드 대금 결제(예: "OO카드결제대금") 출금은 그 카드로 산 영수증이 미지급(외상매입금)으로 남아 있을 때 외상매입금 상환입니다. "카드결제+카드번호"처럼 보이는 수백 원 단위의 소액 출금은 카드 수수료이므로 지급수수료입니다.
- 이미 외상매입금으로 잡힌 거래처에 대한 지급은 외상매입금입니다. 은행 수수료·인증서·OTP 발급비는 지급수수료입니다.
- tax는 영수증에 국내 사업자등록번호와 부가세가 따로 적혀 있고 매입세액 공제 대상일 때만 그 금액, 아니면 0입니다. 접대비, 면세품, 해외 결제, 영수증이 없는 통장 거래는 0입니다.
- vendor는 거래처의 상호를 적습니다. 카드번호 접두("2462 ")와 "취소"는 빼세요.
- purpose는 영수증 항목에서 사용 목적·적요를 15자 이내로 적습니다. 통장 거래면 빈 문자열도 됩니다.
- reason은 판단 근거를 한국어 한 문장으로 적습니다.
- 모든 항목에 대해 id를 그대로 돌려주고, 항목을 빠뜨리지 마세요."""


def _schema(codes: list[str]) -> dict:
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "account": {"type": "string", "enum": codes},
            "tax": {"type": "integer"},
            "vendor": {"type": "string"},
            "purpose": {"type": "string"},
            "reason": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["id", "account", "tax", "vendor", "purpose", "reason", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": item}},
        "required": ["results"],
        "additionalProperties": False,
    }


def _chart(workspace: Workspace) -> tuple[str, list[str]]:
    kinds = {"asset": "자산", "liability": "부채", "equity": "자본", "revenue": "수익", "expense": "비용", "other": "기타"}
    banks = {b.account for b in workspace.banks}
    usable = [a for a in workspace.accounts if a.code not in banks and a.code not in ("25300", "40000")]
    text = "\n".join(f"{a.code} {a.name} ({kinds.get(a.kind, a.kind)})" for a in usable)
    vendor_lines = [f"- {v.name}{f' / {v.alias}' if v.alias else ''}: {v.account}" for v in workspace.vendors if v.account and not v.auto]
    if vendor_lines:
        text += "\n\n거래처별 기본 계정\n" + "\n".join(vendor_lines[:80])
    return text, [a.code for a in usable]


def bank_item(view: EntryView, peers: list[str] = ()) -> dict:
    tx = view.transaction
    return {
        "same_amount_rows": list(peers)[:5],
        "id": tx.id,
        "type": "통장 거래",
        "date": tx.posted_at[:10],
        "direction": "출금" if tx.withdrawal else "입금",
        "amount": tx.withdrawal or tx.deposit,
        "description": tx.description,
        "bank_kind": tx.kind,
        "bank": tx.bank_name,
        "card": bool(view.card),
        "cancel": bank_entries.is_cancel(tx),
    }


def receipt_item(r: ReceiptRecord, payment: str = "") -> dict:
    ext = r.extraction
    return {
        "id": r.id,
        "type": "영수증",
        "date": r.date,
        "direction": "출금",
        "amount": r.total,
        "merchant": r.merchant,
        "tax_on_receipt": r.original_tax,
        "business_number": (ext.business_number if ext else None) or "",
        "payment_method": ext.payment_method if ext else "",
        "items": [i.description for i in ext.line_items][:8] if ext else [],
        "paid_from_bank": payment,
    }


def peers(views: list[EntryView]) -> dict[str, list[str]]:
    """Other rows with the same amount and direction, so recurring transfers are judged together."""
    groups: dict[tuple, list[EntryView]] = {}
    for v in views:
        tx = v.transaction
        groups.setdefault((bool(tx.withdrawal), tx.withdrawal or tx.deposit), []).append(v)
    return {v.tx_id: [f"{o.transaction.posted_at[:10]} {o.transaction.description}" for o in group if o.tx_id != v.tx_id]
            for group in groups.values() if len(group) > 1 for v in group}


def examples(entries: list[EntryView], receipts: list[ReceiptRecord], workspace: Workspace) -> list[str]:
    names = {a.code: a.name for a in workspace.accounts}
    found = []
    for r in sorted((x for x in receipts if x.confirmed and x.account), key=lambda x: x.confirmed_at or "", reverse=True):
        found.append(f"영수증 | {r.merchant} | {r.total:,}원 | 부가세 {r.tax if r.vat == 'deductible' else 0:,} → {r.account} {names.get(r.account, '')} ({r.purpose})")
    for v in entries:
        if v.confirmed and v.account and not v.transfer_to:
            tx = v.transaction
            found.append(f"통장 {'출금' if tx.withdrawal else '입금'} | {tx.description} | {(tx.withdrawal or tx.deposit):,}원 → {v.account} {names.get(v.account, '')}")
    # Keep one example per description so a single frequent vendor does not crowd out the rest.
    unique = list(dict.fromkeys(found))[:MAX_EXAMPLES]
    # The company's earlier ledger comes next: how it booked the same 적요·거래처 before.
    past = [f"이전 장부 | {p.text} → {p.account} {names.get(p.account, '')} ({p.count}회)"
            for p in sorted(workspace.precedents, key=lambda p: -p.count)]
    return unique + past[:MAX_PRECEDENTS]


def _vat_policy(workspace: Workspace) -> str:
    if workspace.card_vat == "expense":
        return "회사 기준: 카드 영수증의 부가세는 공제하지 않고 비용에 포함(카드 영수증 tax는 항상 0, 세금계산서만 분리)"
    return "회사 기준: 카드 영수증도 공제 요건을 갖추면 부가세를 분리"


def profile_text(workspace: Workspace) -> str:
    p = workspace.profile
    if p is None or p.error:
        return f"회사명 {workspace.company or '미상'} · 회계연도 {workspace.year} · {_vat_policy(workspace)}"
    parts = [
        f"회사명 {p.company or workspace.company}", f"사업자등록번호 {p.business_number}" if p.business_number else "",
        f"대표자 {p.representative}" if p.representative else "", f"개업일 {p.opened}" if p.opened else "",
        f"업태·종목 {p.business_type}" if p.business_type else "", f"자본금 {p.capital:,}원" if p.capital else "",
        f"주주 {', '.join(p.shareholders)}" if p.shareholders else "", f"임원 {', '.join(p.officers)}" if p.officers else "",
        p.summary,
    ]
    parts.append(_vat_policy(workspace))
    return " · ".join(x for x in parts if x) + f" · 회계연도 {workspace.year}"


def _call(client: llm.Client, batch: list[dict], chart: str, codes: list[str], history: list[str], profile: str = "") -> list[dict]:
    content = (
        "회사 정보\n" + (profile or "없음")
        + "\n\n계정과목표\n" + chart
        + "\n\n회사의 과거 확정 분류 (examples)\n" + ("\n".join(history) if history else "없음")
        + "\n\n분류할 항목\n" + json.dumps(batch, ensure_ascii=False)
    )
    answer = llm.complete(client, SYSTEM, [content], _schema(codes), effort=EFFORT)
    results = answer.get("results")
    if not isinstance(results, list):
        raise AiJudgeError("모델 응답을 읽지 못했습니다.")
    return [r for r in results if isinstance(r, dict)]


def recommend(items: list[dict], workspace: Workspace, history: list[str], client: llm.Client) -> dict[str, AiSuggestion]:
    """Ask the model for one recommendation per item. Items it skips or answers with unknown ids are left out."""
    if not items:
        return {}
    chart, codes = _chart(workspace)
    now = datetime.now().isoformat(timespec="seconds")
    result: dict[str, AiSuggestion] = {}
    pending = items
    for _ in range(2):  # the model occasionally skips an item in a long batch; ask once more for those
        batches = [pending[i:i + BATCH] for i in range(0, len(pending), BATCH)]
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            answers = list(pool.map(lambda b: _call(client, b, chart, codes, history, profile_text(workspace)), batches))
        wanted = {i["id"] for i in pending}
        for answer in answers:
            for row in answer:
                if row.get("id") in wanted and row.get("account") in codes:
                    try:
                        tax, confidence = max(0, int(row.get("tax") or 0)), float(row.get("confidence") or 0)
                    except (TypeError, ValueError):
                        continue  # a malformed row from a provider without strict schemas
                    result[row["id"]] = AiSuggestion(
                        account=row["account"], tax=tax, vendor=str(row.get("vendor") or "").strip(),
                        purpose=str(row.get("purpose") or "").strip(), reason=str(row.get("reason") or "").strip(),
                        confidence=confidence, model=client.label, at=now,
                    )
        pending = [i for i in pending if i["id"] not in result]
        if not pending:
            break
    return _consistent(items, result)


def _name_key(item: dict) -> tuple:
    name = item.get("merchant") or item.get("description") or ""
    match = bank_entries.CARD_ROW.match(name.strip())
    name = bank_entries.CANCEL.sub("", match.group(2) if match else name)
    return item.get("type"), item.get("direction"), bank_entries.normalize_name(name)


def _consistent(items: list[dict], result: dict[str, AiSuggestion]) -> dict[str, AiSuggestion]:
    """Give every row of the same counterparty the account most of its rows got (ties: highest confidence)."""
    groups: dict[tuple, list[str]] = {}
    for item in items:
        key = _name_key(item)
        if key[2] and item["id"] in result and not item.get("cancel"):
            groups.setdefault(key, []).append(item["id"])
    for ids in groups.values():
        votes: dict[str, list[float]] = {}
        for i in ids:
            votes.setdefault(result[i].account, []).append(result[i].confidence)
        if len(votes) < 2:
            continue
        winner = max(votes, key=lambda a: (len(votes[a]), max(votes[a])))
        source = next(result[i] for i in ids if result[i].account == winner)
        for i in ids:
            if result[i].account != winner:
                result[i] = result[i].model_copy(update={
                    "account": winner, "confidence": min(result[i].confidence, source.confidence),
                    "reason": f"같은 거래처 {len(ids)}건 중 {len(votes[winner])}건의 판단에 맞춤 · {source.reason}",
                })
    return result


PROFILE_PROMPT = """\
첨부한 회사 서류(사업자등록증, 법인 등기사항증명서, 주주명부)에서 회사 정보를 읽어 주세요.
없는 항목은 빈 값(숫자는 0)으로 두세요. business_type에는 업태와 종목을 함께 적고,
shareholders에는 "이름 (지분 또는 주식 수)" 형식으로, officers에는 "직위 이름" 형식으로 적습니다.
summary에는 회사가 하는 일을 한 문장으로 적습니다."""

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"}, "business_number": {"type": "string"}, "representative": {"type": "string"},
        "opened": {"type": "string"}, "business_type": {"type": "string"}, "capital": {"type": "integer"},
        "shareholders": {"type": "array", "items": {"type": "string"}}, "officers": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["company", "business_number", "representative", "opened", "business_type", "capital", "shareholders", "officers", "summary"],
    "additionalProperties": False,
}


def _file(name: str, content_type: str, data: bytes) -> File:
    return File(name=name, media_type=content_type, data=data)


def read_profile(documents: list[tuple[str, str, str, bytes]], client: llm.Client) -> CompanyProfile:
    """Read company facts from the registration documents: (label, file name, content type, bytes)."""
    parts: list = []
    for label, name, content_type, data in documents:
        parts += [f"[{label}]", _file(name, content_type, data)]
    parts.append(PROFILE_PROMPT)
    try:
        data = llm.complete(client, "", parts, PROFILE_SCHEMA, effort="low", max_tokens=8000)
        return CompanyProfile.model_validate(data)
    except AiJudgeError as exc:
        raise AiJudgeError(f"회사 서류를 읽지 못했습니다 ({exc}).") from exc
    except ValueError as exc:
        raise AiJudgeError("회사 서류를 읽지 못했습니다 (응답 형식).") from exc


def _replaceable(autofilled: dict[str, str], field: str, value) -> bool:
    """Fill empty fields and our own earlier suggestions; never what a person typed or the company history set."""
    reason = autofilled.get(field)
    if reason is None:
        return value in ("", 0, None)
    return not bank_entries.history_reason(reason)


def _reason(s: AiSuggestion) -> str:
    return f"{AI_PREFIX} · {s.reason}" if s.reason else AI_PREFIX


def apply_to_entry(entry: BankEntry, tx_amount: int, s: AiSuggestion) -> None:
    entry.ai = s
    if entry.transfer_to:
        return
    if _replaceable(entry.autofilled, "account", entry.account):
        entry.account = s.account
        entry.autofilled["account"] = _reason(s)
    tax = s.tax if 0 <= s.tax < tax_amount_limit(tx_amount) else 0
    if _replaceable(entry.autofilled, "tax", entry.tax) and tax != entry.tax:
        entry.tax = tax
        entry.autofilled["tax"] = _reason(s)
    if s.vendor and _replaceable(entry.autofilled, "vendor", entry.vendor) and s.vendor != entry.vendor:
        entry.vendor = s.vendor
        entry.autofilled["vendor"] = _reason(s)


def tax_amount_limit(amount: int) -> int:
    # VAT is 10/110 of the total; anything above that is a model mistake.
    return amount // 11 + 2


def apply_to_receipt(r: ReceiptRecord, s: AiSuggestion, card_vat: str = "deduct") -> None:
    if card_vat == "expense" and not (r.extraction and r.extraction.document_type == "tax_invoice"):
        s = s.model_copy(update={"tax": 0})  # company practice: VAT on card slips stays in the cost
    r.ai = s
    if _replaceable(r.autofilled, "account", r.account):
        r.account = s.account
        r.autofilled["account"] = _reason(s)
    if r.account == "81300" and r.vat == "deductible" and "vat" in r.autofilled:
        r.vat = "included"  # 접대비: VAT is never deductible, whatever the card-slip policy
        r.autofilled["vat"] = "회사 기준 · 접대비 부가세는 공제받을 수 없어 비용에 포함"
    if r.vat == "pending" or ("vat" in r.autofilled and not bank_entries.history_reason(r.autofilled["vat"])):
        tax = s.tax if 0 < s.tax < tax_amount_limit(r.total) else 0
        r.vat = "deductible" if tax else "included"
        r.autofilled["vat"] = _reason(s)
        typed = "tax" not in r.autofilled and r.tax not in (0, r.original_tax)  # OCR copies original_tax; anything else was typed
        if tax and not typed:
            r.tax = tax
            r.autofilled["tax"] = _reason(s)
    if s.purpose and _replaceable(r.autofilled, "purpose", r.purpose.strip()):
        r.purpose = s.purpose
        r.autofilled["purpose"] = _reason(s)
    if not r.evidence.strip() and r.extraction:
        r.evidence = f"{AI_PREFIX} · 영수증 표시 세액 {r.original_tax:,}원 · {s.reason}"[:200]
        r.autofilled["evidence"] = _reason(s)


SALES_PROMPT = """\
첨부한 문서는 우리 회사({company})가 발행한 매출 세금계산서입니다. 다음을 읽어 주세요.
issued는 작성일자(YYYY-MM-DD), customer는 공급받는자 상호, business_number는 공급받는자 사업자등록번호,
representative는 공급받는자 대표자, item은 첫 품목명, supply는 공급가액 합계, tax는 세액 합계(정수).
우리 회사가 공급자가 아니면 note에 그 사실을 적으세요."""

SALES_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "integer"} if k in ("supply", "tax") else {"type": "string"}
                   for k in ("issued", "customer", "business_number", "representative", "item", "supply", "tax", "note")},
    "required": ["issued", "customer", "business_number", "representative", "item", "supply", "tax", "note"],
    "additionalProperties": False,
}


def read_sales_invoice(name: str, content_type: str, data: bytes, company: str, client: llm.Client) -> dict:
    """Read a 매출 세금계산서 into the SalesInvoice fields."""
    try:
        return llm.complete(client, "", [_file(name, content_type, data), SALES_PROMPT.format(company=company or "미상")],
                            SALES_SCHEMA, effort="low", max_tokens=4000)
    except AiJudgeError as exc:
        raise AiJudgeError(f"세금계산서를 읽지 못했습니다 ({exc}).") from exc


RECEIPT_VAT_PROMPT = """\
이 카드 영수증(매출전표)에서 부가가치세를 다시 읽어 주세요. 가맹점은 법인이라 보통 부가세가 표시됩니다.
"부가세", "부가가치세", "VAT", "세액" 같은 항목의 금액을 tax로, "공급가액", "과세물품가액" 금액을 supply로,
합계를 total로 적습니다. 면세 품목만 있거나 부가세 칸이 정말 없으면 tax는 0이고 note에 그 이유를 적으세요."""

RECEIPT_VAT_SCHEMA = {
    "type": "object",
    "properties": {"supply": {"type": "integer"}, "tax": {"type": "integer"}, "total": {"type": "integer"}, "note": {"type": "string"}},
    "required": ["supply", "tax", "total", "note"],
    "additionalProperties": False,
}


def read_receipt_vat(name: str, content_type: str, data: bytes, client: llm.Client) -> dict:
    """Re-read only the VAT of a receipt the OCR read as 0."""
    try:
        return llm.complete(client, "", [_file(name, content_type, data), RECEIPT_VAT_PROMPT], RECEIPT_VAT_SCHEMA, effort="low", max_tokens=4000, timeout=120)
    except AiJudgeError as exc:
        raise AiJudgeError(f"영수증을 다시 읽지 못했습니다 ({exc}).") from exc


SCHEDULE_PROMPT = """\
첨부한 문서는 부가가치세 신고서의 부속서류(신용카드매출전표 등 수령명세서, 매입처별 세금계산서합계표 등)입니다.
공급자(가맹점·매입처)별 행을 모두 읽어 rows에 넣으세요. kind는 카드·현금영수증이면 "card", 세금계산서면 "invoice",
business_number는 공급자 사업자등록번호(000-00-00000), name은 상호(없으면 빈 문자열), count는 거래 건수(매수),
supply는 공급가액, tax는 세액입니다. 합계·소계 행은 넣지 마세요."""

SCHEDULE_SCHEMA = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["card", "invoice"]}, "business_number": {"type": "string"}, "name": {"type": "string"},
                       "count": {"type": "integer"}, "supply": {"type": "integer"}, "tax": {"type": "integer"}},
        "required": ["kind", "business_number", "name", "count", "supply", "tax"],
        "additionalProperties": False,
    }}},
    "required": ["rows"],
    "additionalProperties": False,
}


def read_vat_schedule(name: str, content_type: str, data: bytes, table_text: str, client: llm.Client) -> list[dict]:
    """Read a VAT return schedule (PDF/image, or a spreadsheet already turned into text) into supplier rows."""
    source = ("부속서류 표:\n" + table_text) if table_text else _file(name, content_type, data)
    try:
        rows = llm.complete(client, "", [source, SCHEDULE_PROMPT], SCHEDULE_SCHEMA, effort="low").get("rows")
    except AiJudgeError as exc:
        raise AiJudgeError(f"부속서류를 읽지 못했습니다 ({exc}).") from exc
    if not isinstance(rows, list):
        raise AiJudgeError("부속서류를 읽지 못했습니다 (응답 형식).")
    return rows
