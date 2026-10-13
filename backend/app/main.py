from datetime import date, datetime
from decimal import Decimal
import mimetypes
import os
import re
from urllib.parse import quote, urlencode

from dotenv import load_dotenv

# Pick up OCR credentials from backend/.env however the server is started.
# Variables already set in the shell take precedence.
load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from app.models import (
    Account,
    BankEntry,
    BankTransaction,
    BulkIds,
    BulkUpdate,
    CompanyProfile,
    EntryReview,
    EntryView,
    JournalEntryDraft,
    LedgerView,
    PaymentCandidate,
    ReceiptExtraction,
    ReceiptRecord,
    ReceiptReview,
    ReceiptView,
    SalesInvoice,
    ScheduleRow,
    VatReturn,
    Vendor,
    Workspace,
)
from app.services import ai_judge, autofill, bank_entries, books, exports, invoice_review, ledger, llm, matching, precedents, sandbox, statements, vat_checks, workspace_setup
from app.services.bank_pdf_parser import ReceiptParseError
from app.services.bank_statements import StatementParseError, parse_statement, read_statement, schedule_table
from app.services.journalizer import build_journal_entry
from app.services.receipts import extract_receipt
from app import auth
from app.limits import Meter, RequestSizeLimit, check_file_count, get_meter, read_upload
from app.auth import User, current_user, get_db, get_store
from app.services.store import Store, company_for_user
from app.services.vision_receipts import VisionResponseError

RECEIPT_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp")

app = FastAPI(title="Journalizer API", version="0.3.0")

from app.hometax_routes import router as hometax_router
app.include_router(hometax_router)
from app.policy_routes import router as policy_router
app.include_router(policy_router)

