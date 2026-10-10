"""Google Vision OCR for single-page PDFs and receipt images, with shared caching."""
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading

from app.services.bank_pdf_parser import ReceiptInputError, ReceiptParseError, parse_bank_pdf

OCR_SETTINGS = {'version': 1, 'feature': 'DOCUMENT_TEXT_DETECTION', 'mime_type': 'application/pdf', 'pages': [1]}
IMAGE_OCR_SETTINGS = {'version': 1, 'feature': 'DOCUMENT_TEXT_DETECTION', 'input_type': 'image'}
_CACHE_LOCKS = [threading.Lock() for _ in range(32)]
_OCR_SLOTS = threading.BoundedSemaphore(4)


class VisionResponseError(RuntimeError):
    pass


def cache_key(content: bytes, settings: dict | None = None) -> str:
    settings = json.dumps(OCR_SETTINGS if settings is None else settings, sort_keys=True).encode()
    return hashlib.sha256(settings + b'\0' + content).hexdigest()


def annotation_from_response(response: dict) -> dict:
    files = response.get('responses', [])
    if len(files) != 1:
        raise VisionResponseError('Vision returned an unexpected file count.')
    file = files[0]
    pages = file.get('responses', [])
    for item in [file, *pages]:
        if item.get('error', {}).get('code'):
            raise VisionResponseError(item['error'].get('message', 'Vision OCR failed.'))
    if len(pages) != 1 or not pages[0].get('fullTextAnnotation'):
        raise VisionResponseError('Vision did not return a single page of document text.')
    return pages[0]['fullTextAnnotation']


def image_annotation_from_response(response: dict) -> dict:
    images = response.get('responses', [])
    if len(images) != 1:
        raise VisionResponseError('Vision returned an unexpected image count.')
    item = images[0]
    if item.get('error', {}).get('code'):
        raise VisionResponseError(item['error'].get('message', 'Vision image OCR failed.'))
    annotation = item.get('fullTextAnnotation')
    if not annotation or not annotation.get('text', '').strip():
        raise VisionResponseError('Vision did not return document text for the image.')
    if len(annotation.get('pages', [])) != 1:
        raise VisionResponseError('Vision did not return a single image page.')
    return annotation


@lru_cache(maxsize=1)
def _client():
    from google.cloud import vision
    return vision.ImageAnnotatorClient()


def _request_ocr(content):
    from google.api_core.retry import Retry
    from google.cloud import vision
    request = vision.AnnotateFileRequest(
        input_config=vision.InputConfig(content=content, mime_type='application/pdf'),
        features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
        pages=[1],
    )
    with _OCR_SLOTS:
        response = _client().batch_annotate_files(requests=[request], retry=Retry(initial=.5, maximum=2, multiplier=2, timeout=30), timeout=30)
    return json.loads(vision.BatchAnnotateFilesResponse.to_json(response, preserving_proto_field_name=False))


def _request_image_ocr(content):
    from google.api_core.retry import Retry
    from google.cloud import vision
    request = vision.AnnotateImageRequest(
        image=vision.Image(content=content),
        features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
    )
    with _OCR_SLOTS:
        response = _client().batch_annotate_images(requests=[request], retry=Retry(initial=.5, maximum=2, multiplier=2, timeout=30), timeout=30)
    return json.loads(vision.BatchAnnotateImagesResponse.to_json(response, preserving_proto_field_name=False))


def ocr_pdf(content: bytes) -> dict:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or len(reader.pages) != 1:
            raise ReceiptInputError('The Vision rule path requires one unencrypted PDF page.')
    except ReceiptParseError:
        raise
    except Exception as exc:
        raise ReceiptInputError('The PDF cannot be inspected.') from exc
    return _cached_ocr(content, OCR_SETTINGS, _request_ocr, annotation_from_response)


def ocr_image(content: bytes) -> dict:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP', 'BMP', 'GIF', 'TIFF'):
                raise ReceiptInputError('Unsupported receipt image format.')
            if getattr(image, 'n_frames', 1) != 1:
                raise ReceiptInputError('Each receipt image must contain one frame only.')
            image.verify()
    except ReceiptParseError:
        raise
    except Exception as exc:
        raise ReceiptInputError('The receipt image cannot be inspected.') from exc
    return _cached_ocr(content, IMAGE_OCR_SETTINGS, _request_image_ocr, image_annotation_from_response)


def _cached_ocr(content, settings, request, read_annotation):
    key = cache_key(content, settings)
    directory = Path(os.environ.get('VISION_OCR_CACHE_DIR', str(Path(__file__).resolve().parents[2] / '.cache/vision')))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{key}.json'
    with _CACHE_LOCKS[int(key[:2], 16) % len(_CACHE_LOCKS)]:
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
                if cached.get('cache_key') == key and cached.get('settings') == settings:
                    read_annotation(cached['response'])
                    return cached['response']
            except (ValueError, KeyError, VisionResponseError):
                pass
        response = request(content)
        read_annotation(response)
        hash_field = 'pdf_sha256' if settings is OCR_SETTINGS else 'image_sha256'
        entry = {'cache_key': key, hash_field: hashlib.sha256(content).hexdigest(), 'settings': settings, 'response': response}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(entry, handle, ensure_ascii=False)
            temporary.replace(path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return response


def extract_vision_pdf(content: bytes):
    annotation = annotation_from_response(ocr_pdf(content))
    return _parse_annotation(annotation)


def extract_vision_image(content: bytes):
    return _parse_annotation(image_annotation_from_response(ocr_image(content)))


def _parse_annotation(annotation):
    compact = ''.join(annotation.get('text', '').split())
    if '우리카드' in compact and '매출전표' in compact:
        from app.services.woori_pdf_parser import parse_woori_pdf
        return parse_woori_pdf(annotation)[0]
    if '광주은행' in compact and '매출전표' in compact:
        return parse_bank_pdf(annotation)
    from app.services.card_receipt_parser import parse_card_receipt
    return parse_card_receipt(annotation)
