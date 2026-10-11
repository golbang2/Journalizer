"""Authenticated, company-scoped Hometax evidence imports and reconciliation."""
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import get_store
from app.limits import read_upload
from app.services import hometax, sandbox
from app.services.store import Store

router = APIRouter(prefix="/hometax")


@router.post("/imports")
async def upload(kind: Literal["card", "invoice"] = Form(...), file: UploadFile = File(...), store: Store = Depends(get_store)):
    content = await read_upload(file)
    if not content:
        raise HTTPException(422, "빈 파일입니다.")
    workspace = store.workspace()
    try:
        item = await run_in_threadpool(sandbox.run, hometax.parse, file.filename or "", content, kind,
                                      workspace.profile.business_number if workspace.profile else "")
    except (hometax.HometaxError, sandbox.SandboxError) as exc:
        raise HTTPException(422, str(exc)) from exc
    added = store.add_hometax_import(item, content)
    return {"id": item.id, "added": added, "rows": len(item.rows)}


@router.get("/reconcile")
def report(start: date, end: date, store: Store = Depends(get_store)):
    if start > end:
        raise HTTPException(422, "시작일은 종료일보다 늦을 수 없습니다.")
    imports, receipts = store.hometax_imports(), store.receipts()
    all_rows = hometax.reconcile(imports, receipts, store.hometax_links())
    rows = [r for r in all_rows if start.isoformat() <= r["date"] <= end.isoformat()]
    covered = {c["id"] for row in all_rows for c in row["candidates"]}
    kinds = {b.kind for b in imports}
    unmatched = [{"id": r.id, "merchant": r.merchant, "date": r.date, "total": r.total} for r in receipts
                 if r.id not in covered and start.isoformat() <= r.date <= end.isoformat()
                 and ("invoice" if r.extraction and r.extraction.document_type == "tax_invoice" else "card") in kinds]
    return {"rows": rows, "unmatched_receipts": unmatched,
            "imports": [{"id": b.id, "kind": b.kind, "file_name": b.file_name, "uploaded_at": b.uploaded_at,
                         "rows": len(b.rows), "first": min(r.date for r in b.rows), "last": max(r.date for r in b.rows)} for b in imports]}


@router.get("/imports/{item_id}/file")
def original(item_id: str, store: Store = Depends(get_store)):
    file = store.hometax_file(item_id)
    if not file:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    return Response(file.content, media_type=file.content_type,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.name, safe='')}"})


@router.delete("/imports/{item_id}", status_code=204)
def remove(item_id: str, store: Store = Depends(get_store)):
    if not store.delete_hometax_import(item_id):
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    return Response(status_code=204)


class Link(BaseModel):
    receipt_id: str = ""


@router.put("/rows/{row_id}/link", status_code=204)
def link(row_id: str, body: Link, store: Store = Depends(get_store)):
    rows = hometax.reconcile(store.hometax_imports(), store.receipts(), store.hometax_links())
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise HTTPException(404, "자료를 찾을 수 없습니다.")
    if body.receipt_id and body.receipt_id not in {r["id"] for r in row["candidates"]}:
        raise HTTPException(422, "사업자번호·승인번호 또는 날짜·금액이 대응하는 영수증만 연결할 수 있습니다.")
    store.link_hometax(row_id, body.receipt_id)
    return Response(status_code=204)
