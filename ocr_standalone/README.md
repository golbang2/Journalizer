# Spyder 영수증 OCR 실험

`receipt_ocr.py` 하나로 영수증 사진을 읽어 OCR 원문과 처리 시간을 출력합니다.
웹 서버, Streamlit, DB, LLM, 회계 처리, 영수증 필드 파싱은 필요 없습니다.

## 설치: Spyder가 실제로 사용하는 Python 환경

Spyder의 **IPython 콘솔**에서 다음 코드를 한 번 실행하세요.
별도 터미널의 `pip` 대신 현재 콘솔의 Python을 사용하므로 환경이 달라지는 문제를 피할 수 있습니다.

```python
import sys
import subprocess
subprocess.check_call([
    sys.executable, "-m", "pip", "install", "-r",
    r"F:\Journalizer\ocr_standalone\requirements.txt",
])
```

설치 후 Spyder에서 커널을 다시 시작하세요. 원본 SDK 버전을 고정했습니다.
실행 코드에는 Google Vision SDK 외의 추가 패키지가 없습니다.

## Google 인증 설정

이 레포지터리의 엔진은 **Google Cloud Vision API**입니다. 로컬 신경망 모델이나
학습된 가중치가 제공되는 구조가 아니므로, 인터넷과 Google 인증이 필요합니다.
사진은 OCR 요청 시 Google 서버로 전송됩니다. 호출 횟수만큼 API 사용량이 발생합니다.

1. Google Cloud 프로젝트에서 결제를 설정하고 **Cloud Vision API**를 활성화합니다.
2. 프로젝트에서 Vision API를 호출할 수 있는 서비스 계정을 준비하고 JSON 키를 발급받습니다.
3. JSON은 저장소 밖에 보관하고 아래 `CREDENTIALS_PATH`에 경로를 지정합니다.

공식 안내: [Vision 설정](https://docs.cloud.google.com/vision/docs/setup),
[서비스 계정 키 만들기](https://docs.cloud.google.com/iam/docs/creating-managing-service-account-keys).
조직에서 키 발급을 제한한다면 관리자에게 인증 방법을 확인하세요.
이미 `GOOGLE_APPLICATION_CREDENTIALS`가 설정되어 있거나
`gcloud auth application-default login`으로 ADC가 설정되어 있으면 `CREDENTIALS_PATH`는 비워도 됩니다.

## Spyder에서 실행

1. `F:\Journalizer\ocr_standalone\receipt_ocr.py`를 엽니다.
2. 맨 위 설정을 수정합니다. Windows 경로는 `r"..."` 형태로 입력하세요.

   ```python
   IMAGE_PATH = r"C:\Users\taeyoon\Pictures\영수증.jpg"
   CREDENTIALS_PATH = r"C:\Users\taeyoon\Keys\vision-account.json"
   REPEAT = 1
   ```

3. **F5**로 파일 전체를 실행합니다. `IMAGE_PATH`를 비워두면 파일 선택 창이 뜹니다.
4. 콘솔에서 인식된 원문과 처리 시간을 확인합니다. 변수 탐색기의 `result`에도 결과가 남습니다.

상대 경로는 Spyder의 현재 작업 폴더를 기준으로 해석되므로 절대 경로를 권장합니다.
지원 입력은 JPG/JPEG/PNG/BMP/WEBP 사진입니다. PDF는 이 실험에 포함하지 않습니다.
Google Vision 오류가 나면 콘솔의 오류 내용을 보고 API 활성화, 프로젝트 결제,
계정 권한, 이미지 형식을 확인하세요.

## 성능 확인

- `REPEAT = 1`: 사진 한 장을 한 번 인식합니다. 기본값입니다.
- `REPEAT = 5`: 같은 사진으로 다섯 번 호출하고 API 응답 시간의 평균/중앙값을 출력합니다.
  모든 호출의 원문과 시간은 `result["runs"]`에 남으며 콘솔 원문은 마지막 호출 결과입니다.
- API 응답 시간에는 업로드, 네트워크, 인증 토큰 획득, Google 서버 처리 시간이 포함됩니다.
  순수 모델 추론 시간은 API에서 제공하지 않습니다. 첫 호출도 평균에 포함됩니다.
- 이미지 읽기를 포함한 시간과 SDK/클라이언트 초기화 시간은 별도로 출력합니다.
  콘솔 출력과 클라이언트 종료 시간은 측정에서 제외됩니다. 자동 재시도는 끄고 요청에
  `TIMEOUT_SECONDS`를 전달합니다. 인증 등 SDK 내부 작업 때문에 전체 실행 시간이 이 값보다 길 수 있습니다.
- 정확도도 수치로 보려면 영수증을 사람이 그대로 옮겨 적은 UTF-8 `.txt` 파일을 만들고
  `GROUND_TRUTH_PATH`에 경로를 넣습니다. 공백/줄바꿈을 제외하고 한글을 NFC로 정규화한
  문자 오류율(CER = 삽입·삭제·치환 최소 횟수 / 정답 문자 수)을 출력합니다.
  0%가 완전 일치이며, 삽입 오류가 많으면 100%를 넘을 수도 있습니다.
  정답 파일을 지정하지 않으면 정확도 수치를 추정하지 않습니다.

## 원본과의 관계

출처: [jojindragon/OCR-Receipt-System](https://github.com/jojindragon/OCR-Receipt-System)
커밋: `b8e98c3f795aa9169c96bd38e7fe71c4f926f532`

기준 파일은 [google_vision_adapter.py](https://github.com/jojindragon/OCR-Receipt-System/blob/b8e98c3f795aa9169c96bd38e7fe71c4f926f532/services/ocr_pipeline/ocr/google_vision_adapter.py)입니다.
이미지 바이트를 `vision.Image`로 전달하고 `client.text_detection()`을 호출한 뒤
`text_annotations[0].description`을 반환하는 OCR 동작을 유지했습니다.
Streamlit 인증 처리를 제거하고 Spyder용 경로 설정, 파일 선택, 입력 검증, 시간 측정과
선택적 CER 평가를 추가했습니다. 전처리, 언어 힌트, 다른 OCR 모델로의 교체는 적용하지 않았습니다.

로컬 테스트(API 호출 없음):

```powershell
python -m unittest discover -s F:\Journalizer\ocr_standalone\tests -v
```
