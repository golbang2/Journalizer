import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.bank_pdf_parser import ReceiptParseError, parse_bank_pdf
from app.services.receipts import extract_receipt
from app.services.vision_receipts import OCR_SETTINGS, VisionResponseError, cache_key, ocr_pdf
from app.services.vision_receipts import IMAGE_OCR_SETTINGS, ocr_image
from PIL import Image
from pypdf import PdfWriter


def annotation():
    values = [
        ('거래일자', .32, .095), ('2023/05/08', .31, .110),
        ('승인번호', .15, .156), ('12345678', .15, .171),
        ('금액', .30, .158), ('3,455', .40, .164),
        ('부가세', .30, .188), ('345', .40, .194),
        ('봉사료', .30, .216), ('0', .40, .222),
        ('합계', .30, .245), ('3,800', .40, .251),
        ('결제방법', .12, .186), ('체크예금', .12, .201),
        ('가맹점명', .25, .272), ('테스트카페', .22, .288),
        ('대표자명', .15, .303), ('사업자번호', .32, .303), ('111-22-33333', .32, .318),
        ('2024-06-28', .80, .993),
    ]
    words = []
    for text, x, y in values:
        words.append({'symbols': [{'text': c} for c in text], 'confidence': .99,
                      'boundingBox': {'normalizedVertices': [{'x':x,'y':y-.004}, {'x':x+.045,'y':y-.004}, {'x':x+.045,'y':y+.004}, {'x':x,'y':y+.004}]}})
    return {'text':'광주은행 매출전표', 'pages':[{'width':595, 'height':841, 'blocks':[{'paragraphs':[{'words':words}]}]}]}


def response():
    return {'responses':[{'responses':[{'fullTextAnnotation':annotation()}]}]}


def pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=841)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def image_bytes(format='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (64, 80), 'white').save(output, format=format)
    return output.getvalue()


def image_response():
    return {'responses':[{'fullTextAnnotation':annotation()}]}


class BankParserTests(unittest.TestCase):
    def test_transaction_date_not_browser_print_date_and_preserve_metadata(self):
        r = parse_bank_pdf(annotation())
        self.assertEqual(str(r.purchased_at), '2023-05-08')
        self.assertEqual(str(r.total), '3800')
        self.assertEqual(r.approval_number, '12345678')
        self.assertEqual(r.business_number, '111-22-33333')
        self.assertEqual(r.merchant, '테스트카페')
        self.assertEqual(r.line_items, [])
        self.assertEqual(r.extraction_provider, 'google_vision')
        self.assertIn('bbox', r.field_evidence['total'])

    def test_pixel_boxes_and_shifted_layout_produce_same_result(self):
        data = annotation()
        for w in data['pages'][0]['blocks'][0]['paragraphs'][0]['words']:
            vertices = w['boundingBox'].pop('normalizedVertices')
            w['boundingBox']['vertices'] = [{'x':(v['x']+.03)*595, 'y':(v['y']+.002)*841} for v in vertices]
        self.assertEqual(parse_bank_pdf(data).merchant, '테스트카페')
        self.assertEqual(str(parse_bank_pdf(data).total), '3800')

    def test_missing_label_arithmetic_low_confidence_and_unknown_layout_fail(self):
        for mode in ('missing', 'arithmetic', 'confidence', 'layout', 'date'):
            data = annotation()
            words = data['pages'][0]['blocks'][0]['paragraphs'][0]['words']
            if mode == 'layout':
                data['text'] = '다른 은행 영수증'
            else:
                target = next(w for w in words if ''.join(s['text'] for s in w['symbols']) == ('부가세' if mode == 'missing' else '2023/05/08' if mode == 'date' else '3,800'))
                if mode == 'missing':
                    words.remove(target)
                elif mode == 'confidence':
                    target['confidence'] = .4
                else:
                    target['symbols'] = [{'text':c} for c in ('2023/99/08' if mode == 'date' else '3,900')]
            with self.subTest(mode=mode), self.assertRaises(ReceiptParseError):
                parse_bank_pdf(data)

    def test_foreign_review_keeps_won_and_numbers(self):
        data = annotation()
        words = data['pages'][0]['blocks'][0]['paragraphs'][0]['words']
        words[:] = [w for w in words if ''.join(s['text'] for s in w['symbols']) != '111-22-33333']
        r = parse_bank_pdf(data)
        self.assertIsNone(r.business_number)
        self.assertEqual(r.currency, 'KRW')
        self.assertEqual(str(r.total), '3800')
        self.assertTrue(any('해외 거래' in w for w in r.warnings))


class VisionCacheTests(unittest.TestCase):
    def test_cache_reuses_successful_ocr_and_settings_changes_invalidate(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder}), patch('app.services.vision_receipts._request_ocr', return_value=response()) as request:
            content = pdf()
            first = cache_key(content)
            ocr_pdf(content)
            ocr_pdf(content)
            self.assertEqual(request.call_count, 1)
            with patch.dict(OCR_SETTINGS, {'version':2}):
                self.assertNotEqual(cache_key(content), first)
                ocr_pdf(content)
            self.assertEqual(request.call_count, 2)
            path = Path(folder)/f'{first}.json'
            path.write_text('{broken', encoding='utf-8')
            ocr_pdf(content)
            self.assertEqual(request.call_count, 3)

    def test_error_is_not_cached_or_hidden_by_fallback(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder, 'RECEIPT_PDF_ENGINE':'vision', 'RECEIPT_VISION_FALLBACK':'claude'}), patch('app.services.vision_receipts._request_ocr', return_value={'responses':[{'error':{'code':7,'message':'access denied'}}]}), patch('app.services.receipts.extract_claude_receipt') as claude:
            with self.assertRaises(VisionResponseError):
                extract_receipt('sample.pdf', pdf())
            self.assertEqual(list(Path(folder).glob('*.json')), [])
            claude.assert_not_called()

    def test_multiple_pages_are_not_silently_truncated(self):
        with patch('app.services.vision_receipts._request_ocr') as request:
            with self.assertRaises(ReceiptParseError):
                ocr_pdf(pdf(2))
            request.assert_not_called()

    def test_vision_is_default_for_pdf_and_image_and_claude_requires_opt_in(self):
        parsed = parse_bank_pdf(annotation())
        with patch.dict(os.environ, {}, clear=True), patch('app.services.receipts.extract_claude_receipt') as claude, patch('app.services.vision_receipts.extract_vision_pdf', return_value=parsed) as pdf_extract, patch('app.services.vision_receipts.extract_vision_image', return_value=parsed) as image_extract:
            self.assertEqual(extract_receipt('sample.PDF', b'pdf'), parsed)
            for name in ('sample.jpg','sample.jpeg','sample.png','sample.webp','sample.bmp','sample.gif','sample.tiff'):
                self.assertEqual(extract_receipt(name, b'image'), parsed)
            pdf_extract.assert_called_once()
            self.assertEqual(image_extract.call_count, 7)
            claude.assert_not_called()
        with patch.dict(os.environ, {'RECEIPT_OCR_ENGINE':'claude'}, clear=True), patch('app.services.receipts.extract_claude_receipt', return_value=parsed) as claude:
            extract_receipt('sample.png', b'image')
            claude.assert_called_once()

    def test_pdf_override_and_fallback_require_explicit_setting(self):
        parsed = parse_bank_pdf(annotation())
        with patch.dict(os.environ, {'RECEIPT_PDF_ENGINE':'claude'}), patch('app.services.receipts.extract_claude_receipt', return_value=parsed) as claude, patch('app.services.vision_receipts.extract_vision_pdf') as vision:
            extract_receipt('sample.pdf', b'pdf')
            claude.assert_called_once()
            vision.assert_not_called()
        with patch.dict(os.environ, {'RECEIPT_PDF_ENGINE':'vision', 'RECEIPT_VISION_FALLBACK':'none'}), patch('app.services.vision_receipts.extract_vision_pdf', side_effect=ReceiptParseError('unknown layout')), patch('app.services.receipts.extract_claude_receipt') as claude:
            with self.assertRaises(ReceiptParseError):
                extract_receipt('sample.pdf', b'pdf')
            claude.assert_not_called()
        with patch.dict(os.environ, {'RECEIPT_PDF_ENGINE':'vision', 'RECEIPT_VISION_FALLBACK':'claude'}), patch('app.services.vision_receipts.extract_vision_pdf', side_effect=ReceiptParseError('unknown layout')), patch('app.services.receipts.extract_claude_receipt', return_value=parsed) as claude:
            result = extract_receipt('sample.pdf', b'pdf')
            self.assertEqual(claude.call_args.kwargs['model'], os.environ.get('CLAUDE_RECEIPT_MODEL', 'claude-haiku-5-5'))
            self.assertTrue(any('Claude로 처리' in w for w in result.warnings))

    def test_image_cache_and_pdf_cache_are_separate_and_reuse_success(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder}), patch('app.services.vision_receipts._request_image_ocr', return_value=image_response()) as request:
            content = image_bytes()
            self.assertNotEqual(cache_key(content), cache_key(content, IMAGE_OCR_SETTINGS))
            ocr_image(content)
            ocr_image(content)
            self.assertEqual(request.call_count, 1)
            key = cache_key(content, IMAGE_OCR_SETTINGS)
            (Path(folder)/f'{key}.json').write_text('{broken', encoding='utf-8')
            ocr_image(content)
            self.assertEqual(request.call_count, 2)

    def test_invalid_and_multiple_frame_images_are_rejected_before_ocr(self):
        output = io.BytesIO()
        Image.new('RGB',(64,80),'white').save(output, format='GIF', save_all=True, append_images=[Image.new('RGB',(64,80),'black')])
        with patch('app.services.vision_receipts._request_image_ocr') as request:
            for data in (b'not an image', output.getvalue()):
                with self.subTest(data=data[:20]), self.assertRaises(ReceiptParseError):
                    ocr_image(data)
            request.assert_not_called()

    def test_invalid_receipt_input_does_not_trigger_explicit_claude_fallback(self):
        with patch.dict(os.environ, {'RECEIPT_VISION_FALLBACK':'claude'}, clear=True), patch('app.services.receipts.extract_claude_receipt') as claude:
            for name, data in [('many.pdf',pdf(2)),('broken.pdf',b'broken'),('broken.png',b'broken')]:
                with self.subTest(name=name), self.assertRaises(ReceiptParseError):
                    extract_receipt(name, data)
            claude.assert_not_called()

    def test_image_ocr_error_and_empty_response_are_not_cached_or_sent_to_claude(self):
        for response_data in ({'responses':[{'error':{'code':7,'message':'denied'}}]}, {'responses':[{}]}):
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder, 'RECEIPT_VISION_FALLBACK':'claude'}, clear=True), patch('app.services.vision_receipts._request_image_ocr', return_value=response_data), patch('app.services.receipts.extract_claude_receipt') as claude:
                with self.assertRaises(VisionResponseError):
                    extract_receipt('sample.png', image_bytes())
                self.assertEqual(list(Path(folder).glob('*.json')), [])
                claude.assert_not_called()

    def test_sdk_image_request_uses_document_text_detection_and_original_content(self):
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import vision
        from app.services.vision_receipts import _request_image_ocr
        client = vision.ImageAnnotatorClient(credentials=AnonymousCredentials())
        response_data = vision.BatchAnnotateImagesResponse(responses=[vision.AnnotateImageResponse(full_text_annotation={'text':'test','pages':[{'width':64,'height':80}]})])
        content = image_bytes()
        with patch('app.services.vision_receipts._client', return_value=client), patch.object(client, 'batch_annotate_images', return_value=response_data) as rpc:
            result = _request_image_ocr(content)
        request = rpc.call_args.kwargs['requests'][0]
        self.assertEqual(request.image.content, content)
        self.assertEqual(request.features[0].type_, vision.Feature.Type.DOCUMENT_TEXT_DETECTION)
        self.assertEqual(result['responses'][0]['fullTextAnnotation']['text'], 'test')
        client.transport.close()


