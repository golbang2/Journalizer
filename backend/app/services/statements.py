"""Year-end statements from the posted lines: 합계잔액시산표, 손익계산서, 재무상태표, 결손금처리계산서.

Accounts are placed by code range, the way Korean accounting programs number them
(101-250 자산, 251-330 부채, 331-399 자본, 401 매출 … 801-900 판매비와관리비, 901 영업외수익 …).
The income statement uses the lines before the year-end closing; the balance sheet uses all of them.
"""
from dataclasses import dataclass, field

from app.models import Account, LedgerLine

CLOSING_VOUCHER = "결산"
INCOME_SUMMARY = "40000"


def _n(code: str) -> int:
    digits = "".join(ch for ch in code[:3] if ch.isdigit())
    return int(digits) if len(digits) == 3 else 0


def section(account: Account) -> str:
    n = _n(account.code)
    ranges = [
        (101, 145, "당좌자산"), (146, 175, "재고자산"), (176, 194, "투자자산"), (195, 230, "유형자산"), (231, 250, "무형·기타비유동자산"),
        (251, 290, "유동부채"), (291, 330, "비유동부채"),
        (331, 340, "자본금"), (341, 350, "자본잉여금"), (351, 380, "이익잉여금(결손금)"), (381, 399, "자본조정"),
        (401, 450, "매출액"), (451, 800, "매출원가"), (801, 900, "판매비와관리비"),
        (901, 950, "영업외수익"), (951, 997, "영업외비용"), (998, 999, "법인세등"),
    ]
    for low, high, name in ranges:
        if low <= n <= high:
            return name
    fallback = {"asset": "당좌자산", "liability": "유동부채", "equity": "자본조정", "revenue": "영업외수익", "expense": "판매비와관리비"}
    return fallback.get(account.kind, "")


def _balances(accounts: list[Account], lines: list[LedgerLine]) -> dict[str, dict]:
    """Per account: opening, debit and credit sums, and the debit-positive balance."""
    out = {a.code: {"account": a, "opening": a.opening if a.normal == "debit" else -a.opening, "debit": 0, "credit": 0} for a in accounts}
    for line in lines:
        if line.account in out:
            out[line.account]["debit"] += line.debit
            out[line.account]["credit"] += line.credit
    for row in out.values():
        row["balance"] = row["opening"] + row["debit"] - row["credit"]
    return out


def before_closing(lines: list[LedgerLine]) -> list[LedgerLine]:
    return [x for x in lines if x.voucher != CLOSING_VOUCHER]


@dataclass
class Item:
    label: str
    amount: int
    level: int = 1  # 0 = section heading/total, 1 = account, 2 = sub-total
    code: str = ""


@dataclass
class Statement:
    title: str
    items: list[Item] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def trial_balance(accounts: list[Account], lines: list[LedgerLine]) -> list[dict]:
    """합계잔액시산표 (before closing): 차변 잔액·합계, 계정, 대변 합계·잔액."""
    rows = []
    for code, b in sorted(_balances(accounts, before_closing(lines)).items()):
        a = b["account"]
        if code == INCOME_SUMMARY or not (b["debit"] or b["credit"] or b["opening"]):
            continue
        debit_total = b["debit"] + max(b["opening"], 0)
        credit_total = b["credit"] + max(-b["opening"], 0)
        rows.append({"code": code, "name": a.name, "debit_balance": max(b["balance"], 0), "debit_total": debit_total,
                     "credit_total": credit_total, "credit_balance": max(-b["balance"], 0)})
    return rows


def income_statement(accounts: list[Account], lines: list[LedgerLine], year: str) -> Statement:
    balances = _balances(accounts, before_closing(lines))
    groups: dict[str, list[Item]] = {}
    for code, b in sorted(balances.items()):
        name = section(b["account"])
        if name not in ("매출액", "매출원가", "판매비와관리비", "영업외수익", "영업외비용", "법인세등") or not b["balance"]:
            continue
        revenue = name in ("매출액", "영업외수익")
        groups.setdefault(name, []).append(Item(b["account"].name, -b["balance"] if revenue else b["balance"], 1, code))

    def total(name: str) -> int:
        return sum(i.amount for i in groups.get(name, []))

    sales, cost, sga = total("매출액"), total("매출원가"), total("판매비와관리비")
    other_in, other_out, tax = total("영업외수익"), total("영업외비용"), total("법인세등")
    gross = sales - cost
    operating = gross - sga
    before_tax = operating + other_in - other_out
    net = before_tax - tax
    st = Statement(f"손익계산서 (제1기 {year}년 1월 1일부터 {year}년 12월 31일까지)")
    for label, name, amount in (("Ⅰ. 매출액", "매출액", sales), ("Ⅱ. 매출원가", "매출원가", cost)):
        st.items.append(Item(label, amount, 0))
        st.items += groups.get(name, [])
    st.items.append(Item("Ⅲ. 매출총이익" if gross >= 0 else "Ⅲ. 매출총손실", gross, 0))
    st.items.append(Item("Ⅳ. 판매비와관리비", sga, 0))
    st.items += groups.get("판매비와관리비", [])
    st.items.append(Item("Ⅴ. 영업이익" if operating >= 0 else "Ⅴ. 영업손실", operating, 0))
    st.items.append(Item("Ⅵ. 영업외수익", other_in, 0))
    st.items += groups.get("영업외수익", [])
    st.items.append(Item("Ⅶ. 영업외비용", other_out, 0))
    st.items += groups.get("영업외비용", [])
    st.items.append(Item("Ⅷ. 법인세차감전순이익" if before_tax >= 0 else "Ⅷ. 법인세차감전순손실", before_tax, 0))
    st.items.append(Item("Ⅸ. 법인세등", tax, 0))
    st.items += groups.get("법인세등", [])
    st.items.append(Item("Ⅹ. 당기순이익" if net >= 0 else "Ⅹ. 당기순손실", net, 0))
    return st


