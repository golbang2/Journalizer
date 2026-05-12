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
```

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
