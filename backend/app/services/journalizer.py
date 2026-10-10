from decimal import Decimal

from app.models import JournalEntryDraft, JournalLine, ReceiptExtraction


EXPENSE_ACCOUNT_BY_CATEGORY = {
    "meals": ("81300", "Meals and Entertainment"),
    "transport": ("81200", "Travel and Transportation"),
    "office": ("81100", "Office Supplies"),
}


def build_journal_entry(receipt: ReceiptExtraction) -> JournalEntryDraft:
    expense_code, expense_name = EXPENSE_ACCOUNT_BY_CATEGORY.get(
        receipt.category_hint, ("81900", "Miscellaneous Expense")
    )

    lines = [
        JournalLine(
            side="debit",
            account_code=expense_code,
            account_name=expense_name,
            amount=receipt.subtotal,
            memo=receipt.merchant,
        )
    ]

    if receipt.tax > Decimal("0"):
        lines.append(
            JournalLine(
                side="debit",
                account_code="13500",
                account_name="VAT Receivable",
                amount=receipt.tax,
                memo=f"Tax on {receipt.merchant}",
            )
        )

    lines.append(
        JournalLine(
            side="credit",
            account_code="21000",
            account_name="Corporate Card Payable",
            amount=receipt.total,
            memo=receipt.payment_method,
        )
    )

    debit_total = sum(line.amount for line in lines if line.side == "debit")
    credit_total = sum(line.amount for line in lines if line.side == "credit")
    warnings = list(receipt.warnings)
    if debit_total != credit_total:
        warnings.append("Debit and credit totals do not match.")
    if receipt.confidence < 0.75:
        warnings.append("Extraction confidence is low. Please review before posting.")

    return JournalEntryDraft(
        receipt=receipt,
        description=f"{receipt.purchased_at} {receipt.merchant} receipt",
        lines=lines,
        warnings=warnings,
    )
