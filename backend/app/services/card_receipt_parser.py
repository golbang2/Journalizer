"""Conservative parsing of labeled Korean card approval receipts from Vision words."""
from datetime import date
from decimal import Decimal
import re

from app.models import ReceiptExtraction
from app.services.bank_pdf_parser import ReceiptParseError, _anchor, _evidence, _lines, _render, _words


def _label(lines, *names, required=True):
    for name in names:
        found = _anchor(lines, name, optional=True)
        if found:
            return found
    if required:
        raise ReceiptParseError(f'Missing card receipt label: {names[0]}')
    return None


def _right_cell(words, anchor):
    selected = [w for w in words if w.left >= anchor.right and abs(w.y-anchor.y) < anchor.height*.7]
    return [w for w in selected if not re.fullmatch(r'[:：|]+', w.text)]


def _amount(words, anchor):
    selected = _right_cell(words, anchor)
    raw = re.sub(r'[\s원₩]', '', _render(selected))
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)', raw):
        raise ReceiptParseError(f'Missing or ambiguous card receipt amount: {anchor.text}')
    evidence = _evidence(selected)
    if evidence['confidence'] < .8:
        raise ReceiptParseError(f'Low OCR confidence for card receipt amount: {anchor.text}')
    return Decimal(raw.replace(',', '')), evidence


def parse_card_receipt(annotation):
    words = _words(annotation)
    lines = _lines(words)
    evidence = {}
    date_label = _label(lines, '거래일시', '거래일자', '거래일', '일시')
    date_words = _right_cell(words, date_label)
    matches = re.findall(r'(?<!\d)(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})(?!\d)', _render(date_words))
    if len(matches) != 1:
        raise ReceiptParseError('Missing or ambiguous card receipt transaction date.')
    try:
        purchased_at = date(*map(int, matches[0]))
    except ValueError as exc:
        raise ReceiptParseError('Invalid card receipt transaction date.') from exc
    evidence['purchased_at'] = _evidence(date_words)

    total_label = _label(lines, '합계금액', '결제금액', '승인금액', '거래금액', '합계', '금액')
    total, evidence['total'] = _amount(words, total_label)
    tax_label = _label(lines, '부가세', '부가가치세', required=False)
    warnings = ['구매 목적·품목을 확정하지 않아 기타비용 초안으로 처리했습니다.',
                '취소 여부와 중복 비용은 사용내역의 승인번호로 확인해 주세요.']
    if tax_label:
        tax, evidence['tax'] = _amount(words, tax_label)
    else:
        tax = Decimal('0')
        warnings.append('부가세가 별도로 표시되지 않아 초안 세액을 0원으로 처리했습니다. 세액을 확인해 주세요.')
    if tax > total:
        raise ReceiptParseError('Card receipt VAT exceeds total.')
    subtotal = total-tax
    subtotal_label = _label(lines, '공급가액', required=False)
    if subtotal_label:
        subtotal, evidence['subtotal'] = _amount(words, subtotal_label)
        if subtotal+tax != total:
            raise ReceiptParseError('Card receipt monetary fields do not reconcile.')
    for name in ('봉사료', '자원순환보증금'):
        label = _label(lines, name, required=False)
        if label and _amount(words, label)[0] != 0:
            raise ReceiptParseError(f'Card receipt {name} requires separate journal treatment.')

    approval_label = _label(lines, '승인번호')
    approval_words = _right_cell(words, approval_label)
    approval = re.sub(r'\s+', '', _render(approval_words))
    if not re.fullmatch(r'\d{6,12}', approval):
        raise ReceiptParseError('Missing or ambiguous card receipt approval number.')
    evidence['approval_number'] = _evidence(approval_words)

    merchant_label = _label(lines, '가맹점명', '상호명', '상호', required=False)
    if merchant_label:
        merchant_words = _right_cell(words, merchant_label)
    else:
        # Some terminal receipts put the merchant immediately above the address.
        address = _label(lines, '주소')
        candidates = [line for line in lines if 0 < address.top-max(w.bottom for w in line) < address.height*3
                      and re.search(r'[가-힣]{2}', _render(line))]
        if not candidates:
            raise ReceiptParseError('Card receipt merchant header is missing.')
        merchant_words = max(candidates, key=lambda line:sum(w.y for w in line)/len(line))
        if re.search(r'카드|승인|영수증|거래|사업자', _render(merchant_words)):
            raise ReceiptParseError('Card receipt merchant header is ambiguous.')
        warnings.append('주소 위의 상호를 추출했습니다. 원본 상호명을 확인해 주세요.')
    merchant = _render(merchant_words)
    if not merchant:
        raise ReceiptParseError('Card receipt merchant is missing.')
    evidence['merchant'] = _evidence(merchant_words)

    business_label = _label(lines, '사업자번호', required=False)
    business = None
    if business_label:
        business_words = _right_cell(words, business_label)
        business = re.sub(r'\s+', '', _render(business_words))
        if not re.fullmatch(r'\d{3}-\d{2}-\d{5}', business):
            raise ReceiptParseError('Card receipt business number is malformed.')
        evidence['business_number'] = _evidence(business_words)
    else:
        warnings.append('사업자번호가 없습니다. 거래처와 원화 결제금액을 확인해 주세요.')
    payment_label = _label(lines, '결제방법', required=False)
    if payment_label:
        payment = _render(_right_cell(words, payment_label))
    else:
        compact = re.sub(r'\s+', '', annotation.get('text', ''))
        payment = '신용카드' if '신용승인' in compact or '신용카드' in compact else '카드'
    if not payment:
        raise ReceiptParseError('Card receipt payment method is missing.')
    return ReceiptExtraction(
        merchant=merchant, purchased_at=purchased_at, currency='KRW', subtotal=subtotal,
        tax=tax, total=total, payment_method=payment, category_hint='other',
        confidence=min(evidence[k]['confidence'] for k in ('purchased_at', 'total', 'merchant', 'approval_number')),
        line_items=[], warnings=warnings, approval_number=approval, business_number=business,
        extraction_provider='google_vision', field_evidence=evidence,
    )