app.add_middleware(RequestSizeLimit)
app.add_middleware(  # outermost, so 413/429 answers still carry CORS headers
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Accounts ---------------------------------------------------------------

class Signup(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class Login(BaseModel):
    email: str
    password: str


# Documents every company uploads before using the workbench.
REQUIRED_DOCUMENTS = {"registry": "등기부등본", "shareholders": "주주명부", "business": "사업자등록증"}
DOCUMENT_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


def _me(user: User, engine: Engine) -> dict:
    store = Store(engine, company_for_user(engine, user.id))
    onboarded = set(REQUIRED_DOCUMENTS) <= set(store.documents())
    return {**user.model_dump(), "company": store.workspace().company, **store.counts(), "imported": None, "onboarded": onboarded}


@app.get("/auth/config")
def auth_config() -> dict:
    return {"google": auth.google_config() is not None}


@app.post("/auth/signup")
def signup(body: Signup, response: Response, engine: Engine = Depends(get_db)) -> dict:
    email = auth.normalize_email(body.email)
    user = auth.create_user(engine, email, body.name.strip() or email.split("@")[0], password=body.password)
    auth.start_session(engine, response, user.id)
    return _me(user, engine)


@app.post("/auth/login")
def login(body: Login, response: Response, engine: Engine = Depends(get_db)) -> dict:
    key = body.email.strip().lower()
    auth.login_throttle.check(key)
    row = auth.find_user(engine, key)
    if row is None or not auth.verify_password(body.password, row.password_hash):
        auth.login_throttle.fail(key)
        hint = " 구글로 가입한 계정이면 구글로 로그인하세요." if row is not None and row.google_sub and not row.password_hash else ""
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 맞지 않습니다." + hint)
    auth.login_throttle.reset(key)
    auth.start_session(engine, response, row.id)
    return _me(auth._user(row), engine)


@app.post("/auth/logout", status_code=204)
def logout(response: Response, engine: Engine = Depends(get_db), jz_session: str | None = Cookie(default=None)) -> Response:
    auth.end_session(engine, response, jz_session)
    response.status_code = 204
    return response


@app.get("/auth/me")
def me(user: User = Depends(current_user), engine: Engine = Depends(get_db)) -> dict:
    return _me(user, engine)


@app.get("/auth/google/login")
def google_login() -> RedirectResponse:
    return auth.google_login_redirect()


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                    engine: Engine = Depends(get_db)) -> RedirectResponse:
    try:
        if error or not code:
            raise HTTPException(status_code=400, detail="구글 로그인이 취소되었습니다.")
        auth.check_state(request, state)
        user = auth.google_user(engine, auth.google_profile(code))
    except HTTPException as exc:
        return RedirectResponse("/?" + urlencode({"login_error": exc.detail}), status_code=303)
    except Exception:  # network or Google errors
        return RedirectResponse("/?" + urlencode({"login_error": "구글 로그인에 실패했습니다. 잠시 뒤 다시 시도하세요."}), status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.STATE_COOKIE, path="/")
    auth.start_session(engine, response, user.id)
    return response


# --- Company documents ------------------------------------------------------

def _documents(store: Store) -> list[dict]:
    uploaded = store.documents()
    return [{"kind": kind, "label": label, **(uploaded.get(kind) or {"file_name": "", "uploaded_at": ""})}
            for kind, label in REQUIRED_DOCUMENTS.items()]


@app.get("/company/documents")
def list_documents(store: Store = Depends(get_store)) -> list[dict]:
    return _documents(store)


@app.post("/company/documents/{kind}")
async def upload_document(kind: str, file: UploadFile = File(...), store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> list[dict]:
    if kind not in REQUIRED_DOCUMENTS:
        raise HTTPException(status_code=404, detail="알 수 없는 서류입니다.")
    filename = file.filename or kind
    if not filename.lower().endswith(DOCUMENT_SUFFIXES):
        raise HTTPException(status_code=415, detail="서류는 PDF 또는 이미지 파일만 올릴 수 있습니다.")
    content = await read_upload(file)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")
    if ai_enabled():
        meter.charge("profile")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    store.save_document(kind, filename, content, content_type)
    workspace = store.workspace()
    workspace.profile = None  # documents changed: read them again
    store.save_workspace(workspace)
    if ai_enabled():
        await run_in_threadpool(_ensure_profile, store)
    return _documents(store)


@app.get("/company/documents/{kind}/file")
def document_file(kind: str, store: Store = Depends(get_store)) -> Response:
    original = store.document_file(kind)
    if original is None:
        raise HTTPException(status_code=404, detail="올린 서류가 없습니다.")
    return Response(original.content, media_type=mimetypes.guess_type(original.name)[0] or original.content_type, headers={
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(original.name),
        "Cache-Control": "private, max-age=3600",
    })


@app.post("/journal-entries/draft", response_model=JournalEntryDraft)
def draft_journal_entry(receipt: ReceiptExtraction, _: User = Depends(current_user)) -> JournalEntryDraft:
    return build_journal_entry(receipt)


# --- Workspace --------------------------------------------------------------

@app.get("/workspace", response_model=Workspace)
def get_workspace(store: Store = Depends(get_store)) -> Workspace:
    return store.workspace()


@app.put("/workspace", response_model=Workspace)
def put_workspace(workspace: Workspace, store: Store = Depends(get_store)) -> Workspace:
    codes = [a.code.strip() for a in workspace.accounts]
    if not all(codes) or len(set(codes)) != len(codes):
        raise HTTPException(status_code=422, detail="계정 코드는 비어 있거나 중복될 수 없습니다.")
    bank_codes = [b.code.strip() for b in workspace.banks]
    if not all(bank_codes) or len(set(bank_codes)) != len(bank_codes):
        raise HTTPException(status_code=422, detail="은행 계좌 코드는 비어 있거나 중복될 수 없습니다.")
    card_codes = [c.code.strip() for c in workspace.cards]
    if not all(card_codes) or len(set(card_codes)) != len(card_codes) or set(card_codes) & set(bank_codes):
        raise HTTPException(status_code=422, detail="카드 코드는 비어 있거나 중복되거나 은행 계좌 코드와 같을 수 없습니다.")
    if not workspace.year.isdigit() or len(workspace.year) != 4:
        raise HTTPException(status_code=422, detail="회계연도는 네 자리 연도여야 합니다.")
    workspace.vendors = [v for v in workspace.vendors if v.code.strip() or v.name.strip()]
    before = store.workspace()
    usable = set(codes) - {b.account for b in workspace.banks} - {"25300", "40000"}
    if any(rule.account not in usable for p in before.policies if p.status == "approved" for rule in p.rules):
        raise HTTPException(status_code=422, detail="승인 정책에 연결된 계정은 삭제하거나 추천 제외 계정으로 바꿀 수 없습니다. 먼저 정책을 철회하세요.")
    workspace.profile = workspace.profile or before.profile
    saved = store.save_workspace(workspace)
    if before.card_vat != saved.card_vat:
        for r in store.receipts():
            if autofill.apply_card_vat(r, saved):
                store.save_receipt(r)
    return saved


@app.post("/workspace/from-statements")
async def workspace_from_statements(files: list[UploadFile] = File(...), store: Store = Depends(get_store)) -> dict:
    """Read bank statements and propose 회사 기준 values. Saves nothing."""
    check_file_count(files)
    statements = []
    for file in files:
        content = await read_upload(file)
        try:
            statements.append((file.filename or "statement", await run_in_threadpool(sandbox.run, read_statement, file.filename or "statement", content)))
        except (StatementParseError, sandbox.SandboxError) as exc:
            raise HTTPException(status_code=422, detail=f"{file.filename}: {exc}") from exc
    return workspace_setup.propose(statements, store.workspace())


# --- Receipts ---------------------------------------------------------------

def _get_receipt(store: Store, receipt_id: str) -> ReceiptRecord:
    receipt = store.receipt(receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="영수증을 찾을 수 없습니다.")
    return receipt


def _won(value: Decimal) -> int:
    return int(value.to_integral_value())


def _views(store: Store) -> list[ReceiptView]:
    workspace, receipts = store.workspace(), store.receipts()
    dups = autofill.duplicates(receipts)
    return [ledger.view(r, workspace, dups.get(r.id, "")) for r in receipts]


def _view(store: Store, receipt: ReceiptRecord) -> ReceiptView:
    dups = autofill.duplicates(store.receipts())
    return ledger.view(receipt, store.workspace(), dups.get(receipt.id, ""))


def _apply_extraction(receipt: ReceiptRecord, extraction: ReceiptExtraction) -> None:
    """Copy OCR facts; keeps review input already entered."""
    day = extraction.purchased_at.isoformat()
    receipt.year_confirmed_for = ""
    receipt.merchant = extraction.merchant
    receipt.date = day
    receipt.posting_date = receipt.posting_date or day
    receipt.payment_date = receipt.payment_date or day
    receipt.total = _won(extraction.total)
    receipt.original_tax = _won(extraction.tax)
    receipt.tax = receipt.tax or receipt.original_tax


def _run_autofill(store: Store, ids: set[str] | None = None) -> int:
    """Prefill unconfirmed receipts (all, or `ids`) and save the ones that changed."""
    workspace, receipts, transactions = store.workspace(), store.receipts(), store.transactions()
    dups = autofill.duplicates(receipts)
    workspace_changed, updated = False, 0
    for r in receipts:
        if r.confirmed or (ids is not None and r.id not in ids):
            continue
        before = r.model_dump_json()
        workspace_changed |= autofill.autofill(r, workspace, receipts, transactions, link=r.id not in dups)
        r.account_suggestion, r.account_suggestion_reason = ledger.suggest_account(r, receipts)
        if r.model_dump_json() != before:
            store.save_receipt(r)
            updated += 1
    if workspace_changed:
        store.save_workspace(workspace)
    return updated


async def _run_ocr(filename: str, content: bytes) -> tuple[ReceiptExtraction | None, str | None]:
    try:
        return await run_in_threadpool(extract_receipt, filename=filename, content=content), None
    except ReceiptParseError as exc:
        return None, f"자동 추출을 확인하지 못했습니다: {exc}"
    except VisionResponseError:
        return None, "Google Vision에서 정상적인 OCR 결과를 받지 못했습니다."
    except Exception as exc:  # missing credentials, network errors, model errors
        return None, f"OCR을 실행하지 못했습니다 ({type(exc).__name__}). 원본을 보고 직접 입력하세요."


@app.get("/receipts", response_model=list[ReceiptView])
def list_receipts(store: Store = Depends(get_store)) -> list[ReceiptView]:
    return _views(store)


@app.post("/receipts", response_model=ReceiptView, status_code=201)
async def upload_receipt(file: UploadFile = File(...), store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> ReceiptView:
    """Store the original, OCR it, and prefill from rules. OCR failure still creates a receipt for manual entry."""
    filename = file.filename or "receipt"
    if not filename.lower().endswith(RECEIPT_SUFFIXES):
        raise HTTPException(status_code=415, detail="영수증은 PDF 또는 이미지 파일만 올릴 수 있습니다.")
    content = await read_upload(file)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")
    meter.charge("ocr")
    receipt_id = store.reserve_receipt_id()
    try:
        extraction, error = await _run_ocr(filename, content)
        content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        store.save_receipt_file(receipt_id, filename, content, content_type)
        receipt = ReceiptRecord(
            id=receipt_id,
            file_name=filename,
            content_type=file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
            uploaded_at=datetime.now().isoformat(timespec="seconds"),
            extraction=extraction,
            extraction_error=error,
        )
        if extraction:
            _apply_extraction(receipt, extraction)
        store.save_receipt(receipt)
        _run_autofill(store, {receipt_id})
    except BaseException:
        store.delete_receipt(receipt_id)
        raise
    if extraction and ai_enabled():
        await run_in_threadpool(_recheck_vat, store, {receipt_id})
        await run_in_threadpool(_run_ai, store, set(), {receipt_id})
    return _view(store, _get_receipt(store, receipt_id))


@app.get("/receipts/{receipt_id}", response_model=ReceiptView)
def get_receipt(receipt_id: str, store: Store = Depends(get_store)) -> ReceiptView:
    return _view(store, _get_receipt(store, receipt_id))


def _apply_review(receipt: ReceiptRecord, changes: dict, store: Store) -> ReceiptRecord:
    confirm_year = changes.pop("confirm_year", False)
    confirm_vendor = changes.pop("confirm_vendor", False)
    workspace = store.workspace()
    tx = None
    tx_id = changes.get("payment_transaction_id")
    if tx_id:
        tx = next((t for t in store.transactions() if t.id == tx_id), None)
        if tx is None:
            raise HTTPException(status_code=422, detail="연결할 출금 거래를 찾을 수 없습니다.")
        if invoice_review.invoice(receipt):
            if tx.withdrawal != changes.get("total", receipt.total) or not tx.withdrawal:
                raise HTTPException(status_code=422, detail="세금계산서와 출금 금액이 다릅니다.")
            if any(r.id != receipt.id and r.payment_transaction_id == tx.id for r in store.receipts()):
                raise HTTPException(status_code=409, detail="다른 영수증에 이미 연결된 출금입니다.")
        card = bank_entries.card_of(tx, store.workspace())
        changes.update(
            payment="paid",
            payment_date=tx.posted_at[:10],
            bank=tx.bank,
            card=card.code if card else "",
            payment_link=f"{tx.bank_name} {tx.posted_at[:16].replace('T', ' ')} · {tx.description} · {tx.withdrawal:,}원",
        )
    elif "payment_link" in changes or changes.get("payment") in ("pending", "unpaid"):
        # A typed reference or a non-bank payment replaces any statement link.
        changes["payment_transaction_id"] = ""
        changes["card"] = ""
    updated = receipt.model_copy(update=changes)
    if changes.get("date", receipt.date)[:4] != receipt.date[:4]:
        updated.year_confirmed_for = ""
    if confirm_year:
        try:
            reviewed_date = date.fromisoformat(updated.date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="유효한 거래일을 입력하세요.") from exc
        if str(reviewed_date.year) != workspace.year:
            raise HTTPException(status_code=422, detail="회계연도에 맞는 거래일로 수정한 뒤 연도를 확인하세요.")
        updated.year_confirmed_for = invoice_review.year_review_key(updated, workspace)
    if invoice_review.invoice(updated) and (confirm_vendor or tx):
        try:
            changed = invoice_review.confirm_vendor(updated, workspace, tx)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if changed:
            store.save_workspace(workspace)
    # What the reviewer touched is no longer "automatic".
    updated.autofilled = {k: v for k, v in receipt.autofilled.items() if k not in changes}
    if "vendor_code" in changes:
        vendor = next((v for v in store.workspace().vendors if v.code == updated.vendor_code), None)
        if vendor:
            autofill.vendor_defaults(updated, vendor)
    return updated


@app.patch("/receipts/{receipt_id}", response_model=ReceiptView)
def update_receipt(receipt_id: str, review: ReceiptReview, store: Store = Depends(get_store)) -> ReceiptView:
    receipt = _get_receipt(store, receipt_id)
    changes = review.model_dump(exclude_unset=True, exclude_none=True)
    if receipt.confirmed and changes:
        raise HTTPException(status_code=409, detail="확정된 전표입니다. 검토로 되돌린 뒤 수정하세요.")
    return _view(store, store.save_receipt(_apply_review(receipt, changes, store)))


@app.post("/receipts/bulk-update", response_model=list[ReceiptView])
def bulk_update(body: BulkUpdate, store: Store = Depends(get_store)) -> list[ReceiptView]:
    changes = body.changes.model_dump(exclude_unset=True, exclude_none=True)
    if "payment_transaction_id" in changes:
        raise HTTPException(status_code=422, detail="출금 거래 연결은 영수증마다 따로 해야 합니다.")
    if changes.get("confirm_year") or changes.get("confirm_vendor"):
        raise HTTPException(status_code=422, detail="연도·거래처 확인은 영수증마다 따로 해야 합니다.")
    for receipt_id in body.ids:
        receipt = _get_receipt(store, receipt_id)
        if not receipt.confirmed:
            store.save_receipt(_apply_review(receipt, dict(changes), store))
    return _views(store)


@app.post("/receipts/autofill", response_model=list[ReceiptView])
def autofill_receipts(body: BulkIds, store: Store = Depends(get_store)) -> list[ReceiptView]:
    _run_autofill(store, set(body.ids) if body.ids else None)
    return _views(store)


@app.post("/receipts/bulk-confirm")
def bulk_confirm(body: BulkIds, store: Store = Depends(get_store)) -> dict:
    confirmed, skipped = [], {}
    for receipt_id in body.ids:
        receipt = _get_receipt(store, receipt_id)
        if receipt.confirmed:
            continue
        gaps = _confirm(store, receipt)
        if gaps:
            skipped[receipt_id] = gaps
        else:
            confirmed.append(receipt_id)
    return {"confirmed": confirmed, "skipped": skipped, "receipts": [v.model_dump() for v in _views(store)]}


@app.post("/receipts/delete-duplicates")
def delete_duplicates(store: Store = Depends(get_store)) -> dict:
    """Delete unconfirmed later copies of receipts uploaded more than once."""
    receipts = {r.id: r for r in store.receipts()}
    deleted = [rid for rid in autofill.duplicates(list(receipts.values())) if not receipts[rid].confirmed]
    for rid in deleted:
        store.delete_receipt(rid)
    return {"deleted": deleted}


@app.post("/receipts/{receipt_id}/extract", response_model=ReceiptView)
async def reextract_receipt(receipt_id: str, store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> ReceiptView:
    """Re-run OCR on the stored original, e.g. after fixing OCR credentials.

    Only the extracted facts are refreshed; reviewer input (purpose, account, payment...) is kept.
    """
    receipt = _get_receipt(store, receipt_id)
    if receipt.confirmed:
        raise HTTPException(status_code=409, detail="확정된 전표입니다. 검토로 되돌린 뒤 다시 추출하세요.")
    original = store.receipt_file(receipt_id)
    if original is None:
        raise HTTPException(status_code=404, detail="원본 파일이 없습니다.")
    meter.charge("ocr")
    extraction, error = await _run_ocr(receipt.file_name, original.content)
    if extraction and receipt.extraction is None:
        # AI guesses made from an unreadable receipt must not override newly recovered facts.
        for field in ("account", "purpose", "vat", "tax", "payment", "evidence", "checked"):
            reason = receipt.autofilled.get(field, "")
            if reason.startswith(("AI 추천", "자동 처리")):
                setattr(receipt, field, ReceiptRecord.model_fields[field].default)
                receipt.autofilled.pop(field, None)
        receipt.ai = None
    receipt.extraction, receipt.extraction_error = extraction, error
    if extraction:
        _apply_extraction(receipt, extraction)
    store.save_receipt(receipt)
    _run_autofill(store, {receipt_id})
    return _view(store, _get_receipt(store, receipt_id))


def _confirm(store: Store, receipt: ReceiptRecord) -> list[str]:
    """Confirm if complete and teach the vendor its defaults. Returns what is still missing."""
    current = _view(store, receipt)
    if current.missing:
        return current.missing
    if not current.balanced:
        return ["차변·대변 일치"]
    receipt.confirmed = True
    receipt.confirmed_at = datetime.now().isoformat(timespec="seconds")
    store.save_receipt(receipt)
    workspace = store.workspace()
    if autofill.learn(receipt, workspace):
        store.save_workspace(workspace)
        vendor = next(v for v in workspace.vendors if v.code == receipt.vendor_code)
        for other in store.receipts():
            if not other.confirmed and other.vendor_code == vendor.code and autofill.vendor_defaults(other, vendor):
                store.save_receipt(other)
    return []


@app.post("/receipts/{receipt_id}/confirm", response_model=ReceiptView)
def confirm_receipt(receipt_id: str, store: Store = Depends(get_store)) -> ReceiptView:
    gaps = _confirm(store, _get_receipt(store, receipt_id))
    if gaps:
        raise HTTPException(status_code=409, detail="확인할 항목이 남아 있습니다: " + ", ".join(gaps))
    return _view(store, _get_receipt(store, receipt_id))


@app.post("/receipts/{receipt_id}/reopen", response_model=ReceiptView)
def reopen_receipt(receipt_id: str, store: Store = Depends(get_store)) -> ReceiptView:
    receipt = _get_receipt(store, receipt_id)
    receipt.confirmed = False
    receipt.confirmed_at = None
    return _view(store, store.save_receipt(receipt))


@app.delete("/receipts/{receipt_id}", status_code=204)
def delete_receipt(receipt_id: str, store: Store = Depends(get_store)) -> Response:
    if _get_receipt(store, receipt_id).confirmed:
        raise HTTPException(status_code=409, detail="확정된 전표는 삭제할 수 없습니다. 먼저 검토로 되돌리세요.")
    store.delete_receipt(receipt_id)
    return Response(status_code=204)


@app.get("/receipts/{receipt_id}/file")
def receipt_file(receipt_id: str, store: Store = Depends(get_store)) -> Response:
    _get_receipt(store, receipt_id)
    original = store.receipt_file(receipt_id)
    if original is None:
        raise HTTPException(status_code=404, detail="원본 파일이 없습니다.")
    media_type = mimetypes.guess_type(original.name)[0] or original.content_type
    return Response(original.content, media_type=media_type, headers={
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(original.name),
        "Cache-Control": "private, max-age=3600",
    })


@app.get("/receipts/{receipt_id}/payment-candidates", response_model=list[PaymentCandidate])
def payment_candidates(receipt_id: str, store: Store = Depends(get_store)) -> list[PaymentCandidate]:
    receipt = _get_receipt(store, receipt_id)
    return matching.candidates(receipt, store.transactions(), store.receipts(), store.workspace())


# --- Bank statements --------------------------------------------------------

@app.post("/bank-statements")
async def import_statement(file: UploadFile = File(...), bank: str | None = Form(None), store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> dict:
    content = await read_upload(file)
    try:
        account, transactions = await run_in_threadpool(sandbox.run, parse_statement, file.filename or "statement", content, store.workspace().banks, bank or None)
    except (StatementParseError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    added = store.add_transactions(transactions)
    autofilled = _run_autofill(store) if added else 0
    _prefill_entries(store)
    ai = None
    if added and ai_enabled():
        try:
            meter.charge("ai")
            ai = await run_in_threadpool(_run_ai, store)
        except HTTPException as exc:  # over the AI quota: the import itself still counts
            if exc.status_code != 429:
                raise
            ai = {"judged": 0, "error": exc.detail}
    return {
        "autofilled": autofilled,
        "ai": ai,
        "bank": account.code,
        "bank_name": account.name,
        "file_name": file.filename,
        "rows": len(transactions),
        "added": added,
        "duplicates": len(transactions) - added,
        "first": min(t.posted_at for t in transactions)[:10],
        "last": max(t.posted_at for t in transactions)[:10],
    }


@app.get("/bank-transactions", response_model=list[BankTransaction])
def list_transactions(store: Store = Depends(get_store)) -> list[BankTransaction]:
    return store.transactions()


@app.delete("/bank-transactions")
def delete_transactions(bank: str, store: Store = Depends(get_store)) -> dict:
    linked = [r.id for r in store.receipts() if r.payment_transaction_id.startswith(f"T-{bank}-")]
    if linked:
        raise HTTPException(status_code=409, detail="영수증에 연결된 거래가 있어 삭제할 수 없습니다: " + ", ".join(linked))
    return {"deleted": store.delete_transactions(bank)}


# --- Ledger -----------------------------------------------------------------

def _download(content: bytes, media_type: str, filename: str) -> Response:
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})


def _books(store: Store) -> tuple[list, list[Account], Workspace]:
    workspace = store.workspace()
    lines = books.lines(store.receipts(), store.transactions(), store.entries(), workspace, store.sales_invoices(), store.vat_returns())
    return lines, books.accounts(workspace, lines), workspace


def _book_account(accounts: list[Account], code: str) -> Account:
    account = next((a for a in accounts if a.code == code), None)
    if account is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return account


@app.get("/ledger/{account_code}", response_model=LedgerView)
def get_ledger(account_code: str, store: Store = Depends(get_store)) -> LedgerView:
    lines, accounts, _ = _books(store)
    return ledger.ledger_view(_book_account(accounts, account_code), lines)


@app.get("/ledger/{account_code}/export.csv")
def export_ledger_csv(account_code: str, store: Store = Depends(get_store)) -> Response:
    lines, accounts, workspace = _books(store)
    view = ledger.ledger_view(_book_account(accounts, account_code), lines)
    return _download(exports.ledger_csv(view, workspace), "text/csv; charset=utf-8", f"계정별원장_{workspace.year}_{account_code}.csv")


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/exports/workbook.xlsx")
def export_workbook(store: Store = Depends(get_store)) -> Response:
    lines, accounts, workspace = _books(store)
    return _download(exports.workbook(lines, accounts, workspace), XLSX, f"원장_{workspace.company or '회사'}_{workspace.year}.xlsx")


@app.get("/reports/statements")
def get_statements(store: Store = Depends(get_store)) -> dict:
    """재무상태표·손익계산서·결손금처리계산서 for the screen (same numbers as the 결산보고서 file)."""
    lines, accounts, workspace = _books(store)

    def as_dict(st) -> dict:
        return {"title": st.title, "items": [vars(i) for i in st.items], "checks": st.checks}

    return {
        "balance_sheet": as_dict(statements.balance_sheet(accounts, lines, workspace.year)),
        "income_statement": as_dict(statements.income_statement(accounts, lines, workspace.year)),
        "deficit": as_dict(statements.deficit_statement(accounts, lines, workspace.year)),
    }


@app.get("/exports/closing-report.xlsx")
def export_closing_report(store: Store = Depends(get_store)) -> Response:
    lines, accounts, workspace = _books(store)
    return _download(exports.closing_report_xlsx(lines, accounts, workspace), XLSX, f"{workspace.year}결산보고서_{workspace.company or '회사'}.xlsx")


@app.get("/exports/income-statement.xlsx")
def export_income_statement(store: Store = Depends(get_store)) -> Response:
    lines, accounts, workspace = _books(store)
    return _download(exports.income_statement_xlsx(lines, accounts, workspace), XLSX, f"{workspace.year}손익계산서_{workspace.company or '회사'}.xlsx")


@app.get("/exports/ledger-book.xlsx")
def export_ledger_book(store: Store = Depends(get_store)) -> Response:
    lines, accounts, workspace = _books(store)
    return _download(exports.ledger_book(lines, accounts, workspace), XLSX, f"{workspace.year}계정별원장_{workspace.company or '회사'}.xlsx")


# --- Bank entries (통장 거래 분개) --------------------------------------------

def _key_name(provider: str) -> str:
    return f"{provider}_api_key"  # "anthropic_api_key" is the name companies already saved


def _llm(store: Store) -> llm.Client:
    """The LLM this company chose: its own key first, then the server's .env key for that provider."""
    workspace = store.workspace()
    provider = workspace.ai_provider if workspace.ai_provider in llm.PROVIDERS else "anthropic"
    key = store.secret(_key_name(provider)) or llm.env_key(provider)
    model = workspace.ai_model or (llm.DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else "")
    return llm.Client(provider=provider, api_key=key, model=model)


class AiSettings(BaseModel):
    provider: str = "anthropic"
    api_key: str = Field(default="", max_length=300)
    model: str = Field(default="", max_length=120)


def _ai_status(store: Store, models: list[str] | None = None) -> dict:
    client = _llm(store)
    providers = []
    for provider, info in llm.PROVIDERS.items():
        key = store.secret(_key_name(provider))
        source = "company" if key else "server" if llm.env_key(provider) else "none"
        providers.append({"id": provider, "label": info["label"], "console": info["console"], "key_hint": info["key_hint"],
                          "source": source, "masked": llm.mask(key) if key else ""})
    active = next(p for p in providers if p["id"] == client.provider)
    return {
        "provider": client.provider, "model": client.model, "label": client.label,
        "configured": active["source"] != "none" and bool(client.model),
        "source": active["source"], "masked": active["masked"], "providers": providers,
        "models": models if models is not None else [],
    }


@app.get("/settings/ai")
def get_ai_settings(store: Store = Depends(get_store)) -> dict:
    return _ai_status(store)


@app.get("/settings/ai/models")
async def get_ai_models(provider: str, store: Store = Depends(get_store)) -> dict:
    if provider not in llm.PROVIDERS:
        raise HTTPException(status_code=404, detail="지원하지 않는 AI 서비스입니다.")
    key = store.secret(_key_name(provider)) or llm.env_key(provider)
    if not key:
        return {"models": []}
    try:
        return {"models": await run_in_threadpool(llm.list_models, provider, key)}
    except llm.LlmError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/settings/ai/models")
async def preview_ai_models(body: AiSettings, store: Store = Depends(get_store)) -> dict:
    """Check a key without saving it and list its models, so the model can be chosen before registering."""
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(status_code=404, detail="지원하지 않는 AI 서비스입니다.")
    key = body.api_key.strip() or store.secret(_key_name(body.provider)) or llm.env_key(body.provider)
    if len(key) < 20:
        raise HTTPException(status_code=422, detail="API 키를 입력하세요.")
    try:
        models = await run_in_threadpool(llm.list_models, body.provider, key)
    except llm.LlmError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"models": models, "default": llm.default_model(body.provider, models)}


@app.put("/settings/ai")
async def put_ai_settings(body: AiSettings, store: Store = Depends(get_store)) -> dict:
    """Choose the AI service and model; a new key is checked against the provider before it is kept.

    The full key is never sent back.
    """
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(status_code=422, detail="지원하지 않는 AI 서비스입니다.")
    new_key = body.api_key.strip()
    if new_key and len(new_key) < 20:
        raise HTTPException(status_code=422, detail="API 키가 너무 짧습니다. 발급받은 키 전체를 붙여 넣으세요.")
    key = new_key or store.secret(_key_name(body.provider)) or llm.env_key(body.provider)
    if not key:
        raise HTTPException(status_code=422, detail=f"{llm.PROVIDERS[body.provider]['label']} API 키를 입력하세요.")
    try:
        models = await run_in_threadpool(llm.list_models, body.provider, key)
    except llm.LlmError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not models:
        raise HTTPException(status_code=422, detail="이 키로 쓸 수 있는 모델이 없습니다.")
    if new_key:
        store.save_secret(_key_name(body.provider), new_key)
    workspace = store.workspace()
    if body.model and body.model not in models:
        raise HTTPException(status_code=422, detail=f"이 키로는 {body.model} 모델을 쓸 수 없습니다.")
    keep = workspace.ai_model if workspace.ai_provider == body.provider and workspace.ai_model in models else ""
    workspace.ai_provider, workspace.ai_model = body.provider, body.model or keep or llm.default_model(body.provider, models)
    store.save_workspace(workspace)
    return _ai_status(store, models)


@app.delete("/settings/ai")
def delete_ai_settings(provider: str = "anthropic", store: Store = Depends(get_store)) -> dict:
    store.save_secret(_key_name(provider), "")
    return _ai_status(store)


def ai_enabled() -> bool:
    return os.environ.get("AI_JUDGE", "on").lower() not in ("off", "0", "false")


def _prefill_entries(store: Store) -> None:
    store.save_entries(bank_entries.prefill(store.transactions(), store.entries(), store.workspace(), store.sales_invoices(), store.vat_returns()))


def _ensure_profile(store: Store) -> str:
    """Read 사업자등록증·등기부등본·주주명부 into the workspace profile once. Returns an error message or ""."""
    workspace = store.workspace()
    if not ai_enabled() or workspace.profile is not None or not set(REQUIRED_DOCUMENTS) <= set(store.documents()):
        return ""
    files = [(label, f.name, f.content_type, f.content) for kind, label in REQUIRED_DOCUMENTS.items() if (f := store.document_file(kind))]
    try:
        profile = ai_judge.read_profile(files, _llm(store))
    except ai_judge.AiJudgeError as exc:
        # Remember the failure so every AI run does not pay for the same unreadable documents again.
        workspace = store.workspace()
        workspace.profile = CompanyProfile(error=str(exc))
        store.save_workspace(workspace)
        return str(exc)
    workspace = store.workspace()
    workspace.profile = profile
    if not workspace.company and profile.company:
        workspace.company = profile.company
    store.save_workspace(workspace)
    return ""


def _run_ai(store: Store, entry_ids: set[str] | None = None, receipt_ids: set[str] | None = None) -> dict:
    """Ask the model for one recommendation per unconfirmed bank row and receipt.

    With no ids, only rows and receipts that have no recommendation yet are sent.
    """
    _ensure_profile(store)
    workspace, transactions, receipts = store.workspace(), store.transactions(), store.receipts()
    entries, invoices, vat_returns = store.entries(), store.sales_invoices(), store.vat_returns()
    changed = {e.tx_id: e for e in bank_entries.prefill(transactions, entries, workspace, invoices, vat_returns)}
    views = bank_entries.views(transactions, entries, receipts, workspace)
    peer_rows = ai_judge.peers(views)
    by_tx = {t.id: t for t in transactions}
    items = []
    for v in views:
        if v.confirmed or v.covered_by or v.skip or v.transfer_to or not v.transaction.posted_at.startswith(workspace.year):
            continue
        if (entry_ids is not None and v.tx_id not in entry_ids) or (entry_ids is None and v.ai):
            continue
        items.append(ai_judge.bank_item(v, peer_rows.get(v.tx_id, [])))
    for r in receipts:
        if r.confirmed or (receipt_ids is not None and r.id not in receipt_ids) or (receipt_ids is None and r.ai):
            continue
        tx = by_tx.get(r.payment_transaction_id)
        items.append(ai_judge.receipt_item(r, f"{tx.bank_name} {tx.description}" if tx else ""))
    error = ""
    client = _llm(store)
    try:
        if not ai_enabled():
            raise ai_judge.AiJudgeError("AI 판단이 꺼져 있습니다 (AI_JUDGE=off).")
        suggestions = ai_judge.recommend(items, workspace, ai_judge.examples(items, views, receipts, workspace), client)
    except ai_judge.AiJudgeError as exc:
        suggestions, error = {}, str(exc)
    for item_id, suggestion in suggestions.items():
        if item_id in entries:
            tx = by_tx[item_id]
            ai_judge.apply_to_entry(entries[item_id], tx.withdrawal or tx.deposit, suggestion)
            changed[item_id] = entries[item_id]
    for r in receipts:
        if r.id in suggestions:
            ai_judge.apply_to_receipt(r, suggestions[r.id], workspace.card_vat)
            store.save_receipt(r)
    # Cancellations follow the account the model just gave the original purchase.
    changed.update({e.tx_id: e for e in bank_entries.prefill(transactions, entries, workspace, invoices, vat_returns)})
    store.save_entries(list(changed.values()))
    result = {"requested": len(items), "judged": len(suggestions), "model": client.label, "error": error}
    if auto_confirm():
        result["auto"] = _auto_process(store)
    return result


AUTO_MIN_CONFIDENCE = 0.5


def _unsure(autofilled: dict[str, str], ai) -> bool:
    """The model chose the account but said itself it was not sure: a person decides."""
    return bool(ai and (ai.review_required or (ai.confidence < AUTO_MIN_CONFIDENCE and autofilled.get("account", "").startswith(ai_judge.AI_PREFIX))))


def auto_confirm() -> bool:
    return os.environ.get("AUTO_CONFIRM", "on").lower() not in ("off", "0", "false")


AUTO = "자동 처리"


def _auto_fill_receipt(r: ReceiptRecord, transactions: dict[str, BankTransaction]) -> None:
    """Fill what only a reviewer used to provide, so a complete receipt can be posted without one."""
    def fill(field: str, value, reason: str) -> None:
        setattr(r, field, value)
        r.autofilled[field] = f"{AUTO} · {reason}"

    if not r.purpose.strip() and r.merchant:
        fill("purpose", f"{r.merchant} 사용", "사용 목적 미입력 · 상호로 적요 작성")
    if not r.evidence.strip():
        fill("evidence", f"영수증 원본 {r.file_name} · 표시 세액 {r.original_tax:,}원", "원본 영수증을 증빙으로 사용")
    if r.payment == "pending":
        fill("payment", "unpaid", "연결할 통장 출금을 찾지 못해 외상매입금으로 처리")
    if not r.checked:
        fill("checked", True, "원본 추출값과 통장 거래를 시스템이 대조")


def _auto_process(store: Store) -> dict:
    """Post everything complete: receipts first (they take over their bank rows), then the remaining bank rows."""
    dups = autofill.duplicates(store.receipts())
    transactions = {t.id: t for t in store.transactions()}
    receipts_done, receipts_left = 0, {}
    workspace = store.workspace()
    for r in store.receipts():
        if r.confirmed or r.id in dups or not r.account:
            if not r.confirmed:
                receipts_left[r.id] = "중복 영수증" if r.id in dups else "계정 없음"
            continue
        if invoice_review.needs_year_review(r, workspace) or invoice_review.needs_vendor_review(r, workspace):
            receipts_left[r.id] = "OCR 연도 또는 거래처 연결 확인 필요"
            continue
        if invoice_review.invoice(r) and r.payment == "pending" and not r.payment_transaction_id:
            if matching.candidates(r, list(transactions.values()), store.receipts(), workspace):
                receipts_left[r.id] = "출금 후보를 확인해 연결하거나 미지급을 선택하세요."
                continue
        if _unsure(r.autofilled, r.ai):
            receipts_left[r.id] = "정책 근거·예외 검토 필요" if r.ai and r.ai.review_required else "AI 확신 낮음 · 확인 필요"
            continue
        if autofill.guessed(r, "account"):
            receipts_left[r.id] = "비용 계정 추정 · 확인 필요"
            continue
        _auto_fill_receipt(r, transactions)
        store.save_receipt(r)
        gaps = _confirm(store, r)
        if gaps:
            receipts_left[r.id] = ", ".join(gaps)
        else:
            receipts_done += 1
    entries_done, entries_left = 0, {}
    for view in _entry_views(store):
        if view.confirmed or view.covered_by or view.skip or not view.transaction.posted_at.startswith(store.workspace().year):
            continue
        if view.requires_person:
            entries_left[view.tx_id] = "영수증 없는 이체 · 사람 확인 필수"
            continue
        if _unsure(view.autofilled, view.ai):
            entries_left[view.tx_id] = "정책 근거·예외 검토 필요" if view.ai and view.ai.review_required else "AI 확신 낮음 · 확인 필요"
            continue
        gaps = _confirm_entry(store, view)
        if gaps:
            entries_left[view.tx_id] = ", ".join(gaps)
        else:
            entries_done += 1
    return {"receipts": receipts_done, "entries": entries_done, "receipts_left": receipts_left, "entries_left": entries_left}


def _entry_views(store: Store) -> list[EntryView]:
    return bank_entries.views(store.transactions(), store.entries(), store.receipts(), store.workspace())


# --- Earlier-year ledger and sales invoices -------------------------------------

@app.post("/workspace/precedents")
async def upload_precedents(file: UploadFile = File(...), store: Store = Depends(get_store)) -> dict:
    """Learn the company's habits (적요·거래처 → 계정) from an earlier year's 계정별원장."""
    content = await read_upload(file)
    try:
        found = await run_in_threadpool(sandbox.run, precedents.read, file.filename or "ledger.xls", content)
    except (precedents.PrecedentError, sandbox.SandboxError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    workspace = store.workspace()
    workspace.precedents, workspace.precedent_source = found, file.filename or ""
    store.save_workspace(workspace)
    return {"count": len(found), "rows": sum(p.count for p in found), "source": workspace.precedent_source}


@app.delete("/workspace/precedents")
def delete_precedents(store: Store = Depends(get_store)) -> dict:
    workspace = store.workspace()
    workspace.precedents, workspace.precedent_source = [], ""
    store.save_workspace(workspace)
    return {"count": 0}


def _check_invoice(invoice: SalesInvoice) -> SalesInvoice:
    if invoice.supply <= 0 or invoice.tax < 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", invoice.issued) or not invoice.customer.strip():
        raise HTTPException(status_code=422, detail="작성일자·공급받는자·공급가액을 확인하세요.")
    return invoice


def _recheck_vat(store: Store, ids: set[str] | None = None) -> dict:
    """Re-read corporate card slips that showed 0 VAT. Fixes the VAT and re-applies the card-VAT policy."""
    from concurrent.futures import ThreadPoolExecutor

    workspace = store.workspace()
    targets = [r for r in store.receipts() if (ids is None or r.id in ids) and vat_checks.needs_vat_check(r)]
    if not targets or not ai_enabled():
        return {"checked": 0, "fixed": 0, "error": ""}
    client = _llm(store)

    def read(r):
        original = store.receipt_file(r.id)
        if original is None:
            return r, None, "원본 파일 없음"
        try:
            return r, ai_judge.read_receipt_vat(original.name, original.content_type, original.content, client), ""
        except ai_judge.AiJudgeError as exc:
            return r, None, str(exc)

    fixed, error = 0, ""
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r, answer, problem in pool.map(read, targets):
            if answer is None:
                error = problem
                continue
            tax = int(answer.get("tax") or 0)
            if vat_checks.set_vat(r, tax, f"Claude 재판독 · 부가세 {tax:,}원" + (f" · {answer.get('note')}" if not tax and answer.get("note") else "")):
                autofill.apply_card_vat(r, workspace)
                fixed += 1
            store.save_receipt(r)
    return {"checked": len(targets), "fixed": fixed, "error": error}


@app.post("/receipts/vat-check")
async def recheck_vat(store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> dict:
    """Re-read every corporate card slip that showed 0 VAT."""
    meter.charge("ai")
    result = await run_in_threadpool(_recheck_vat, store)
    if result["error"] and not result["fixed"] and result["checked"]:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


def _vat_return(store: Store, item_id: str) -> VatReturn:
    item = next((r for r in store.vat_returns() if r.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="부가세 신고를 찾을 수 없습니다.")
    return item


@app.get("/vat-returns/{item_id}/reconcile")
def reconcile_vat_return(item_id: str, store: Store = Depends(get_store)) -> dict:
    return vat_checks.reconcile(_vat_return(store, item_id), store.receipts())


@app.post("/vat-returns/{item_id}/schedule")
async def upload_vat_schedule(item_id: str, file: UploadFile = File(...), store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> dict:
    """Read aggregate schedules for reconciliation only; never infer individual slip VAT."""
    item = _vat_return(store, item_id)
    filename = file.filename or "schedule.pdf"
    content = await read_upload(file)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")
    table = ""
    if filename.lower().endswith((".xlsx", ".xls", ".csv")):
        try:
            table = await run_in_threadpool(sandbox.run, schedule_table, filename, content)
        except (StatementParseError, sandbox.SandboxError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif not filename.lower().endswith(DOCUMENT_SUFFIXES):
        raise HTTPException(status_code=415, detail="부속서류는 PDF·이미지·엑셀 파일만 올릴 수 있습니다.")
    meter.charge("ai")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        rows = await run_in_threadpool(ai_judge.read_vat_schedule, filename, content_type, content, table, _llm(store))
    except ai_judge.AiJudgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    item = item.model_copy(update={"schedule": [ScheduleRow.model_validate(r) for r in rows], "schedule_file": filename})
    store.save_vat_return(item)
    result = vat_checks.reconcile(item, store.receipts())
    result["fixed"] = []  # Aggregate differences are not evidence of an individual slip's VAT.
    return result


@app.get("/vat-returns", response_model=list[VatReturn])
def list_vat_returns(store: Store = Depends(get_store)) -> list[VatReturn]:
    return store.vat_returns()


@app.get("/vat-returns/booked")
def booked_vat(start: str, end: str, store: Store = Depends(get_store)) -> dict:
    """매출세액·매입세액 as the books show them for a filing period, to prefill the return."""
    lines, _, _ = _books(store)
    body = [x for x in lines if x.voucher != "결산"]
    output, input_ = books.booked_vat(body, start, end)
    return {"output_tax": output, "input_tax": input_}


@app.post("/vat-returns", response_model=VatReturn, status_code=201)
def add_vat_return(item: VatReturn, store: Store = Depends(get_store)) -> VatReturn:
    dates = (item.period_start, item.period_end, item.filed)
    if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates) or item.period_start > item.period_end:
        raise HTTPException(status_code=422, detail="신고 기간과 신고일을 확인하세요.")
    if item.output_tax < 0 or item.input_tax < 0:
        raise HTTPException(status_code=422, detail="매출세액과 매입세액은 0 이상이어야 합니다.")
    used = {r.id for r in store.vat_returns()}
    item = item.model_copy(update={"id": next(f"V-{n:03d}" for n in range(1, 10_000) if f"V-{n:03d}" not in used)})
    store.save_vat_return(item)
    _prefill_entries(store)
    if auto_confirm():
        _auto_process(store)
    return item


@app.delete("/vat-returns/{item_id}", status_code=204)
def delete_vat_return(item_id: str, store: Store = Depends(get_store)) -> Response:
    if not store.delete_vat_return(item_id):
        raise HTTPException(status_code=404, detail="부가세 신고를 찾을 수 없습니다.")
    return Response(status_code=204)


@app.get("/sales-invoices", response_model=list[SalesInvoice])
def list_sales_invoices(store: Store = Depends(get_store)) -> list[SalesInvoice]:
    return store.sales_invoices()


def _next_invoice_id(store: Store) -> str:
    used = {i.id for i in store.sales_invoices()}
    return next(f"S-{n:03d}" for n in range(1, 10_000) if f"S-{n:03d}" not in used)


@app.post("/sales-invoices", response_model=SalesInvoice, status_code=201)
async def upload_sales_invoice(file: UploadFile = File(...), store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> SalesInvoice:
    """Read a 매출 세금계산서 (PDF or image) and book 외상매출금 / 매출 + 부가세예수금. The customer's deposit then collects it."""
    filename = file.filename or "invoice.pdf"
    if not filename.lower().endswith(DOCUMENT_SUFFIXES):
        raise HTTPException(status_code=415, detail="세금계산서는 PDF 또는 이미지 파일만 올릴 수 있습니다.")
    content = await read_upload(file)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")
    meter.charge("ai")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        data = await run_in_threadpool(ai_judge.read_sales_invoice, filename, content_type, content, store.workspace().company, _llm(store))
    except ai_judge.AiJudgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    invoice = _check_invoice(SalesInvoice(id=_next_invoice_id(store), file_name=filename, **data))
    store.save_sales_invoice(invoice)
    _prefill_entries(store)
    if auto_confirm():
        await run_in_threadpool(_auto_process, store)
    return invoice


@app.post("/sales-invoices/manual", response_model=SalesInvoice, status_code=201)
def add_sales_invoice(invoice: SalesInvoice, store: Store = Depends(get_store)) -> SalesInvoice:
    invoice = _check_invoice(invoice.model_copy(update={"id": _next_invoice_id(store)}))
    store.save_sales_invoice(invoice)
    _prefill_entries(store)
    if auto_confirm():
        _auto_process(store)
    return invoice


@app.delete("/sales-invoices/{invoice_id}", status_code=204)
def delete_sales_invoice(invoice_id: str, store: Store = Depends(get_store)) -> Response:
    if not store.delete_sales_invoice(invoice_id):
        raise HTTPException(status_code=404, detail="세금계산서를 찾을 수 없습니다.")
    return Response(status_code=204)


def _entry_view(store: Store, tx_id: str) -> EntryView:
    view = next((v for v in _entry_views(store) if v.tx_id == tx_id), None)
    if view is None:
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다.")
    return view


def _get_entry(store: Store, tx_id: str) -> BankEntry:
    if not any(t.id == tx_id for t in store.transactions()):
        raise HTTPException(status_code=404, detail="거래를 찾을 수 없습니다.")
    return store.entries().get(tx_id) or BankEntry(tx_id=tx_id)


@app.get("/entries", response_model=list[EntryView])
def list_entries(store: Store = Depends(get_store)) -> list[EntryView]:
    _prefill_entries(store)
    return _entry_views(store)


@app.patch("/entries/{tx_id}", response_model=EntryView)
def update_entry(tx_id: str, review: EntryReview, store: Store = Depends(get_store)) -> EntryView:
    entry = _get_entry(store, tx_id)
    if entry.confirmed:
        raise HTTPException(status_code=409, detail="확정된 전표입니다. 검토로 되돌린 뒤 수정하세요.")
    changes = review.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("transfer_to") and not any(t.id == changes["transfer_to"] for t in store.transactions()):
        raise HTTPException(status_code=422, detail="이체 상대 거래를 찾을 수 없습니다.")
    updated = entry.model_copy(update=changes)
    # What a person typed is no longer a recommendation.
    updated.autofilled = {k: v for k, v in entry.autofilled.items() if k not in changes}
    store.save_entries([updated])
    return _entry_view(store, tx_id)


def _confirm_entry(store: Store, view: EntryView) -> list[str]:
    if view.covered_by:
        return [f"{view.covered_by}에서 분개합니다"]
    if view.missing:
        return view.missing
    if not view.balanced:
        return ["차변·대변 일치"]
    entry = BankEntry.model_validate(view.model_dump(include=set(BankEntry.model_fields)))
    entry.confirmed = True
    entry.confirmed_at = datetime.now().isoformat(timespec="seconds")
    store.save_entries([entry])
    return []


@app.post("/entries/{tx_id}/confirm", response_model=EntryView)
def confirm_entry(tx_id: str, store: Store = Depends(get_store)) -> EntryView:
    gaps = _confirm_entry(store, _entry_view(store, tx_id))
    if gaps:
        raise HTTPException(status_code=409, detail="확인할 항목이 남아 있습니다: " + ", ".join(gaps))
    return _entry_view(store, tx_id)


@app.post("/entries/{tx_id}/reopen", response_model=EntryView)
def reopen_entry(tx_id: str, store: Store = Depends(get_store)) -> EntryView:
    entry = _get_entry(store, tx_id)
    entry.confirmed, entry.confirmed_at = False, None
    store.save_entries([entry])
    return _entry_view(store, tx_id)


@app.post("/entries/bulk-confirm")
def bulk_confirm_entries(body: BulkIds, store: Store = Depends(get_store)) -> dict:
    views = {v.tx_id: v for v in _entry_views(store)}
    confirmed, skipped = [], {}
    for tx_id in body.ids:
        view = views.get(tx_id)
        if view is None or view.confirmed:
            continue
        gaps = _confirm_entry(store, view)
        if gaps:
            skipped[tx_id] = gaps
        else:
            confirmed.append(tx_id)
    return {"confirmed": confirmed, "skipped": skipped}


@app.post("/auto/process")
async def auto_process(store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> dict:
    """Everything at once: rules, the model's judgment for whatever is not judged yet, then posting."""
    meter.charge("ai")
    result = await run_in_threadpool(_run_ai, store)
    if not auto_confirm():
        result["auto"] = await run_in_threadpool(_auto_process, store)
    return result


@app.post("/ai/judge")
async def judge(body: BulkIds, store: Store = Depends(get_store), meter: Meter = Depends(get_meter)) -> dict:
    """Get the model's recommendation for the given bank rows / receipts, or for everything not yet judged."""
    meter.charge("ai")
    if body.ids:
        ids = set(body.ids)
        result = await run_in_threadpool(_run_ai, store, {i for i in ids if i.startswith("T-")}, {i for i in ids if not i.startswith("T-")})
    else:
        result = await run_in_threadpool(_run_ai, store)
    if result["error"] and not result["judged"] and not (result.get("auto") or {}).get("entries"):
        raise HTTPException(status_code=502, detail=result["error"])
    return result
