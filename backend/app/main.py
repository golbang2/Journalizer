from datetime import datetime
from decimal import Decimal
import mimetypes
import os
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
    BankTransaction,
    BulkIds,
    BulkUpdate,
    JournalEntryDraft,
    LedgerView,
    PaymentCandidate,
    ReceiptExtraction,
    ReceiptRecord,
    ReceiptReview,
    ReceiptView,
    Vendor,
    Workspace,
)
from app.services import autofill, exports, ledger, matching, workspace_setup
from app.services.bank_pdf_parser import ReceiptParseError
from app.services.bank_statements import StatementParseError, parse_statement, read_statement
from app.services.journalizer import build_journal_entry
from app.services.receipts import extract_receipt
from app import auth
from app.auth import User, current_user, get_db, get_store
from app.services.legacy import import_legacy
from app.services.store import Store, company_for_user
from app.services.vision_receipts import VisionResponseError

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
RECEIPT_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp")

app = FastAPI(title="Journalizer API", version="0.3.0")

app.add_middleware(
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


def _me(user: User, engine: Engine, imported: dict | None = None) -> dict:
    store = Store(engine, company_for_user(engine, user.id))
    return {**user.model_dump(), "company": store.workspace().company, **store.counts(), "imported": imported}


@app.get("/auth/config")
def auth_config() -> dict:
    return {"google": auth.google_config() is not None}


@app.post("/auth/signup")
def signup(body: Signup, response: Response, engine: Engine = Depends(get_db)) -> dict:
    email = auth.normalize_email(body.email)
    first = auth.user_count(engine) == 0
    user = auth.create_user(engine, email, body.name.strip() or email.split("@")[0], password=body.password)
    imported = import_legacy(Store(engine, company_for_user(engine, user.id))) if first else None
    auth.start_session(engine, response, user.id)
    return _me(user, engine, imported)


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
        first = auth.user_count(engine) == 0
        user = auth.google_user(engine, auth.google_profile(code))
        if first:
            import_legacy(Store(engine, company_for_user(engine, user.id)))
    except HTTPException as exc:
        return RedirectResponse("/?" + urlencode({"login_error": exc.detail}), status_code=303)
    except Exception:  # network or Google errors
        return RedirectResponse("/?" + urlencode({"login_error": "구글 로그인에 실패했습니다. 잠시 뒤 다시 시도하세요."}), status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.STATE_COOKIE, path="/")
    auth.start_session(engine, response, user.id)
    return response


@app.post("/receipts/extract", response_model=ReceiptExtraction)
async def extract(file: UploadFile = File(...)) -> ReceiptExtraction:
    content = await file.read()
    try:
        return await run_in_threadpool(extract_receipt, filename=file.filename or "receipt", content=content)
    except ReceiptParseError as exc:
        raise HTTPException(status_code=422, detail=f"전표 확인이 필요합니다: {exc}") from exc
    except VisionResponseError as exc:
        raise HTTPException(status_code=502, detail="Google Vision에서 정상적인 OCR 결과를 받지 못했습니다.") from exc


@app.post("/journal-entries/draft", response_model=JournalEntryDraft)
def draft_journal_entry(receipt: ReceiptExtraction) -> JournalEntryDraft:
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
    if not workspace.year.isdigit() or len(workspace.year) != 4:
        raise HTTPException(status_code=422, detail="회계연도는 네 자리 연도여야 합니다.")
    workspace.vendors = [v for v in workspace.vendors if v.code.strip() or v.name.strip()]
    return store.save_workspace(workspace)


@app.post("/workspace/from-statements")
async def workspace_from_statements(files: list[UploadFile] = File(...), store: Store = Depends(get_store)) -> dict:
    """Read bank statements and propose 회사 기준 values. Saves nothing."""
    statements = []
    for file in files:
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{file.filename}: 파일은 15MB 이하만 올릴 수 있습니다.")
        try:
            statements.append((file.filename or "statement", read_statement(file.filename or "statement", content)))
        except StatementParseError as exc:
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
async def upload_receipt(file: UploadFile = File(...), store: Store = Depends(get_store)) -> ReceiptView:
    """Store the original, OCR it, and prefill from rules. OCR failure still creates a receipt for manual entry."""
    filename = file.filename or "receipt"
    if not filename.lower().endswith(RECEIPT_SUFFIXES):
        raise HTTPException(status_code=415, detail="영수증은 PDF 또는 이미지 파일만 올릴 수 있습니다.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일입니다.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일은 15MB 이하만 올릴 수 있습니다.")
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
    return _view(store, _get_receipt(store, receipt_id))


@app.get("/receipts/{receipt_id}", response_model=ReceiptView)
def get_receipt(receipt_id: str, store: Store = Depends(get_store)) -> ReceiptView:
    return _view(store, _get_receipt(store, receipt_id))


def _vendor_defaults(r: ReceiptRecord, vendor: Vendor) -> bool:
    changed = False
    if vendor.account and not r.account:
        r.account, r.autofilled["account"], changed = vendor.account, f"{vendor.name}의 기본 계정", True
    if vendor.purpose and not r.purpose.strip():
        r.purpose, r.autofilled["purpose"], changed = vendor.purpose, f"{vendor.name}의 기본 적요", True
    return changed


def _apply_review(receipt: ReceiptRecord, changes: dict, store: Store) -> ReceiptRecord:
    tx_id = changes.get("payment_transaction_id")
    if tx_id:
        tx = next((t for t in store.transactions() if t.id == tx_id), None)
        if tx is None:
            raise HTTPException(status_code=422, detail="연결할 출금 거래를 찾을 수 없습니다.")
        changes.update(
            payment="paid",
            payment_date=tx.posted_at[:10],
            bank=tx.bank,
            payment_link=f"{tx.bank_name} {tx.posted_at[:16].replace('T', ' ')} · {tx.description} · {tx.withdrawal:,}원",
        )
    elif "payment_link" in changes or changes.get("payment") in ("pending", "unpaid"):
        # A typed reference or a non-bank payment replaces any statement link.
        changes["payment_transaction_id"] = ""
    updated = receipt.model_copy(update=changes)
    # What the reviewer touched is no longer "automatic".
    updated.autofilled = {k: v for k, v in receipt.autofilled.items() if k not in changes}
    if "vendor_code" in changes:
        vendor = next((v for v in store.workspace().vendors if v.code == updated.vendor_code), None)
        if vendor:
            _vendor_defaults(updated, vendor)
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
async def reextract_receipt(receipt_id: str, store: Store = Depends(get_store)) -> ReceiptView:
    """Re-run OCR on the stored original, e.g. after fixing OCR credentials.

    Only the extracted facts are refreshed; reviewer input (purpose, account, payment...) is kept.
    """
    receipt = _get_receipt(store, receipt_id)
    if receipt.confirmed:
        raise HTTPException(status_code=409, detail="확정된 전표입니다. 검토로 되돌린 뒤 다시 추출하세요.")
    original = store.receipt_file(receipt_id)
    if original is None:
        raise HTTPException(status_code=404, detail="원본 파일이 없습니다.")
    extraction, error = await _run_ocr(receipt.file_name, original.content)
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
            if not other.confirmed and other.vendor_code == vendor.code and _vendor_defaults(other, vendor):
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
async def import_statement(file: UploadFile = File(...), bank: str | None = Form(None), store: Store = Depends(get_store)) -> dict:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일은 15MB 이하만 올릴 수 있습니다.")
    try:
        account, transactions = parse_statement(file.filename or "statement", content, store.workspace().banks, bank or None)
    except StatementParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    added = store.add_transactions(transactions)
    autofilled = _run_autofill(store) if added else 0
    return {
        "autofilled": autofilled,
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

def _account(store: Store, code: str) -> Account:
    account = next((a for a in store.workspace().accounts if a.code == code), None)
    if account is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    return account


def _download(content: bytes, media_type: str, filename: str) -> Response:
    disposition = "attachment; filename*=UTF-8''" + quote(filename)
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})


@app.get("/ledger/{account_code}", response_model=LedgerView)
def get_ledger(account_code: str, store: Store = Depends(get_store)) -> LedgerView:
    return ledger.ledger(_account(store, account_code), store.receipts(), store.workspace())


@app.get("/ledger/{account_code}/export.csv")
def export_ledger_csv(account_code: str, store: Store = Depends(get_store)) -> Response:
    workspace = store.workspace()
    view = ledger.ledger(_account(store, account_code), store.receipts(), workspace)
    return _download(exports.ledger_csv(view, workspace), "text/csv; charset=utf-8", f"계정별원장_{workspace.year}_{account_code}.csv")


@app.get("/exports/workbook.xlsx")
def export_workbook(store: Store = Depends(get_store)) -> Response:
    workspace = store.workspace()
    return _download(
        exports.workbook(store.receipts(), workspace),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        f"원장_{workspace.company or '회사'}_{workspace.year}.xlsx",
    )
