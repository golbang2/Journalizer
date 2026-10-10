"""Ouricard sales slips: labeled cells, raw VAT blanks, and audit evidence."""
from datetime import datetime
from decimal import Decimal
import re

from app.models import ReceiptExtraction
from app.services.bank_pdf_parser import ReceiptParseError, _anchor, _evidence, _lines, _render, _words


def parse_woori_pdf(annotation):
    compact = re.sub(r'\s+', '', annotation.get('text', ''))
    if '우리카드매출전표' not in compact:
        raise ReceiptParseError('This is not a recognized Ouricard sales slip.')
    words = _words(annotation)
    lines = _lines(words)
    labels = {key: _anchor(lines, text) for key, text in {
        'card_type':'카드종류', 'card_number':'카드번호', 'purchased_at':'거래일시',
        'payment_method':'결제방법', 'total':'거래금액', 'deposit':'자원순환보증금',
        'tax':'부가세', 'fee':'봉사료', 'approval_number':'승인번호',
        'merchant':'가맹점명', 'address':'가맹점주소', 'merchant_number':'가맹점번호',
        'business_number':'사업자번호', 'telephone':'연락처',
    }.items()}
    date_label = labels['purchased_at']
    date_words = [w for w in words if w.left > date_label.right and abs(w.y-date_label.y) < date_label.height*.7 and re.fullmatch(r'\d{4}/\d{2}/\d{2}', w.text)]
    if len(date_words) != 1:
        raise ReceiptParseError('Missing or ambiguous Ouricard transaction date.')
    data_left = date_words[0].left-date_label.height*.25
    card_label = labels['card_number']
    card_words = [w for w in words if w.left >= data_left and abs(w.y-card_label.y) < card_label.height*.7 and re.fullmatch(r'[\d*\-]+', w.text)]
    if not card_words:
        raise ReceiptParseError('Card number cell cannot be located.')
    data_right = max(w.right for w in card_words)+card_label.height*.5
    ordered = sorted(labels.values(), key=lambda a:a.y)
    raw, evidence = {}, {}
    for key, anchor in labels.items():
        position = ordered.index(anchor)
        top = (ordered[position-1].y+anchor.y)/2 if position else anchor.y-anchor.height
        bottom = (anchor.y+ordered[position+1].y)/2 if position+1<len(ordered) else anchor.y+anchor.height
        selected = [w for w in words if data_left <= (w.left+w.right)/2 < data_right and top < w.y < bottom]
        raw[key] = ' '.join(_render(line) for line in _lines(selected))
        if selected:
            evidence[key] = _evidence(selected)
    try:
        stamp = re.search(r'\d{4}/\d{2}/\d{2}\s*\d{2}:\d{2}:\d{2}', raw['purchased_at'])
        if not stamp:
            raise ReceiptParseError('Ouricard transaction timestamp is missing.')
        purchased_at = datetime.strptime(stamp.group(), '%Y/%m/%d %H:%M:%S')
    except ValueError as exc:
        raise ReceiptParseError('Invalid Ouricard transaction timestamp.') from exc
    amounts={}
    for key in ('total','tax','deposit','fee'):
        value=re.sub(r'[\s원]', '', raw[key])
        if not value and key != 'total':
            amounts[key]=None
            continue
        if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)',value):
            raise ReceiptParseError(f'Missing or ambiguous Ouricard amount: {key}')
        amounts[key]=Decimal(value.replace(',',''))
        if evidence[key]['confidence'] < .8:
            raise ReceiptParseError(f'Low OCR confidence for Ouricard {key}.')
    if any(amounts[k] not in (None,0) for k in ('deposit','fee')):
        raise ReceiptParseError('Deposit/service fee requires a separate journal treatment.')
    approval=re.sub(r'\s+','',raw['approval_number'])
    if not re.fullmatch(r'\d{6,8}',approval):
        raise ReceiptParseError('Ouricard approval number is missing or ambiguous.')
    if not raw['merchant'] or not raw['payment_method']:
        raise ReceiptParseError('Ouricard merchant/payment method is missing.')
    tax=amounts['tax'] if amounts['tax'] is not None else Decimal('0')
    if tax>amounts['total']:
        raise ReceiptParseError('VAT exceeds total.')
    business=re.sub(r'\s+','',raw['business_number'])
    if business and not re.fullmatch(r'\d{3}-\d{2}-\d{5}',business):
        raise ReceiptParseError('Business number is malformed.')
    warnings=['계정과목을 확정할 구매 목적·품목 정보가 없어 기타비용 초안으로 처리했습니다.',
              '취소 여부와 중복 비용은 사용내역의 승인번호로 확인해 주세요.']
    if amounts['tax'] is None:
        warnings.append('부가세가 공란입니다. 현재 초안은 세액 0원으로 처리했으며 원문에 0원이 표시된 경우와 구분했습니다.')
    if not business:
        warnings.append('사업자번호가 없습니다. 해외 거래 여부와 원화 결제금액을 확인해 주세요. 금액은 KRW로 처리했습니다.')
    if '체크' in raw['payment_method']:
        warnings.append('원본 결제방법은 체크계좌입니다. 현재 분개 엔진의 대변 법인카드 미지급금 대신 실제 결제계좌 사용 여부를 확인해 주세요.')
    if tax>0:
        warnings.append('전표에 표시된 부가세를 분리했습니다. 부가세 공제 및 비용·자산 구분을 확인해 주세요.')
    confidence=min(evidence[k]['confidence'] for k in ('purchased_at','total','merchant','approval_number'))
    receipt=ReceiptExtraction(merchant=raw['merchant'], purchased_at=purchased_at.date(), currency='KRW',subtotal=amounts['total']-tax,tax=tax,total=amounts['total'],payment_method=raw['payment_method'],category_hint='other',confidence=confidence,line_items=[],warnings=warnings,approval_number=approval,business_number=business or None,extraction_provider='google_vision',field_evidence=evidence)
    metadata={'raw_fields':raw,'transaction_timestamp':purchased_at.isoformat(),'raw_tax':str(amounts['tax']) if amounts['tax'] is not None else None,'vat_blank':amounts['tax'] is None,'subtotal_is_derived':True,'card_last_four':re.sub(r'\D','',raw['card_number'])[-4:]}
    return receipt,metadata
