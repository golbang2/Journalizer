from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_store
from app.models import AccountingPolicy, PolicyInput
from app.services.store import Store

router = APIRouter(prefix="/policies", tags=["policies"])


def checked_save(store: Store, policy: AccountingPolicy, expected: AccountingPolicy | None = None) -> AccountingPolicy:
    workspace = store.workspace()
    usable = {a.code for a in workspace.accounts} - {b.account for b in workspace.banks} - {"25300", "40000"}
    if policy.status != "retired" and any(rule.account not in usable for rule in policy.rules):
        raise HTTPException(422, "정책 계정은 회사 계정과목표의 추천 가능한 계정이어야 합니다.")
    try:
        return store.save_policy(policy, expected)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("", response_model=list[AccountingPolicy])
def list_policies(store: Store = Depends(get_store)):
    return store.policies()


@router.post("", response_model=AccountingPolicy, status_code=201)
def create_policy(body: PolicyInput, store: Store = Depends(get_store)):
    policy = AccountingPolicy(**body.model_dump(), id="POL-" + uuid4().hex,
                              created_at=datetime.now(timezone.utc).isoformat())
    return checked_save(store, policy)


@router.post("/{policy_id}/{action}", response_model=AccountingPolicy)
def transition_policy(policy_id: str, action: str, store: Store = Depends(get_store)):
    policy = next((p for p in store.policies() if p.id == policy_id), None)
    if policy is None:
        raise HTTPException(404, "정책을 찾을 수 없습니다.")
    expected = policy.model_copy(deep=True)
    if action == "approve" and policy.status == "draft":
        policy.status = "approved"
        policy.approved_at = datetime.now(timezone.utc).isoformat()
    elif action == "retire" and policy.status in ("draft", "approved"):
        policy.status = "retired"
    else:
        raise HTTPException(409, "허용되지 않는 정책 상태 변경입니다.")
    return checked_save(store, policy, expected)


@router.put("/{policy_id}", response_model=AccountingPolicy)
def update_policy(policy_id: str, body: PolicyInput, store: Store = Depends(get_store)):
    current = next((p for p in store.policies() if p.id == policy_id), None)
    if current is None:
        raise HTTPException(404, "정책을 찾을 수 없습니다.")
    if current.status != "draft":
        raise HTTPException(409, "승인·철회된 정책은 수정할 수 없습니다. 새 버전을 등록하세요.")
    return checked_save(store, AccountingPolicy(**body.model_dump(), id=current.id, created_at=current.created_at), current)
