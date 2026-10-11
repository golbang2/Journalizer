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
  이메일이 같은 비밀번호 계정과 구글 계정은 자동으로 연결하지 않는다. 충돌하면 구글 로그인은
  거절되며 기존 가입 방식으로 로그인하거나 관리자에게 소유권 확인을 요청해야 한다.
  이전 버전에서 자동 연결된 계정은 서버가 DB를 초기화할 때 기존 비밀번호와 모든 세션을
  폐기하고 구글 로그인만 허용한다. 회사·영수증 자료는 보존된다. 이 보안 변경을 배포할 때는
  기존 서버 프로세스를 모두 종료하고 새 버전으로 재시작해야 한다.
- **프록시**: 브라우저는 프론트엔드(3000)의 `/api/*`만 호출하고, Next.js가 이를 백엔드로 넘긴다.
  백엔드 주소가 8000이 아니면 프론트엔드를 켤 때 `BACKEND_URL`을 지정한다.
- **PostgreSQL로 테스트**: `TEST_DATABASE_URL`을 지정하면 백엔드 테스트가 그 DB에서 돈다.
  테스트마다 표를 지우고 새로 만들므로 **실데이터가 있는 DB는 절대 지정하지 않는다.**
  `TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/journalizer_test python -m pytest`
- **기존 데이터**: 회원가입·구글 로그인으로 이전 자료를 자동 이관하지 않는다. 관리자가 소유권을
  확인한 기존 회원의 **ID와 이메일을 함께 지정**하여 빈 회사로만 이관한다. 가입 이메일은
  소유권이 인증된 값이 아니므로 이메일 일치만으로 대상자를 판단하면 안 된다.
  이관 전 DB를 백업하고 웹 서버를 중지한 뒤 `backend` 폴더에서 아래 명령을 실행한다.
  `--data-dir`는 `journalizer.db`와 `uploads/`가 있는 폴더이고, 대상 DB는 `DATABASE_URL`
  또는 기본 로컬 DB다. 이관 뒤 `journalizer.db.imported` 표시 파일이 생기며 원본은 보존된다.

```powershell
python -m app.services.legacy --owner-id 123 --expected-email owner@example.com --data-dir "F:\Journalizer\backend\data"
```

1. **회사 기준**: 회계연도, 계정(분류·기초잔액), 회사 은행 계좌, 거래처·별칭을 저장한다.
2. **자료 연결**: 영수증(PDF·이미지)을 올리면 OCR로 초안을 만든다. OCR이 실패해도 원본은
   저장되고 직접 입력으로 넘어간다. 은행 거래내역(.xlsx/.xls/.csv)은 계좌번호·은행명으로
   계좌를 판별하며, 같은 파일을 다시 올려도 중복 저장되지 않는다.
3. **분개 검토**: 원본을 보면서 정보를 입력하면 서버가 분개와 누락 항목을 계산한다.
   금액이 같은 출금 거래를 후보로 보여 주고, 검토자가 골라 연결한다. 누락 항목이 없을 때만 확정된다.
4. **계정별원장**: 확정 전표만 반영한다. 계정별 CSV, 분개장+원장 엑셀로 내보낸다.

`/wireframe`은 설계 참고용 샘플 화면으로 남겨 두었다.

### 홈택스 거래별 대조

**자료 연결 → 홈택스 거래별 대조**에서 사업용·법인카드 매입내역과 매입 전자세금계산서의
거래별 상세 파일(`.xlsx`, `.xls`, `.csv`)을 종류별로 올린다. 홈택스 자동 로그인/API 수집은
이번 구현에 포함하지 않는다. 파일은 AI에 보내지 않고 열 이름과 금액 검산으로 읽는다.

- 카드: 거래일자(또는 승인일자), 가맹점 사업자등록번호, 가맹점명, 공급가액, 부가세,
  합계금액이 필요하다. 승인번호, 카드번호, 공제여부, 거래구분, 봉사료도 읽는다.
- 매입 세금계산서: 작성일자, 공급자등록번호, 공급자 상호, 공급가액, 세액, 합계금액이
  필요하다. 승인번호, 공급받는자등록번호, 수정사유도 읽는다. 공급자·공급받는자 뒤에
  각각 상호 열이 있는 형식도 구분한다. 회사 프로필에 사업자번호가 있으면 수취인 번호를 검증한다.
- CSV는 UTF-8 및 CP949를 지원한다. 날짜는 `YYYY-MM-DD`, `YYYY.MM.DD`, `YYYYMMDD`
  등 날짜 문자열을 지원한다. 엑셀 일련번호 날짜, 마스킹된 사업자번호, 숫자 지수 표기의
  승인번호, 암호화 파일·HTML을 `.xls`로 저장한 파일은 지원하지 않는다. 식별자는 앞자리 0을
  보존하도록 텍스트로 저장한다. 알 수 없는 열 구조나 잘못된 상세 행이 있으면 파일 전체를
  거절하고 시트·행 위치를 표시한다. 실제 홈택스 내보내기 서식은 샘플 파일로 추가 검증이 필요하다.

원본 파일과 행 위치는 로그인한 회사별로 보존하며 원본 다운로드·자료 삭제가 가능하다.
같은 파일의 재업로드는 추가 저장하지 않는다. 승인번호 등 식별자가 같은 거래는 여러 파일의
출처를 모아 보여주고, 금액·공제 표시 등이 달라지면 충돌로 표시한다. 승인번호가 없는
동일 금액 거래는 서로 다른 거래일 수 있으므로 자동으로 합치지 않는다.

사업자번호·증빙 종류·승인번호·날짜·금액이 모두 맞는 단일 영수증을 자동 대조한다.
그 밖에는 연결 후보를 표시하며 사용자가 연결·해제할 수 있다. 세액 차이, 공제 판단 차이,
누락 영수증, 복수 후보, 카드와 세금계산서의 이중 계상 가능성, 취소·수정 거래를 구분한다.
조회기간과 검색·검토 필요 필터로 대상을 좁힌 뒤 영수증 검토 화면으로 이동한다.
홈택스에 대응 자료가 없는 기존 영수증도 보여주지만, 자료의 조회기간·등록 범위가 다를 수
있으므로 이것만으로 불공제를 확정하지 않는다.

자료 업로드·연결은 전표나 공제세액을 자동 변경하지 않는다. 원문 세액, 홈택스 공제 표시,
영수증에서 검토한 공제세액을 구분해서 보여준다. 신고서 부속서류의 거래처별 합계 차이로
개별 영수증의 세액을 보정하던 동작도 제거했다. `일치`는 자료 대조 결과이며 사업 관련성·공제
요건의 확정이 아니다. 취소·수정 거래는 원거래와 귀속기간을 별도로 확인한다.

관련 회귀 테스트: `cd backend` 후 `.venv\Scripts\python -m pytest tests/test_hometax.py -q`.
실제 고객 자료 대신 합성 CSV·엑셀과 임시 DB를 사용한다.

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

실제 거래자료와 영수증은 로컬 `test_data/`에만 보관하며 Git에 올리지 않는다.
`output/`, `tmp/`, 로컬 환경설정과 DB도 업로드 대상에서 제외한다.
개인 샘플이 없는 체크아웃에서는 해당 자료에 의존하는 테스트만 건너뛴다.

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
