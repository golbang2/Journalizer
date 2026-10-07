# -*- coding: utf-8 -*-
"""Spyder에서 실행하는 Google Vision 영수증 OCR 실험.

원본: jojindragon/OCR-Receipt-System
services/ocr_pipeline/ocr/google_vision_adapter.py
commit: b8e98c3f795aa9169c96bd38e7fe71c4f926f532
"""

import os
from pathlib import Path
from statistics import mean, median
from time import perf_counter
import unicodedata


# %% 이 부분만 수정한 뒤 Spyder에서 F5로 실행하세요.
IMAGE_PATH = str(Path(__file__).resolve().parents[1] / "test_data" / "IMG_4192.jpg")
CREDENTIALS_PATH = r""  # 로컬에서 지정하거나 GOOGLE_APPLICATION_CREDENTIALS 사용.
REPEAT = 1
TIMEOUT_SECONDS = 60
GROUND_TRUTH_PATH = r""  # 선택: 사람이 옮겨 적은 정답 텍스트(.txt, UTF-8)


# %% OCR 엔진: 원본과 같은 TEXT_DETECTION / text_annotations[0] 사용.
class GoogleVisionOCR:
    def __init__(self, credentials_path=""):
        try:
            from google.cloud import vision
            from google.auth.exceptions import DefaultCredentialsError
        except ImportError as exc:
            raise RuntimeError(
                "Spyder가 사용하는 Python 환경에 google-cloud-vision을 설치하세요. "
                "설치 방법은 이 폴더의 README.md를 참고하세요."
            ) from exc

        self.vision = vision
        credential_file = credentials_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if credential_file:
            path = Path(credential_file).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Google 인증 JSON 파일을 찾을 수 없습니다: {path}")
            self.client = vision.ImageAnnotatorClient.from_service_account_file(str(path))
        else:
            # gcloud auth application-default login으로 설정한 ADC도 사용 가능.
            try:
                self.client = vision.ImageAnnotatorClient()
            except DefaultCredentialsError as exc:
                raise RuntimeError(
                    "Google 인증이 필요합니다. CREDENTIALS_PATH에 서비스 계정 JSON "
                    "경로를 입력하거나 Application Default Credentials를 설정하세요."
                ) from exc

    def run(self, image_path, timeout=60):
        path = checked_image_path(image_path)
        start = perf_counter()
        content = path.read_bytes()
        if not content:
            raise ValueError(f"이미지 파일이 비어 있습니다: {path}")

        image = self.vision.Image(content=content)
        api_start = perf_counter()
        # 자동 재시도를 끄고 요청별 처리 시간을 측정합니다.
        response = self.client.text_detection(image=image, retry=None, timeout=timeout)
        api_seconds = perf_counter() - api_start
        if response.error.message:
            raise RuntimeError(f"Google Vision OCR 오류: {response.error.message}")

        texts = response.text_annotations
        return {
            "adapter": "google_vision",
            "image_name": str(path),
            "full_text": texts[0].description if texts else "",
            "api_seconds": api_seconds,
            "elapsed_seconds": perf_counter() - start,
        }

    def close(self):
        self.client.transport.close()


def checked_image_path(image_path):
    if not image_path:
        raise ValueError("파일 선택 창에서 영수증을 선택하거나 IMAGE_PATH를 입력하세요.")
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"영수증 이미지 파일을 찾을 수 없습니다: {path}")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        raise ValueError("JPG, JPEG, PNG, BMP, WEBP 영수증 사진을 입력하세요.")
    return path


def select_image():
    """IMAGE_PATH가 비어 있으면 파일 선택 창을 표시합니다."""
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    try:
        return filedialog.askopenfilename(
            title="OCR을 확인할 영수증 사진 선택",
            filetypes=[("영수증 이미지", "*.jpg *.jpeg *.png *.bmp *.webp"), ("모든 파일", "*.*")],
        )
    finally:
        root.destroy()


def character_error_rate(reference, prediction):
    """공백/줄바꿈을 제외한 NFC 문자 기준 Levenshtein 거리 / 정답 길이."""
    reference = "".join(unicodedata.normalize("NFC", reference).split())
    prediction = "".join(unicodedata.normalize("NFC", prediction).split())
    if not reference:
        raise ValueError("정답 텍스트에 공백 외의 문자가 없습니다.")
    previous = list(range(len(prediction) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(prediction, 1):
            current.append(min(current[j - 1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected != actual)))
        previous = current
    return previous[-1] / len(reference)


# %% 실행 결과는 Spyder 콘솔과 변수 탐색기의 result에 표시됩니다.
def main():
    if isinstance(REPEAT, bool) or not isinstance(REPEAT, int) or REPEAT < 1:
        raise ValueError("REPEAT는 1 이상의 정수여야 합니다.")
    if TIMEOUT_SECONDS <= 0:
        raise ValueError("TIMEOUT_SECONDS는 0보다 커야 합니다.")
    path = checked_image_path(IMAGE_PATH or select_image())
    reference = None
    if GROUND_TRUTH_PATH:
        reference = Path(GROUND_TRUTH_PATH).expanduser().read_text(encoding="utf-8-sig")
        character_error_rate(reference, "")  # API 호출 전에 정답 파일 검증.

    print(f"영수증: {path}")
    print(f"Google Vision TEXT_DETECTION 호출: {REPEAT}회")
    start = perf_counter()
    engine = GoogleVisionOCR(CREDENTIALS_PATH)
    initialization_seconds = perf_counter() - start
    runs = []
    try:
        for index in range(REPEAT):
            run = engine.run(path, timeout=TIMEOUT_SECONDS)
            if reference is not None:
                run["cer"] = character_error_rate(reference, run["full_text"])
            runs.append(run)
            print(f"[{index + 1}/{REPEAT}] API 응답: {run['api_seconds']:.3f}초 / "
                  f"이미지 읽기 포함: {run['elapsed_seconds']:.3f}초")
    finally:
        engine.close()

    print("\n========== OCR 인식 결과 (마지막 호출) ==========")
    print(runs[-1]["full_text"] or "인식된 텍스트가 없습니다.")
    print("================================================")
    print(f"SDK 및 클라이언트 초기화: {initialization_seconds:.3f}초")
    times = [run["api_seconds"] for run in runs]
    print(f"API 응답 시간 평균: {mean(times):.3f}초 / 중앙값: {median(times):.3f}초")
    if reference is not None:
        print(f"마지막 호출 문자 오류율(CER, 공백 제외): {runs[-1]['cer']:.2%} (낮을수록 좋음)")
    return {**runs[-1], "initialization_seconds": initialization_seconds, "runs": runs}


if __name__ == "__main__":
    result = main()