class ApiIntegrationTests(unittest.TestCase):
    def test_image_default_extracts_and_builds_draft_without_anthropic_key(self):
        from fastapi.testclient import TestClient
        from app.main import app
        for format, ext in [('JPEG','jpg'),('PNG','png'),('WEBP','webp')]:
            with self.subTest(format=format), tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder}, clear=True), patch('app.services.vision_receipts._request_image_ocr', return_value=image_response()) as request, patch('app.services.receipts.extract_claude_receipt') as claude, TestClient(app) as client:
                extracted = client.post('/receipts/extract', files={'file':(f'receipt.{ext}', image_bytes(format), f'image/{ext}')})
                self.assertEqual(extracted.status_code, 200, extracted.text)
                receipt = extracted.json()
                self.assertEqual(receipt['extraction_provider'],'google_vision')
                self.assertEqual(receipt['approval_number'],'12345678')
                draft = client.post('/journal-entries/draft', json=receipt)
                self.assertEqual(draft.status_code, 200)
                self.assertEqual(draft.json()['lines'][-1]['amount'], '3800')
                request.assert_called_once()
                claude.assert_not_called()

    def test_extract_to_draft_preserves_won_warning_and_approval_number(self):
        from fastapi.testclient import TestClient
        from app.main import app
        data = response()
        words = data['responses'][0]['responses'][0]['fullTextAnnotation']['pages'][0]['blocks'][0]['paragraphs'][0]['words']
        words[:] = [w for w in words if ''.join(s['text'] for s in w['symbols']) != '111-22-33333']
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'VISION_OCR_CACHE_DIR':folder, 'RECEIPT_PDF_ENGINE':'vision'}), patch('app.services.vision_receipts._request_ocr', return_value=data):
            with TestClient(app) as client:
                extracted = client.post('/receipts/extract', files={'file':('foreign.pdf', pdf(), 'application/pdf')})
                self.assertEqual(extracted.status_code, 200)
                receipt = extracted.json()
                self.assertEqual(receipt['currency'], 'KRW')
                self.assertEqual(receipt['approval_number'], '12345678')
                self.assertTrue(any('원화 결제금액' in w for w in receipt['warnings']))
                draft = client.post('/journal-entries/draft', json=receipt)
                self.assertEqual(draft.status_code, 200)
                self.assertEqual(draft.json()['receipt']['field_evidence'], receipt['field_evidence'])
                self.assertEqual(draft.json()['warnings'], receipt['warnings'])

    def test_unsupported_receipt_returns_review_error(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with patch.dict(os.environ, {'RECEIPT_PDF_ENGINE':'vision', 'RECEIPT_VISION_FALLBACK':'none'}), patch('app.services.vision_receipts.extract_vision_pdf', side_effect=ReceiptParseError('unknown layout')):
            with TestClient(app) as client:
                result = client.post('/receipts/extract', files={'file':('sample.pdf', b'pdf', 'application/pdf')})
                self.assertEqual(result.status_code, 422)
                self.assertIn('전표 확인', result.json()['detail'])

    def test_vision_error_returns_gateway_error(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with patch.dict(os.environ, {'RECEIPT_PDF_ENGINE':'vision'}), patch('app.services.vision_receipts.extract_vision_pdf', side_effect=VisionResponseError('provider internals')):
            with TestClient(app) as client:
                result = client.post('/receipts/extract', files={'file':('sample.pdf', b'pdf', 'application/pdf')})
                self.assertEqual(result.status_code, 502)
                self.assertNotIn('provider internals', result.json()['detail'])


if __name__ == '__main__':
    unittest.main()
