"""Parse the Kwangju Bank sales-slip layout using OCR labels and word boxes.

This module never consults filenames, benchmark answers, or transaction exports.
Unknown layouts and ambiguous monetary values raise rather than inventing data.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from app.models import ReceiptExtraction


class ReceiptParseError(ValueError):
    pass


class ReceiptInputError(ReceiptParseError):
    """Invalid receipt file; reject before OCR instead of trying another provider."""


@dataclass(frozen=True)
class Word:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: float
    space_after: bool = False

    @property
    def y(self):
        return (self.top + self.bottom) / 2

    @property
    def height(self):
        return self.bottom - self.top


def _box(raw, width, height):
    vertices = raw.get('normalizedVertices') or raw.get('normalized_vertices')
    if vertices:
        points = [(v.get('x', 0), v.get('y', 0)) for v in vertices]
    else:
        points = [(v.get('x', 0) / width, v.get('y', 0) / height) for v in raw.get('vertices', [])]
    if not points:
        raise ReceiptParseError('OCR word coordinates are missing.')
    return min(x for x, y in points), min(y for x, y in points), max(x for x, y in points), max(y for x, y in points)


def _words(annotation):
    pages = annotation.get('pages', [])
    if len(pages) != 1:
        raise ReceiptParseError('Only single-page bank receipts are supported by the rule parser.')
    page = pages[0]
    result = []
    for block in page.get('blocks', []):
        for paragraph in block.get('paragraphs', []):
            for raw in paragraph.get('words', []):
                symbols = raw.get('symbols', [])
                text = ''.join(s.get('text', '') for s in symbols)
                if not text:
                    continue
                box = _box(raw.get('boundingBox', raw.get('bounding_box', {})), page['width'], page['height'])
                br = symbols[-1].get('property', {}).get('detectedBreak', {}).get('type')
                result.append(Word(text, *box, raw.get('confidence', 0), br in ('SPACE', 'SURE_SPACE', 'EOL_SURE_SPACE', 1, 2, 3)))
    return result


def _lines(words):
    lines = []
    for word in sorted(words, key=lambda w: (w.y, w.left)):
        line = next((ln for ln in reversed(lines) if abs(sum(w.y for w in ln)/len(ln) - word.y) < min(word.height, max(w.height for w in ln)) * .4), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    return [sorted(line, key=lambda w: w.left) for line in lines]


def _render(words):
    ordered = sorted(words, key=lambda w: w.left)
    return ''.join(w.text + (' ' if w.space_after else '') for w in ordered).strip()


def _anchor(lines, label, optional=False):
    matches = []
    for line in lines:
        for start in range(len(line)):
            value = ''
            for end in range(start, len(line)):
                value += re.sub(r'\s+', '', line[end].text)
                if value == label:
                    selected = line[start:end+1]
                    matches.append(Word(label, min(w.left for w in selected), min(w.top for w in selected), max(w.right for w in selected), max(w.bottom for w in selected), min(w.confidence for w in selected)))
                    break
                if not label.startswith(value):
                    break
    if not matches and optional:
        return None
    if len(matches) != 1:
        raise ReceiptParseError(f'Missing or ambiguous label: {label}')
    return matches[0]


def _evidence(words):
    return {'text': _render(words), 'confidence': min(w.confidence for w in words), 'bbox': [min(w.left for w in words), min(w.top for w in words), max(w.right for w in words), max(w.bottom for w in words)]}


def parse_bank_pdf(annotation):
    compact = re.sub(r'\s+', '', annotation.get('text', ''))
    if '광주은행' not in compact or '매출전표' not in compact:
        raise ReceiptParseError('This is not a recognized Kwangju Bank sales slip.')
    words = _words(annotation)
    lines = _lines(words)
    labels = {key: _anchor(lines, name) for key, name in {
        'subtotal': '금액', 'tax': '부가세', 'service_fee': '봉사료', 'total': '합계',
        'merchant': '가맹점명', 'purchased_at': '거래일자', 'approval_number': '승인번호',
        'business_number': '사업자번호', 'payment_method': '결제방법', 'representative': '대표자명',
    }.items()}
    fields, evidence = {}, {}
    for key in ('subtotal', 'tax', 'service_fee', 'total'):
        anchor = labels[key]
        candidates = [w for w in words if w.left > anchor.right + anchor.height * .5 and abs(w.y - anchor.y) < anchor.height * 1.3]
        amounts = []
        for line in _lines(candidates):
            raw = re.sub(r'\s+', '', _render(line))
            if re.fullmatch(r'-?(?:\d{1,3}(?:,\d{3})+|\d+)', raw):
                amounts.append((Decimal(raw.replace(',', '')), line))
        if len(amounts) != 1:
            raise ReceiptParseError(f'Missing or ambiguous amount: {key}')
        fields[key], selected = amounts[0]
        evidence[key] = _evidence(selected)
        if evidence[key]['confidence'] < .8:
            raise ReceiptParseError(f'Low OCR confidence for {key}.')
    if fields['service_fee'] != 0:
        raise ReceiptParseError('Nonzero service fees require receipt review; the journal schema has no separate fee field.')
    if fields['subtotal'] + fields['tax'] != fields['total'] or any(fields[k] < 0 for k in ('subtotal', 'tax', 'total')):
        raise ReceiptParseError('Monetary fields failed arithmetic/sign validation.')

    for key in ('purchased_at', 'approval_number', 'business_number', 'payment_method'):
        anchor = labels[key]
        half_width = max((anchor.right-anchor.left)*.85, anchor.height*5)
        center = (anchor.left+anchor.right)/2
        selected = [w for w in words if anchor.bottom < w.y < anchor.bottom + anchor.height*2 and center-half_width < (w.left+w.right)/2 < center+half_width]
        rendered = ' '.join(_render(line) for line in _lines(selected))
        if key == 'purchased_at':
            matches = re.findall(r'\b(\d{4})[/.-](\d{2})[/.-](\d{2})\b', rendered)
            if len(matches) != 1:
                raise ReceiptParseError('Missing or ambiguous transaction date.')
            try:
                fields[key] = date(*map(int, matches[0]))
            except ValueError as exc:
                raise ReceiptParseError('Invalid transaction date.') from exc
        elif key == 'approval_number':
            matches = re.findall(r'(?<!\d)\d{6,8}(?!\d)', rendered)
            if len(matches) != 1:
                raise ReceiptParseError('Missing or ambiguous approval number.')
            fields[key] = matches[0]
        elif key == 'business_number':
            matches = re.findall(r'\b\d{3}-\d{2}-\d{5}\b', rendered)
            fields[key] = matches[0] if len(matches) == 1 else None
        else:
            if not rendered:
                raise ReceiptParseError('Payment method is missing.')
            fields[key] = rendered
        if selected:
            evidence[key] = _evidence(selected)
            if key in ('purchased_at', 'approval_number') and evidence[key]['confidence'] < .8:
                raise ReceiptParseError(f'Low OCR confidence for {key}.')

    anchor = labels['merchant']
    # Merchant spans both table columns; the next label row is the lower boundary.
    selected = [w for w in words if anchor.bottom < w.y < min(labels['representative'].top, labels['business_number'].top)]
    merchant_lines = _lines(selected)
    fields['merchant'] = ' '.join(_render(line) for line in merchant_lines)
    if not fields['merchant'] or len(merchant_lines) != 1:
        raise ReceiptParseError('Missing or ambiguous merchant row.')
    evidence['merchant'] = _evidence(selected)
    warnings = ['계정과목은 자동 분류하지 않았습니다. 분개 계정과목을 확인해 주세요.',
                'PDF 전표만으로 취소 여부를 확정할 수 없습니다. 승인번호를 사용내역과 대조해 주세요.']
    if not fields['business_number']:
        warnings.append('사업자번호가 없는 전표입니다. 해외 거래일 수 있으므로 원화 결제금액과 거래일자를 확인해 주세요. 금액은 KRW로 처리했습니다.')
    if evidence['merchant']['confidence'] < .9:
        warnings.append('상호명의 OCR 신뢰도가 낮습니다. 원본 상호명을 확인해 주세요.')
    receipt = ReceiptExtraction(merchant=fields['merchant'], purchased_at=fields['purchased_at'], currency='KRW', subtotal=fields['subtotal'], tax=fields['tax'], total=fields['total'], payment_method=fields['payment_method'], category_hint='other', confidence=min(evidence[k]['confidence'] for k in ('merchant', 'purchased_at', 'subtotal', 'tax', 'total')), line_items=[], warnings=warnings,
        approval_number=fields['approval_number'], business_number=fields['business_number'], extraction_provider='google_vision', field_evidence=evidence)
    return receipt
