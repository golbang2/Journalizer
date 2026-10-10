# Journalizer

Journalizer is a receipt-to-journal-entry automation service. It helps turn receipt images or PDFs into structured accounting journal drafts that can be reviewed, edited, confirmed, stored, and exported.

## Product Flow

1. Upload a receipt image or PDF.
2. Extract structured receipt data.
3. Recommend accounting categories and journal lines.
4. Review and edit the generated draft.
5. Confirm, store, and export the journal entry.

## Principles

- Accountant trust comes first.
- Automation should be explainable.
- Generated entries should be editable before confirmation.
- Confirmed work should be reversible and auditable.

## Tech Stack

- Frontend: Next.js, TypeScript, React
- Backend: FastAPI, Pydantic
- API: JSON over HTTP
- Uploads: multipart form data

## Repository Structure

```text
Journalizer/
├── backend/    # FastAPI backend
├── frontend/   # Next.js frontend
└── test.cmd    # Root build/test helper
```

## Getting Started

For a standalone receipt OCR experiment in Spyder, see
[ocr_standalone/README.md](ocr_standalone/README.md). It uses the Google Vision
OCR from OCR-Receipt-System independently of the web application.

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
python -m pytest
```

### 원장 만들기 화면 (`/`)

첫 화면은 로그인이며, 로그인하면 4단계 작업 화면이 나온다. 회원 한 명이 회사 하나를 가지며
다른 회원의 데이터는 보이지 않는다.

- **DB**: `DATABASE_URL`이 없으면 `backend/data/app.db`(SQLite)를 쓴다. 운영에서는 PostgreSQL을 쓴다.
  예: `DATABASE_URL=postgresql+psycopg://journalizer:비밀번호@localhost:5432/journalizer`
  표는 서버 시작 시 자동으로 만들어진다. 영수증 원본 파일도 DB에 저장된다.
- **로그인**: 이메일·비밀번호(scrypt 해시, 세션은 httpOnly 쿠키). `GOOGLE_CLIENT_ID`,
  `GOOGLE_CLIENT_SECRET`을 넣으면 구글 로그인 버튼이 나타난다. 자세한 값은 `backend/.env.example`.
- **프록시**: 브라우저는 프론트엔드(3000)의 `/api/*`만 호출하고, Next.js가 이를 백엔드로 넘긴다.
  백엔드 주소가 8000이 아니면 프론트엔드를 켤 때 `BACKEND_URL`을 지정한다.
- **PostgreSQL로 테스트**: `TEST_DATABASE_URL`을 지정하면 백엔드 테스트가 그 DB에서 돈다.
  테스트마다 표를 지우고 새로 만들므로 **실데이터가 있는 DB는 절대 지정하지 않는다.**
  `TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/journalizer_test python -m pytest`
- **기존 데이터**: 회원 기능 이전의 `backend/data/journalizer.db`와 `uploads/`는 처음 가입한 회원에게
  한 번만 옮겨진다. 옮긴 뒤 `journalizer.db.imported` 표시 파일이 생기며 원본 파일은 지우지 않는다.

1. **회사 기준**: 회계연도, 계정(분류·기초잔액), 회사 은행 계좌, 거래처·별칭을 저장한다.
2. **자료 연결**: 영수증(PDF·이미지)을 올리면 OCR로 초안을 만든다. OCR이 실패해도 원본은
   저장되고 직접 입력으로 넘어간다. 은행 거래내역(.xlsx/.xls/.csv)은 계좌번호·은행명으로
   계좌를 판별하며, 같은 파일을 다시 올려도 중복 저장되지 않는다.
3. **분개 검토**: 원본을 보면서 정보를 입력하면 서버가 분개와 누락 항목을 계산한다.
   금액이 같은 출금 거래를 후보로 보여 주고, 검토자가 골라 연결한다. 누락 항목이 없을 때만 확정된다.
4. **계정별원장**: 확정 전표만 반영한다. 계정별 CSV, 분개장+원장 엑셀로 내보낸다.

`/wireframe`은 설계 참고용 샘플 화면으로 남겨 두었다.

### 기본 OCR 설정

