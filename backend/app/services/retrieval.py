"""Small, company-local evidence graph. No external index or generated identity edges.

Traverse explicit vendor IDs, unique registered aliases, and confirmed receipt/payment
links. Rank related bookings by purpose, document kind, amount and date. Only the
current Store's data is supplied; rebuilding per request also drops reopened sources.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import re

from app.models import EntryView, ReceiptRecord, RecommendationEvidence, Workspace
from app.services.ledger import normalize_name

VERSION = "relations-v1"
MAX_EVIDENCE = 8


def name_key(value: str) -> str:
    return normalize_name(re.sub(r"^취소\s*", "", re.sub(r"^\d{4}\s+", "", value.strip())))


def business_key(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 10 else ""


def tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-zA-Z가-힣0-9]+", value.lower()) if len(word) > 1}


def parsed_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@dataclass
class Case:
    evidence: RecommendationEvidence
    names: set[str]
    vendors: set[str]
    business: str = ""
    direction: str = "출금"
    document_type: str = ""
    payment_id: str = ""


def retrieve(items: list[dict], entries: list[EntryView], receipts: list[ReceiptRecord],
             workspace: Workspace) -> dict[str, list[RecommendationEvidence]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    businesses: dict[str, set[str]] = defaultdict(set)
    vendor_business = {}
    for vendor in workspace.vendors:
        if not vendor.code:
            continue
        vendor_business[vendor.code] = business_key(vendor.business)
        for name in [vendor.name, vendor.alias, *vendor.aliases, *vendor.payment_aliases]:
            if name_key(name):
                aliases[name_key(name)].add(vendor.code)
        if business_key(vendor.business):
            businesses[business_key(vendor.business)].add(vendor.code)

    def identities(code: str, business: str, names: set[str]) -> set[str]:
        # Conflicting identifiers are never turned into identity edges.
        if code in vendor_business and not (business and vendor_business[code] and business != vendor_business[code]):
            return {code}
        if business and len(businesses[business]) == 1:
            return set(businesses[business])
        found = set().union(*(aliases[n] for n in names)) if names else set()
        if len(found) == 1:
            v = next(iter(found))
            if not business or not vendor_business[v] or business == vendor_business[v]:
                return found
        return set()

    def known_business(explicit: str, vendors: set[str]) -> str:
        return explicit or (vendor_business.get(next(iter(vendors)), "") if len(vendors) == 1 else "")

    account_names = {a.code: a.name for a in workspace.accounts}
    excluded = {b.account for b in workspace.banks} | {"25300", "40000"}
    cases: list[Case] = []
    by_tx = {v.tx_id: v for v in entries}
    used_receipts: set[tuple] = set()
    for r in receipts:
        if not r.confirmed or not r.account or r.account in excluded or r.account not in account_names:
            continue
        ext = r.extraction
        business = business_key(ext.business_number or "") if ext else ""
        duplicate_key = (ext.document_type, business, ext.approval_number, r.date, r.total) if ext and ext.approval_number else (r.id,)
        if duplicate_key in used_receipts:
            continue
        used_receipts.add(duplicate_key)
        names = {name_key(r.merchant)} - {""}
        vendors = identities(r.vendor_code, business, names)
        business = known_business(business, vendors)
        path = [f"영수증 {r.id}"]
        # A confirmed receipt's explicit payment link is evidence of the bank label.
        linked = by_tx.get(r.payment_transaction_id)
        if linked:
            names.add(name_key(linked.transaction.description))
            names.discard("")
            path.insert(0, f"연결 은행 거래 {linked.tx_id}")
        purpose = " · ".join(filter(None, [r.purpose, *([i.description for i in ext.line_items][:8] if ext else [])]))
        cases.append(Case(RecommendationEvidence(
            source_type="receipt", source_id=r.id, description=r.merchant, account=r.account,
            account_name=account_names[r.account], date=r.date, amount=r.total, purpose=purpose,
            reason="", path=path,
        ), names, vendors, business, document_type=ext.document_type if ext else "", payment_id=r.payment_transaction_id))

    for v in entries:
        if not v.confirmed or not v.account or v.skip or v.transfer_to or v.covered_by or v.account in excluded or v.account not in account_names:
            continue
        tx = v.transaction
        if re.match(r"^(?:\d{4}\s+)?취소", tx.description.strip()) or tx.withdrawal < 0 or tx.deposit < 0:
            continue
        names = {name_key(tx.description), name_key(v.vendor)} - {""}
        vendors = identities(v.vendor_code, "", names)
        cases.append(Case(RecommendationEvidence(
            source_type="bank", source_id=v.tx_id, description=tx.description, account=v.account,
            account_name=account_names[v.account], date=tx.posted_at[:10], amount=tx.withdrawal or tx.deposit,
            purpose=v.memo, reason="", path=[f"은행 거래 {v.tx_id}"],
        ), names, vendors, known_business("", vendors), direction="출금" if tx.withdrawal else "입금"))

    for p in workspace.precedents:
        if p.account not in account_names or p.account in excluded:
            continue
        names = {name_key(p.text)} - {""}
        vendors = identities("", "", names)
        source_id = "P-" + sha256(f"{workspace.precedent_source}\0{p.text}\0{p.account}".encode()).hexdigest()[:16]
        cases.append(Case(RecommendationEvidence(
            source_type="precedent", source_id=source_id, description=p.text, account=p.account,
            account_name=account_names[p.account], purpose=p.text, reason="",
            path=[f"이전 원장 {workspace.precedent_source or '출처 미기록'}"], sources=p.sources[:5],
        ), names, vendors, known_business("", vendors), direction=""))

    # Adjacency indexes avoid scanning every historical booking for every new row.
    index: dict[tuple[str, str], set[int]] = defaultdict(set)
    case_tokens = [tokens(c.evidence.purpose) for c in cases]
    case_days = [parsed_day(c.evidence.date) for c in cases]
    for i, case in enumerate(cases):
        for kind, values in (("vendor", case.vendors), ("name", case.names),
                             ("business", {case.business} - {""}), ("token", case_tokens[i])):
            for value in values:
                index[(kind, value)].add(i)

    result = {}
    for item in items:
        names = {name_key(item.get("merchant") or item.get("description") or "")} - {""}
        business = business_key(item.get("business_number") or "")
        vendors = identities(item.get("vendor_code") or "", business, names)
        business = known_business(business, vendors)
        purpose = " ".join([item.get("purpose") or "", *(item.get("items") or [])])
        query_tokens = tokens(purpose or item.get("description") or "")
        target_day = parsed_day(item.get("date") or "")
        candidates: set[int] = set()
        for kind, values in (("vendor", vendors), ("name", names), ("business", {business} - {""}), ("token", query_tokens)):
            for value in values:
                candidates.update(index.get((kind, value), ()))
        ranked = []
        for i in candidates:
            case = cases[i]
            e = case.evidence
            if e.source_id == item["id"] or (case.payment_id and case.payment_id == item["id"]):
                continue  # Do not use the same underlying transaction as its own precedent.
            if item.get("payment_transaction_id") and item["payment_transaction_id"] in {e.source_id, case.payment_id}:
                continue
            if case.direction and case.direction != item.get("direction", "출금"):
                continue
            if business and case.business and business != case.business:
                continue
            source_day = case_days[i]
            if target_day and source_day and source_day > target_day:
                continue
            same_vendor = bool(vendors & case.vendors)
            same_business = bool(business and business == case.business)
            # Duplicate registered names and known different vendors must not join.
            same_name = bool(names & case.names) and not (vendors and case.vendors and not same_vendor)
            same_name = same_name and not any(len(aliases[n]) > 1 for n in names & case.names)
            overlap = query_tokens & case_tokens[i]
            similarity = len(overlap) / max(1, len(query_tokens | case_tokens[i]))
            if not (same_vendor or same_business or same_name or (len(overlap) >= 2 and similarity >= 0.5)):
                continue
            reasons = []
            score = 0.0
            if same_business:
                score += 60
                reasons.append("사업자번호 일치")
            if same_vendor:
                score += 50
                reasons.append("등록 거래처·별칭 연결")
            elif same_name:
                score += 35
                reasons.append("거래처 표시명 일치")
            if overlap:
                score += 40 * similarity
                reasons.append("적요·품목 유사")
            if item.get("document_type") and case.document_type == item["document_type"]:
                score += 5
                reasons.append("증빙 종류 일치")
            amount = item.get("amount") or 0
            if amount > 0 and e.amount and e.amount > 0:
                ratio = min(amount, e.amount) / max(amount, e.amount)
                if ratio >= 0.8:
                    score += 10 * ratio
                    reasons.append("금액 유사")
            if target_day and source_day:
                score += 5 / (1 + (target_day - source_day).days / 30)
            reason = " · ".join(reasons)
            if e.source_type == "precedent":
                reason += " · 이전 원장 사례(입출금 방향 미기록)"
            selected = e.model_copy(update={"reason": reason, "path": [*reasons[:1], *e.path, f"계정 {e.account}"]})
            ranked.append((score, e.date, e.source_id, selected))
        ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
        result[item["id"]] = [row[3] for row in ranked[:MAX_EVIDENCE]]
    return result
