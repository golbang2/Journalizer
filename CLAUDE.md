# CLAUDE.md

This file gives Claude Code project-specific guidance for working on Journalizer.

## Product Goal

Journalizer is a receipt-to-journal-entry automation service.

The core user flow is:

1. Upload a receipt image or PDF.
2. Extract structured receipt data.
3. Recommend accounting categories and journal lines.
4. Let a human review and edit the draft.
5. Confirm, store, and export the journal entry.

The product should optimize for accountant trust. Automation should be explainable, editable, and reversible.

## Architecture Direction

- Frontend: Next.js, TypeScript, React.
- Backend: FastAPI, Pydantic.
- API contract: JSON over HTTP.
- Receipt upload: multipart form data.
- Journal draft generation: structured receipt JSON in, structured journal draft JSON out.

## Build and Test Commands

From the repository root:

```cmd
test.cmd
```

Frontend:

```powershell
cd frontend
npm install
npm run build
npm run lint
npm run dev
```

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m compileall app
uvicorn app.main:app --reload --port 8000
```

Tests are not configured yet. Use these when test suites are added:

```powershell
pytest
npm test
```
