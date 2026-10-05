"""네트워크 없이 실제 Vision 요청 형식과 Spyder 출력 흐름을 확인합니다."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from google.auth.credentials import AnonymousCredentials
from google.cloud import vision

SCRIPT = Path(__file__).resolve().parents[1] / "receipt_ocr.py"
spec = importlib.util.spec_from_file_location("receipt_ocr", SCRIPT)
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


class OCRTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.image = Path(self.folder.name) / "영수증 사진.png"
        # 유효한 1x1 PNG. 서버 대신 transport를 대체하므로 API 사용량은 발생하지 않음.
        import base64
        self.content = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
        self.image.write_bytes(self.content)

    def engine(self, response):
        client = vision.ImageAnnotatorClient(credentials=AnonymousCredentials())
        self.addCleanup(client.transport.close)
        # 공개 text_detection wrapper는 실제 SDK를 사용하고, 네트워크 RPC만 교체.
        rpc = Mock(return_value=vision.BatchAnnotateImagesResponse(responses=[response]))
        client.transport._wrapped_methods[client.transport.batch_annotate_images] = rpc
        with patch.object(vision, "ImageAnnotatorClient", return_value=client):
            engine = ocr.GoogleVisionOCR()
        return engine, rpc

    def test_original_text_detection_and_korean_path(self):
        engine, rpc = self.engine(vision.AnnotateImageResponse(
            text_annotations=[vision.EntityAnnotation(description="카페\n합계 4,500\n")]
        ))
        result = engine.run(self.image, timeout=17)
        request = rpc.call_args.args[0]
        self.assertEqual(request.requests[0].image.content, self.content)
        self.assertEqual(request.requests[0].features[0].type_, vision.Feature.Type.TEXT_DETECTION)
        self.assertEqual(rpc.call_args.kwargs["timeout"], 17)
        self.assertIsNone(rpc.call_args.kwargs["retry"])
        self.assertEqual(result["full_text"], "카페\n합계 4,500\n")
        self.assertGreaterEqual(result["elapsed_seconds"], result["api_seconds"])

    def test_no_text_is_empty_string(self):
        engine, _ = self.engine(vision.AnnotateImageResponse())
        self.assertEqual(engine.run(self.image)["full_text"], "")

    def test_api_error_is_not_success(self):
        engine, _ = self.engine(vision.AnnotateImageResponse(error={"code": 7, "message": "denied"}))
        with self.assertRaisesRegex(RuntimeError, "denied"):
            engine.run(self.image)

    def test_bad_images_never_call_api(self):
        engine, rpc = self.engine(vision.AnnotateImageResponse())
        with self.assertRaises(FileNotFoundError):
            engine.run(self.image.parent / "missing.png")
        with self.assertRaises(ValueError):
            engine.run("")
        self.image.write_bytes(b"")
        with self.assertRaises(ValueError):
            engine.run(self.image)
        rpc.assert_not_called()

    def test_bad_credentials_never_initialize_client(self):
        with patch.object(vision, "ImageAnnotatorClient") as client:
            with self.assertRaises(FileNotFoundError):
                ocr.GoogleVisionOCR(str(self.image.parent / "missing.json"))
            client.assert_not_called()
            client.from_service_account_file.assert_not_called()

    def test_cer_insert_delete_replace_and_normalization(self):
        self.assertEqual(ocr.character_error_rate("가 나\n", "가나"), 0)
        self.assertEqual(ocr.character_error_rate("abc", "axc"), 1 / 3)
        self.assertEqual(ocr.character_error_rate("abc", "ab"), 1 / 3)
        self.assertEqual(ocr.character_error_rate("ab", "abc"), 1 / 2)
        self.assertEqual(ocr.character_error_rate("a", "aaa"), 2)
        with self.assertRaises(ValueError):
            ocr.character_error_rate(" \n", "abc")

    def test_spyder_main_repeated_calls_and_cer(self):
        engine, rpc = self.engine(vision.AnnotateImageResponse(
            text_annotations=[vision.EntityAnnotation(description="합계 4,500\n")]
        ))
        truth = self.image.parent / "정답.txt"
        truth.write_text("합계 4,500", encoding="utf-8-sig")
        with patch.multiple(ocr, IMAGE_PATH=str(self.image), GROUND_TRUTH_PATH=str(truth), REPEAT=2), \
             patch.object(ocr, "GoogleVisionOCR", return_value=engine), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result = ocr.main()
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(rpc.call_count, 2)
        self.assertEqual(result["cer"], 0)
        self.assertIn("합계 4,500", output.getvalue())
        self.assertIn("0.00%", output.getvalue())

    def test_missing_ground_truth_rejected_before_api(self):
        with patch.multiple(ocr, IMAGE_PATH=str(self.image), GROUND_TRUTH_PATH=str(self.image.parent / "missing.txt")), \
             patch.object(ocr, "GoogleVisionOCR") as factory:
            with self.assertRaises(FileNotFoundError):
                ocr.main()
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
