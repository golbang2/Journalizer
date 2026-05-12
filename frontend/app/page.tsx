"use client";

import { Check, FileUp, Loader2, RefreshCcw } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

type ReceiptExtraction = {
  merchant: string;
  purchased_at: string;
  currency: string;
  subtotal: string;
  tax: string;
  total: string;
  payment_method: string;
  category_hint: string;
  confidence: number;
  line_items: { description: string; quantity: string; amount: string }[];
};

type JournalEntryDraft = {
  receipt: ReceiptExtraction;
  description: string;
  lines: {
    side: "debit" | "credit";
    account_code: string;
    account_name: string;
    amount: string;
    memo: string;
  }[];
  warnings: string[];
};

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function money(value: string, currency: string) {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency,
    maximumFractionDigits: 0
  }).format(Number(value));
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<JournalEntryDraft | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const totals = useMemo(() => {
    if (!draft) return { debit: 0, credit: 0 };
    return draft.lines.reduce(
      (sum, line) => {
        sum[line.side] += Number(line.amount);
        return sum;
      },
      { debit: 0, credit: 0 }
    );
  }, [draft]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;

    setIsLoading(true);
    setError(null);

    try {
      const form = new FormData();
      form.append("file", file);

      const extractResponse = await fetch(`${apiBase}/receipts/extract`, {
        method: "POST",
        body: form
      });
      if (!extractResponse.ok) throw new Error("영수증 추출에 실패했습니다.");
      const receipt = (await extractResponse.json()) as ReceiptExtraction;

      const draftResponse = await fetch(`${apiBase}/journal-entries/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(receipt)
      });
      if (!draftResponse.ok) throw new Error("분개 초안 생성에 실패했습니다.");

      setDraft((await draftResponse.json()) as JournalEntryDraft);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "처리 중 오류가 발생했습니다.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">Journalizer</div>
        <div className="status">영수증 업로드, 자동 추출, 분개 검토</div>
      </header>

      <div className="workspace">
        <section className="panel">
          <h2>영수증 업로드</h2>
          <form className="uploadBox" onSubmit={handleSubmit}>
            <FileUp size={36} color="#0f766e" />
            <input
              accept="image/*,.pdf"
              aria-label="영수증 파일"
              type="file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <button className="primary" disabled={!file || isLoading} type="submit">
              {isLoading ? <Loader2 size={18} /> : <FileUp size={18} />}
              {isLoading ? "처리 중" : "분개 초안 만들기"}
            </button>
          </form>
          {error ? <div className="warning">{error}</div> : null}
        </section>

        <section className="panel">
          <h2>분개 초안</h2>
          {draft ? (
            <>
              <div className="summaryGrid">
                <div className="metric">
                  <span>거래처</span>
                  <strong>{draft.receipt.merchant}</strong>
                </div>
                <div className="metric">
                  <span>거래일</span>
                  <strong>{draft.receipt.purchased_at}</strong>
                </div>
                <div className="metric">
                  <span>합계</span>
                  <strong>{money(draft.receipt.total, draft.receipt.currency)}</strong>
                </div>
                <div className="metric">
                  <span>신뢰도</span>
                  <strong>{Math.round(draft.receipt.confidence * 100)}%</strong>
                </div>
              </div>

              {draft.warnings.map((warning) => (
                <div className="warning" key={warning}>
                  {warning}
                </div>
              ))}

              <table className="table">
                <thead>
                  <tr>
                    <th>차/대</th>
                    <th>계정코드</th>
                    <th>계정과목</th>
                    <th>적요</th>
                    <th className="amount">금액</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.lines.map((line) => (
                    <tr key={`${line.side}-${line.account_code}-${line.amount}`}>
                      <td>{line.side === "debit" ? "차변" : "대변"}</td>
                      <td>{line.account_code}</td>
                      <td>{line.account_name}</td>
                      <td>{line.memo}</td>
                      <td className="amount">{money(line.amount, draft.receipt.currency)}</td>
                    </tr>
                  ))}
                  <tr>
                    <td colSpan={4}>합계</td>
                    <td className="amount">
                      차 {money(String(totals.debit), draft.receipt.currency)} / 대{" "}
                      {money(String(totals.credit), draft.receipt.currency)}
                    </td>
                  </tr>
                </tbody>
              </table>

              <div className="actions">
                <button className="secondary" type="button">
                  <RefreshCcw size={16} /> 재추천
                </button>
                <button className="primary" type="button">
                  <Check size={16} /> 확정
                </button>
              </div>
            </>
          ) : (
            <div className="status">왼쪽에서 영수증 파일을 선택하면 분개 초안이 표시됩니다.</div>
          )}
        </section>
      </div>
    </main>
  );
}
