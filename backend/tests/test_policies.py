"""Policy lifecycle, dated graph retrieval, and recommendation/approval boundary."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.auth import create_user, get_db
from app.models import AccountingPolicy, AiSuggestion, PolicyInput, ReceiptRecord
from app.services import ai_judge, ledger, llm, policies
from app.services.store import Store, company_for_user
from db_helpers import fresh_engine


def body(**changes):
    return {
        "title": "구독 비용 정책", "series": "subscription", "version": "1", "kind": "accounting",
        "source": "회사 지침", "locator": "3페이지 제2조", "text": "업무용 소프트웨어 구독료는 해당 비용 계정으로 처리한다.",
        "effective_from": "2026-01-01", "effective_to": "2026-12-31",
        "rules": [{"terms": ["소프트웨어", "subscription"], "account": "81100", "direction": "출금"}],
        **changes,
    }


def policy(**changes):
    return AccountingPolicy(**{**body(), "id": "POL-1", "status": "approved", "created_at": "2026-01-01", **changes})


def item(**changes):
    return {"id": "R-new", "date": "2026-06-01", "direction": "출금", "amount": 20000,
            "purpose": "소프트웨어 구독", "merchant": "도구회사", "document_type": "receipt", **changes}


def workspace(*items):
    ws = ledger.default_workspace()
    ws.year = "2026"
    ws.policies = list(items)
    return ws


@pytest.fixture
def engine(tmp_path):
    engine = fresh_engine(str(tmp_path))
    main.app.dependency_overrides[get_db] = lambda: engine
    yield engine
    main.app.dependency_overrides.clear()
    engine.dispose()


def test_authenticated_lifecycle_is_company_scoped_and_workspace_cannot_approve(engine):
    with TestClient(main.app) as own, TestClient(main.app) as other:
        assert own.get("/policies").status_code == 401
        for client, email in [(own, "own@example.com"), (other, "other@example.com")]:
            assert client.post("/auth/signup", json={"email": email, "password": "password123"}).status_code == 200
        created = own.post("/policies", json=body())
        assert created.status_code == 201, created.text
        p = created.json()
        assert p["status"] == "draft"
        assert other.get("/policies").json() == []
        assert other.post(f"/policies/{p['id']}/approve").status_code == 404
        ws = own.get("/workspace").json()
        ws["policies"][0]["status"] = "approved"
        own.put("/workspace", json=ws)
        assert own.get("/policies").json()[0]["status"] == "draft"
        assert own.put(f"/policies/{p['id']}", json=body(text="수정한 원문")).status_code == 200
        assert own.post(f"/policies/{p['id']}/approve").status_code == 200
        assert own.put(f"/policies/{p['id']}", json=body()).status_code == 409
        assert own.post(f"/policies/{p['id']}/approve").status_code == 409
        ws = own.get("/workspace").json()
        ws["accounts"] = [a for a in ws["accounts"] if a["code"] != "81100"]
        assert own.put("/workspace", json=ws).status_code == 422
        assert own.post(f"/policies/{p['id']}/retire").status_code == 200
        assert own.post(f"/policies/{p['id']}/approve").status_code == 409
        assert own.get("/policies").json()[0]["text"] == "수정한 원문"


def test_version_overlap_and_invalid_accounts_rejected(engine):
    with TestClient(main.app) as client:
        client.post("/auth/signup", json={"email": "owner@example.com", "password": "password123"})
        p = client.post("/policies", json=body()).json()
        client.post(f"/policies/{p['id']}/approve")
        assert client.post("/policies", json=body()).status_code == 409
        newer = client.post("/policies", json=body(version="2")).json()
        assert client.post(f"/policies/{newer['id']}/approve").status_code == 409
        next_year = client.post("/policies", json=body(version="3", effective_from="2027-01-01", effective_to=None)).json()
        assert client.post(f"/policies/{next_year['id']}/approve").status_code == 200
        assert client.post("/policies", json=body(series="invalid", rules=[{"terms": ["구독"], "account": "bogus"}])).status_code == 422


@pytest.mark.parametrize("changes", [
    {"status": "draft"}, {"status": "retired"}, {"effective_from": "2026-07-01"},
    {"effective_to": "2026-05-31"},
])
def test_only_approved_effective_policy_is_retrieved(changes):
    assert not policies.retrieve(item(), workspace(policy(**changes)), [])


@pytest.mark.parametrize("changes", [{"date": ""}, {"date": "bad"}, {"purpose": "직원 간식"}, {"direction": "입금"}])
def test_missing_date_and_unmatched_transaction_never_guess_policy(changes):
    assert not policies.retrieve(item(**changes), workspace(policy()), [])


def test_conditions_exceptions_and_graph_paths():
    p = policy(rules=[{"terms": ["SUBSCRIPTION"], "account": "81100", "minimum": 10000,
                       "maximum": 30000, "document_type": "receipt", "exceptions": ["개인"]}])
    target = item(purpose="subscription 개인 사용")
    found = policies.retrieve(target, workspace(p), [])
    assert found[0].source_type == "policy"
    assert found[0].policy_text == p.text
    assert found[0].locator == "3페이지 제2조"
    assert "개인" in found[0].exceptions
    assert "예외 조건 일치" in found[0].reason
    assert found[0].path[0] == "거래 R-new"
    assert policies.needs_review(found, "81100")
    assert not policies.retrieve({**target, "amount": 9999}, workspace(p), [])
    assert not policies.retrieve({**target, "document_type": "tax_invoice"}, workspace(p), [])


def test_context_is_bounded_and_overflow_requires_review():
    ws = workspace(*(policy(id=f"POL-{i}", series=str(i)) for i in range(10)))
    found = policies.retrieve(item(), ws, [])
    assert len(found) == policies.MAX_POLICIES
    assert found[0].context_truncated
    assert policies.needs_review(found, "81100")


def test_server_policy_snapshot_reaches_llm_and_conflicts_block_auto_confirmation(engine):
    user = create_user(engine, "owner@example.com", "owner", password="password123")
    store = Store(engine, company_for_user(engine, user.id))
    store.save_workspace(workspace())
    store.save_policy(policy())
    r = ReceiptRecord(id="R-new", file_name="a.pdf", uploaded_at="2026-01-01", date="2026-06-01",
                      total=20000, merchant="도구회사", purpose="소프트웨어 구독")
    store.save_receipt(r)
    seen = []

    def answer(client, system, parts, schema, **kwargs):
        rows = json.loads(parts[0].split("분류할 항목\n", 1)[1])
        seen.extend(rows)
        return {"results": [{"id": row["id"], "account": "81300", "confidence": 1} for row in rows]}

    with patch.object(main, "_ensure_profile"), patch.object(main, "_llm", return_value=llm.Client("anthropic", "fake", "fake")), \
         patch.object(main, "ai_enabled", return_value=True), patch.object(main, "auto_confirm", return_value=True), \
         patch("app.services.llm.complete", side_effect=answer):
        main._run_ai(store, receipt_ids={"R-new"})
    saved = store.receipt("R-new")
    assert saved.ai.review_required
    assert not saved.confirmed
    assert main._unsure({}, saved.ai)
    assert seen[0]["evidence"][0]["policy_text"] == policy().text
    p = store.policies()[0]
    p.status = "retired"
    store.save_policy(p)
    assert not ai_judge.examples([item()], [], [], store.workspace())["R-new"]
    assert store.receipt("R-new").ai.evidence[0].policy_text == p.text


def test_policy_to_account_to_confirmed_case_path_and_tax_review():
    r = ReceiptRecord(id="R-past", file_name="a.pdf", uploaded_at="2026-01-01", date="2026-01-01",
                      total=20000, merchant="도구회사", purpose="소프트웨어 구독", account="81100", confirmed=True)
    evidence = ai_judge.examples([item()], [], [r], workspace(policy()))["R-new"]
    assert any("관련 확정 사례 R-past" in e.path for e in evidence if e.source_type == "policy")
    assert not policies.needs_review(evidence, "81100")
    assert policies.needs_review(policies.retrieve(item(), workspace(policy(kind="tax")), []), "81100")
    assert not main._unsure({}, AiSuggestion(confidence=1))


@pytest.mark.parametrize("changes", [
    {"effective_to": "2025-01-01"}, {"text": "  "},
    {"rules": [{"terms": [" "], "account": "81100"}]},
    {"rules": [{"terms": ["구독"], "account": "81100", "minimum": 10, "maximum": 1}]},
])
def test_policy_validation(changes):
    with pytest.raises(ValidationError):
        PolicyInput.model_validate(body(**changes))
