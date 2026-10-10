"""Explicit year review and identities learned from a person's invoice link."""
import re


def key(value):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).upper()


def invoice(r):
    return bool(r.extraction and r.extraction.document_type == "tax_invoice")


def year_review_key(r, workspace):
    return f"{r.extraction.purchased_at.year}:{workspace.year}:{r.date[:4]}" if r.extraction else ""


def needs_year_review(r, workspace):
    return bool(r.extraction and str(r.extraction.purchased_at.year) != workspace.year
                and r.year_confirmed_for != year_review_key(r, workspace))


def names(vendor):
    return [vendor.name, vendor.alias, *vendor.aliases]


def needs_vendor_review(r, workspace):
    if not invoice(r):
        return False
    vendor = next((v for v in workspace.vendors if v.code == r.vendor_code), None)
    return not vendor or key(vendor.business) != key(r.extraction.business_number or "") or not vendor.business


def representative_matches(r, description):
    if not invoice(r):
        return False
    rep = key(r.extraction.representative)
    # A bank prefix is common on transfers; never match arbitrary substrings.
    bank_name = re.sub(r"^(?:기업|IBK|국민|KB|우리|신한|하나|농협|NH|광주|카카오|토스)(?:은행)?", "", key(description))
    return len(rep) >= 2 and bank_name == rep


def confirm_vendor(r, workspace, tx=None):
    """Called only from a person's PATCH, never by autofill or AI confirmation."""
    if not invoice(r):
        return False
    vendor = next((v for v in workspace.vendors if v.code == r.vendor_code), None)
    if not vendor:
        raise ValueError("연결할 거래처를 먼저 선택하세요.")
    business = r.extraction.business_number or ""
    if vendor.business and key(vendor.business) != key(business):
        raise ValueError("선택한 거래처와 세금계산서의 사업자번호가 다릅니다. 거래처를 확인하세요.")
    if any(v.code != vendor.code and business and key(v.business) == key(business) for v in workspace.vendors):
        raise ValueError("같은 사업자번호의 거래처가 이미 있습니다. 해당 거래처를 선택하세요.")
    if not re.fullmatch(r"\d{3}-\d{2}-\d{5}", business):
        raise ValueError("공급자의 사업자번호를 확인하세요.")
    before = vendor.model_dump()
    vendor.business = business
    if key(r.extraction.merchant) not in {key(n) for n in names(vendor)}:
        if not vendor.alias:
            vendor.alias = r.extraction.merchant
        else:
            vendor.aliases.append(r.extraction.merchant)
    if tx and tx.description not in vendor.payment_aliases:
        vendor.payment_aliases.append(tx.description)
    return vendor.model_dump() != before
