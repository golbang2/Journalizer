"""Propose 회사 기준 (company, year, bank accounts, opening balance, vendors) from bank statements.

Nothing here is saved: the reviewer sees the proposal, unticks what is wrong, and saves.
"""
from collections import Counter, defaultdict
import re

from app.models import Workspace
from app.services.autofill import AUTO_PREFIX, find_vendor
from app.services.bank_statements import StatementInfo
from app.services.ledger import CASH_ACCOUNT, normalize_name

# Rows that are the bank's own activity, not a trading partner.
NOT_A_VENDOR = re.compile(r"이자|수수료|카드결제|결제대금|예금신규|예금결산|OTP|인증서|반환|타행환|자동이체")

# Merchant keywords -> default expense account. Only a starting point; the reviewer can change it.
ACCOUNT_KEYWORDS = [
    (r"철도|코레일|고속버스|시외버스|버스운송|택시|카카오T|주유|하이패스|항공|공항", "81200", "교통·운송"),
    (r"법원|등기|우체국|인지|구청|세무서", "81700", "관공서 수수료·인지대"),
    (r"GOOGLE|도메인|DOMAIN|AWS|AMAZON WEB|CHATGPT|OPENAI|SUBSCRIPTION|구독|특허|법무|세무|회계", "83100", "서비스·구독·전문가 수수료"),
    (r"문구|아트박스|다이소|오피스|프린팅|인쇄|복사", "83000", "사무용품·인쇄"),
    (r"커피|카페|COFFEE|스타벅스|투썸|엔제리너스|이디야|빽다방|메가|컴포즈|버블티|디저트|크리스피|도넛|뚜레쥬르|파리바게|베이커리|"
     r"버거|맥도날드|맘스터치|롯데리아|피자|국밥|규동|막국수|설렁탕|순대|돈까스|토스트|한솥|도시락|공차|더벤티|KOFFEE|크로플|"
     r"식당|김밥|분식|치킨|마트|씨유|CU|GS25|세븐일레븐|이마트24|편의점", "81100", "식음료·편의점"),
]


# Transfer memos the company typed ("임대료", "주민세") look like names but are not trading partners.
MEMO_LIKE = re.compile(r"세$|임대료|월세|관리비|연구비|급여|상여|보험료|비\(|비$")
MIN_COUNT, MIN_AMOUNT = 2, 50_000


def clean_counterparty(description: str) -> str:
    """'2462 스타벅스 코리아' -> '스타벅스 코리아' (check-card prefix); '취소 X' -> 'X'."""
    name = re.sub(r"^\d{4}\s+", "", description.strip())
    return re.sub(r"^취소\s+", "", name).strip()


def guess_account(name: str) -> tuple[str, str]:
    for pattern, account, reason in ACCOUNT_KEYWORDS:
        if re.search(pattern, name, re.IGNORECASE):
            return account, f"상호에 ‘{reason}’ 관련 단어"
    return "", ""


def company_name(holders: list[str]) -> str:
    """Prefer the full legal form ('주식회사 X') and drop an English name in parentheses."""
    cleaned = [re.sub(r"\([A-Za-z0-9 .,&-]+\)\s*$", "", h).strip() for h in holders if h.strip()]
    if not cleaned:
        return ""
    full = [h for h in cleaned if "주식회사" in h]
    return (full or cleaned)[0]


def propose(statements: list[tuple[str, StatementInfo]], workspace: Workspace) -> dict:
    transactions = [t for _, info in statements for t in info.transactions]
    years = Counter(t.posted_at[:4] for t in transactions)
    year = years.most_common(1)[0][0] if years else workspace.year
    company = company_name([info.holder for _, info in statements])
    company_key = normalize_name(company)

    banks, used_codes = [], {b.code for b in workspace.banks}
    for file_name, info in statements:
        number = re.sub(r"\D", "", info.account_number)
        existing = next((b for b in workspace.banks if number and re.sub(r"\D", "", b.number) == number), None) \
            or next((b for b in workspace.banks if info.bank_name and b.name == info.bank_name and not b.number), None)
        if existing:
            code = existing.code
        else:
            code = next(f"{n}" for n in range(99000, 99999) if str(n) not in used_codes)
            used_codes.add(code)
        in_year = [t for t in info.transactions if t.posted_at.startswith(year)]
        first = min((t.posted_at for t in in_year), default="")[:10]
        before_year = [t for t in info.transactions if t.posted_at[:4] < year]
        # Opening = balance after the last row of the previous year, else just before the first row.
        opening = max(before_year, key=lambda t: t.posted_at).balance if before_year else info.opening
        banks.append({
            "file_name": file_name, "code": code, "existing": bool(existing), "name": info.bank_name or "은행",
            "number": info.account_number, "product": info.product, "account": existing.account if existing else CASH_ACCOUNT,
            "opening": opening, "first": first, "last": max((t.posted_at for t in in_year), default="")[:10],
            "rows": len(info.transactions),
            "opening_note": "통장 개설 또는 조회 시작 직전 잔액" if not before_year else f"{year}년 첫 거래 직전 잔액",
        })

    groups: dict[str, dict] = {}
    for t in transactions:
        name = clean_counterparty(t.description)
        key = normalize_name(name)
        if len(key) < 2 or NOT_A_VENDOR.search(name) or (company_key and company_key in key):
            continue
        g = groups.setdefault(key, {"names": Counter(), "count": 0, "withdrawal": 0, "deposit": 0})
        g["names"][name] += 1
        g["count"] += 1
        g["withdrawal"] += t.withdrawal
        g["deposit"] += t.deposit

    # Statements truncate names; fold "ITCH.IO - GAME S" into "ITCH.IO - GAME STORE".
    for key in sorted(groups, key=len):
        longer = next((k for k in groups if k != key and len(key) >= 8 and k.startswith(key)), None)
        if longer:
            target, source = groups[longer], groups.pop(key)
            target["names"].update(source["names"])
            for field in ("count", "withdrawal", "deposit"):
                target[field] += source[field]

    vendors, used = [], {v.code for v in workspace.vendors}
    number = 1
    for key, g in sorted(groups.items(), key=lambda kv: (-kv[1]["withdrawal"], kv[0])):
        name = max(g["names"], key=lambda n: (g["names"][n], len(n)))
        existing = find_vendor(workspace, name)
        if existing:
            continue
        while f"{AUTO_PREFIX}{number:04d}" in used:
            number += 1
        code = f"{AUTO_PREFIX}{number:04d}"
        used.add(code)
        account, reason = guess_account(name)
        outgoing = g["withdrawal"] > 0
        memo = bool(MEMO_LIKE.search(name))
        frequent = g["count"] >= MIN_COUNT or g["withdrawal"] >= MIN_AMOUNT
        note = "입금만 있음 · 자본금·대여금·환급일 수 있음" if not outgoing else "거래처가 아니라 이체 메모로 보임" if memo else "" if frequent else "1회성 소액 거래"
        vendors.append({
            "code": code, "name": name, "count": g["count"], "withdrawal": g["withdrawal"], "deposit": g["deposit"],
            "account": account if outgoing and not memo else "", "account_reason": reason if outgoing and not memo else "",
            "selected": outgoing and not memo and frequent, "note": note,
        })

    openings: dict[str, int] = defaultdict(int)
    for bank in banks:
        if bank["opening"] is not None:
            openings[bank["account"]] += bank["opening"]
    return {
        "company": company,
        "year": year,
        "year_note": ", ".join(f"{y}년 {n}건" for y, n in years.most_common()),
        "banks": banks,
        "openings": dict(openings),
        "vendors": vendors,
    }
