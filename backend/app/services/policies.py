"""Curated local policy graph: transaction -> condition -> policy -> account -> cases.

Relations are explicit and reviewer-approved. No inferred legal applicability or
embedding service is required. Original text is always included with the relation.
"""
from datetime import date

from app.models import RecommendationEvidence, Workspace

MAX_POLICIES = 4


def retrieve(item: dict, workspace: Workspace, cases: list[RecommendationEvidence]) -> list[RecommendationEvidence]:
    try:
        day = date.fromisoformat(item.get("date", "")[:10])
    except ValueError:
        return []  # Never guess the policy version without a transaction date.
    text = " ".join(str(item.get(k, "")) for k in ("description", "merchant", "purpose"))
    text += " " + " ".join(item.get("items", []))
    text = text.lower()
    accounts = {a.code: a.name for a in workspace.accounts}
    excluded = {b.account for b in workspace.banks} | {"25300", "40000"}
    found = []
    for policy in workspace.policies:
        if policy.status != "approved" or day < policy.effective_from or (policy.effective_to and day > policy.effective_to):
            continue
        for index, rule in enumerate(policy.rules):
            if rule.account not in accounts or rule.account in excluded:
                continue
            matches = [term for term in rule.terms if term in text]
            if not matches or (rule.direction and rule.direction != item.get("direction")):
                continue
            if rule.document_type and rule.document_type != item.get("document_type"):
                continue
            amount = item.get("amount")
            if amount is None or amount < rule.minimum or (rule.maximum is not None and amount > rule.maximum):
                continue
            exceptions = [term for term in rule.exceptions if term in text]
            path = [f"거래 {item['id']}", f"조건 {', '.join(matches)}", f"정책 {policy.title} ({policy.version})",
                    f"계정 {rule.account} {accounts[rule.account]}"]
            related = [c.source_id for c in cases if c.account == rule.account]
            if related:
                path.append("관련 확정 사례 " + ", ".join(related))
            evidence = RecommendationEvidence(
                source_type="policy", source_id=policy.id, description=policy.title,
                account=rule.account, account_name=accounts[rule.account], date=policy.effective_from.isoformat(),
                purpose=f"조건 {index + 1}: {', '.join(rule.terms)} · {rule.direction or '입출금 모두'} · "
                        f"{rule.document_type or '증빙 제한 없음'} · {rule.minimum}~{rule.maximum if rule.maximum is not None else '상한 없음'}원",
                reason="예외 조건 일치: 담당자 검토 필요" if exceptions else "승인된 정책의 검색 조건 일치 — 원문과 예외 확인 필요",
                path=path, policy_version=policy.version, policy_kind=policy.kind,
                source=policy.source, locator=policy.locator, policy_text=policy.text,
                exceptions=rule.exceptions,
            )
            found.append((len(matches) + bool(rule.direction) + bool(rule.document_type), evidence))
    found.sort(key=lambda pair: (-pair[0], pair[1].source_id, pair[1].purpose))
    selected = [e for _, e in found[:MAX_POLICIES]]
    if len(found) > MAX_POLICIES:
        selected[0].context_truncated = True
        selected[0].reason += f" · 추가 정책 조건 {len(found) - MAX_POLICIES}건 생략: 담당자 검토 필요"
    return selected


def needs_review(evidence: list[RecommendationEvidence], account: str) -> bool:
    policies = [e for e in evidence if e.source_type == "policy"]
    if not policies:
        return False
    # Tax policy is contextual evidence; it must never silently override VAT checks.
    return (any(e.context_truncated or e.exceptions or e.policy_kind == "tax" or e.account != account for e in policies)
            or any(e.source_type != "policy" and e.account != account for e in evidence))
