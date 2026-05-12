from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

from app.models import JournalEntryDraft, ReceiptExtraction
from app.services.journalizer import build_journal_entry
from app.services.receipts import extract_receipt

app = FastAPI(title="Journalizer API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/receipts/extract", response_model=ReceiptExtraction)
async def extract(file: UploadFile = File(...)) -> ReceiptExtraction:
    content = await file.read()
    return extract_receipt(filename=file.filename or "receipt", content=content)


@app.post("/journal-entries/draft", response_model=JournalEntryDraft)
def draft_journal_entry(receipt: ReceiptExtraction) -> JournalEntryDraft:
    return build_journal_entry(receipt)