PDF와 이미지(JPG/JPEG, PNG, WEBP, BMP, GIF, TIFF)는 기본적으로 Google Vision으로
처리한다. 서버를 시작하는 터미널에서 Google 인증 경로를 설정한다.
인증 파일은 저장소 외부에 보관한다.

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "F:\journalizer-510914-1900eb3aea26.json"
# 기본값이므로 다음 두 설정은 생략할 수 있다.
$env:RECEIPT_OCR_ENGINE = "vision"
$env:RECEIPT_VISION_FALLBACK = "none"
```

PDF는 원본 문서, 이미지는 원본 이미지 바이트를 `DOCUMENT_TEXT_DETECTION`에
보낸다. PDF는 암호화되지 않은 1페이지, GIF/TIFF 등 이미지도 단일 프레임만 허용한다.
글자 좌표와 필드
라벨로 날짜·금액·세액·상호·승인번호·사업자번호를 추출한다. 금액 검산과
필수 필드 확인을 통과한 전표만 반환한다. 통화는 KRW이며, 사업자번호가
없는 전표에는 해외 결제금액 확인 경고를 표시한다. 우리카드 전표의 부가세
공란은 원문 근거와 구분해 세액 0원 가정 경고를 남기고, 체크계좌 전표에는
기존 분개 엔진의 대변 계정 검토 경고를 남긴다. 품목은 추정하지 않고
계정과목은 `other`로 반환하므로 검토가 필요하다. PDF만으로 취소 상태를
확정하지 않으며, 승인번호로 사용내역과 대조해야 한다.

광주은행·우리카드 매출전표는 전용 규칙으로 처리하고, 그 외 카드 승인 영수증은
거래일시·금액·승인번호 등 명확한 라벨이 있는 경우에만 일반 규칙으로 처리한다.
일반 전표의 주소 위에서 상호를 읽거나 부가세를 0원으로 가정한 경우 경고를 남긴다.
서식·필수 값이 모호한 영수증과 전자세금계산서는 검토 오류를 반환한다.

파일 내용의 SHA-256과 OCR 설정으로 응답을 캐시한다. 기본 경로는
`backend/.cache/vision`이며 `VISION_OCR_CACHE_DIR`로 변경할 수 있다.
캐시는 전표 원문을 포함하므로 영수증과 동일한 접근 권한으로 보관한다.
API 클라이언트를 재사용하고 프로세스별 동시 호출을 4개로 제한한다.
일시적인 호출 오류만 제한적으로 재시도한다.

기본 처리에는 Anthropic API 키가 필요하지 않으며 Claude를 호출하지 않는다.
규칙에 맞지 않는 PDF·이미지는 검토 오류를 반환한다. 이 경우 Claude Haiku로
다시 처리하도록 명시적으로 허용하려면 다음을 추가한다.

```powershell
$env:RECEIPT_VISION_FALLBACK = "claude"
```

이 설정은 추가 API 호출을 발생시키며 결과에 대체 처리 경고가 붙는다.
인증·네트워크·Vision 응답 오류는 자동으로 다른 업체에 재전송하지 않는다.
`RECEIPT_OCR_ENGINE=claude`를 명시하면 기존 Claude 추출 경로를 사용할 수 있다.
기존 `RECEIPT_PDF_ENGINE` 설정도 PDF에 한해 우선 적용되므로, 전체를 Vision으로
처리할 때는 해당 설정을 제거하거나 `vision`으로 바꾼다.
`CLAUDE_RECEIPT_MODEL`로 Claude 모델을 지정할 수 있다. 명시적으로 선택한 Claude
직접 경로는 기존 Opus 4.7, 명시적으로 허용한 Vision 대체 경로는 Haiku 5.5다.

회귀 테스트는 API 호출 없이 실행한다.

```powershell
cd backend
python -m unittest discover -s tests -v
```

## 테스트 데이터 보관

`test_data`에는 영수증 하나당 PDF 한 파일(1페이지)만 보관한다.
기존 은행별 폴더를 사용하며 합본 PDF, 빈 페이지, 중복 분할본·ZIP은 보관하지 않는다.
우리은행 증빙은 `test_data/6-1우리은행 영수증/page_001.pdf`부터
`page_044.pdf`까지 44개이며, 광주은행은 기존 개별 PDF 172개다.
우리은행 분개 엑셀은 `output/woori_pdf_audit_20261009/우리은행_영수증_분개결과.xlsx`에 있다.
OCR 원문은 결과 폴더의 `ocr`에 한 번만 보관하고, 우리은행 파싱 결과는 원문 파일을 참조한다.
삭제한 합본과 빈 페이지에 대한 기존 검토 기록은 결과에 남겨 두었다.

## Build

From the repository root:

```cmd
test.cmd
```

Frontend only:

```powershell
cd frontend
npm run build
npm run lint
```