def net_income(accounts: list[Account], lines: list[LedgerLine], year: str) -> int:
    return income_statement(accounts, lines, year).items[-1].amount


def balance_sheet(accounts: list[Account], lines: list[LedgerLine], year: str) -> Statement:
    """재무상태표 at year end. Before the closing is posted, the year's result is shown as 당기순이익(손실)."""
    balances = _balances(accounts, lines)
    closed = any(x.voucher == CLOSING_VOUCHER for x in lines)
    groups: dict[str, list[Item]] = {}
    for code, b in sorted(balances.items()):
        name = section(b["account"])
        if not b["balance"] or name in ("", "매출액", "매출원가", "판매비와관리비", "영업외수익", "영업외비용", "법인세등"):
            continue
        sign = 1 if name in ("당좌자산", "재고자산", "투자자산", "유형자산", "무형·기타비유동자산") else -1
        groups.setdefault(name, []).append(Item(b["account"].name, sign * b["balance"], 1, code))
    if not closed:
        result = net_income(accounts, lines, year)
        if result:
            groups.setdefault("이익잉여금(결손금)", []).append(Item("당기순이익" if result > 0 else "당기순손실", result, 1))

    def total(*names: str) -> int:
        return sum(i.amount for n in names for i in groups.get(n, []))

    st = Statement(f"재무상태표 (제1기 {year}년 12월 31일 현재)")
    current = ("당좌자산", "재고자산")
    fixed = ("투자자산", "유형자산", "무형·기타비유동자산")
    assets = total(*current, *fixed)
    st.items.append(Item("자산", assets, 0))
    for label, names in (("Ⅰ. 유동자산", current), ("Ⅱ. 비유동자산", fixed)):
        st.items.append(Item(label, total(*names), 2))
        for n in names:
            st.items += groups.get(n, [])
    liabilities = total("유동부채", "비유동부채")
    st.items.append(Item("부채", liabilities, 0))
    for label, name in (("Ⅰ. 유동부채", "유동부채"), ("Ⅱ. 비유동부채", "비유동부채")):
        st.items.append(Item(label, total(name), 2))
        st.items += groups.get(name, [])
    equity_names = ("자본금", "자본잉여금", "이익잉여금(결손금)", "자본조정")
    equity = total(*equity_names)
    st.items.append(Item("자본", equity, 0))
    for i, name in enumerate(equity_names):
        st.items.append(Item(f"{'ⅠⅡⅢⅣ'[i]}. {name}", total(name), 2))
        st.items += groups.get(name, [])
    st.items.append(Item("부채와 자본 총계", liabilities + equity, 0))
    if assets != liabilities + equity:
        st.checks.append(f"자산 {assets:,}원과 부채·자본 {liabilities + equity:,}원이 맞지 않습니다.")
    return st


def deficit_statement(accounts: list[Account], lines: list[LedgerLine], year: str) -> Statement:
    """결손금처리계산서, or 이익잉여금처분계산서 when the year made a profit (no appropriation decided yet)."""
    net = net_income(accounts, lines, year)
    carried = sum(a.opening * (1 if a.normal == "credit" else -1) for a in accounts if 351 <= _n(a.code) <= 380)
    total = carried + net
    if total >= 0:
        st = Statement(f"이익잉여금처분계산서 (제1기 {year}년 1월 1일부터 {year}년 12월 31일까지)")
        st.items += [Item("Ⅰ. 미처분이익잉여금", total, 0), Item("전기이월미처분이익잉여금", carried), Item("당기순이익", net),
                     Item("Ⅱ. 이익잉여금처분액", 0, 0), Item("Ⅲ. 차기이월미처분이익잉여금", total, 0)]
    else:
        st = Statement(f"결손금처리계산서 (제1기 {year}년 1월 1일부터 {year}년 12월 31일까지)")
        st.items += [Item("Ⅰ. 미처리결손금", -total, 0), Item("전기이월미처리결손금", -carried), Item("당기순손실", -net),
                     Item("Ⅱ. 결손금처리액", 0, 0), Item("Ⅲ. 차기이월미처리결손금", -total, 0)]
    return st
