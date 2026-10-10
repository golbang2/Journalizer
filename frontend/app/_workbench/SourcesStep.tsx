"use client";

import { useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, FileText, Landmark, Loader2, Trash2, TriangleAlert } from "lucide-react";
import { api, money, type BankTransaction, type Receipt, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

type Progress = { name: string; state: "working" | "done" | "warn" | "failed"; detail: string };
type Props = {
  workspace: Workspace; transactions: BankTransaction[]; onReceipt: (r: Receipt) => void;
  onTransactionsChanged: () => Promise<void>; notify: Notify; onNext: () => void;
};

const later = [
  ["카드 이용·청구 내역", "승인번호, 취소 여부, 청구·결제일"],
  ["세금계산서·정산서", "공급가액, 세액, 플랫폼 수수료"],
  ["기초잔액·기존 채무", "전기말 잔액과 미결제 전표"],
  ["매출·자본금·결산 자료", "영수증 외 거래와 기말 조정"],
];

export default function SourcesStep({ workspace, transactions, onReceipt, onTransactionsChanged, notify, onNext }: Props) {
  const [receiptProgress, setReceiptProgress] = useState<Progress[]>([]);
  const [statementProgress, setStatementProgress] = useState<Progress[]>([]);
  const [bank, setBank] = useState("");
  const busy = [...receiptProgress, ...statementProgress].some(p => p.state === "working");

  const run = async (files: File[], setProgress: typeof setReceiptProgress, task: (file: File) => Promise<Omit<Progress, "name">>) => {
    setProgress(files.map(f => ({ name: f.name, state: "working", detail: "대기 중" })));
    // Sequential on purpose: OCR calls are slow and rate-limited.
    for (const [i, file] of files.entries()) {
      let result: Omit<Progress, "name">;
      try {
        result = await task(file);
      } catch (error) {
        result = { state: "failed", detail: (error as Error).message };
      }
      setProgress(list => list.map((p, j) => j === i ? { name: p.name, ...result } : p));
    }
  };

  const uploadReceipts = (files: File[]) => run(files, setReceiptProgress, async file => {
    const r = await api.uploadReceipt(file);
    onReceipt(r);
    if (r.extraction_error) return { state: "warn", detail: `${r.id} · 직접 입력 필요 — ${r.extraction_error}` };
    if (r.duplicate_of) return { state: "warn", detail: `${r.id} · ${r.duplicate_of}와 같은 영수증입니다 (중복)` };
    return { state: "done", detail: `${r.id} · ${r.merchant} · ${money(r.total)}원 · 자동 입력 ${Object.keys(r.autofilled).length}개` };
  });

  const importStatements = async (files: File[]) => {
    await run(files, setStatementProgress, async file => {
      const result = await api.importStatement(file, bank);
      return { state: "done", detail: `${result.bank_name} · ${result.first} ~ ${result.last} · 새 거래 ${result.added}건${result.duplicates ? ` (중복 ${result.duplicates}건 제외)` : ""}${result.autofilled ? ` · 영수증 ${result.autofilled}건 자동 입력` : ""}` };
    });
    await onTransactionsChanged();
  };

  const summary = useMemo(() => workspace.banks.map(b => {
    const rows = transactions.filter(t => t.bank === b.code);
    const dates = rows.map(t => t.posted_at.slice(0, 10)).sort();
    return { bank: b, count: rows.length, out: rows.reduce((sum, t) => sum + t.withdrawal, 0), first: dates[0], last: dates[dates.length - 1] };
  }).filter(x => x.count), [workspace.banks, transactions]);

  const removeBank = async (code: string, name: string) => {
    if (!window.confirm(`${name} 거래 내역을 모두 삭제할까요? 다시 가져올 수 있습니다.`)) return;
    try {
      const { deleted } = await api.deleteTransactions(code);
      await onTransactionsChanged();
      notify(`${name} 거래 ${deleted}건을 삭제했습니다.`);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  return <>
    <div className={s.sourceGrid}>
      <section className={s.card}>
        <div className={s.sourceIcon}><FileText size={22}/></div><h2>영수증</h2><p>PDF·이미지 · 한 파일에 영수증 하나. 올리면 바로 자동 추출합니다.</p>
        <label className={s.upload}>{busy ? "처리 중…" : "파일 선택 (여러 개 가능)"}<input aria-label="영수증 파일 선택" type="file" multiple disabled={busy} accept="image/*,.pdf" onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length) uploadReceipts(files); }}/></label>
        <ProgressList items={receiptProgress}/>
      </section>
      <section className={s.card}>
        <div className={s.sourceIcon}><Landmark size={22}/></div><h2>은행 거래 내역</h2><p>은행에서 내려받은 거래내역조회 엑셀(.xlsx, .xls) 또는 CSV. 같은 파일을 다시 올려도 중복되지 않습니다.</p>
        <label className={s.compactLabel}>계좌<select value={bank} onChange={e => setBank(e.target.value)}><option value="">파일에서 자동 판별</option>{workspace.banks.map(b => <option key={b.code} value={b.code}>{b.name} ({b.code})</option>)}</select></label>
        <label className={s.upload}>{busy ? "처리 중…" : "파일 선택"}<input aria-label="은행 거래 내역 파일 선택" type="file" multiple disabled={busy} accept=".xlsx,.xls,.csv" onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length) importStatements(files); }}/></label>
        <ProgressList items={statementProgress}/>
      </section>
      {later.map(([title, sub]) => <section className={`${s.card} ${s.disabledCard}`} key={title}>
        <div className={s.sourceIcon}><FileText size={22}/></div><h2>{title}</h2><p>{sub}</p><span className={s.soon}>준비 중</span>
      </section>)}
    </div>

    <section className={s.card}><div className={s.cardHeading}><div><h2>가져온 은행 거래</h2><p>분개 검토에서 영수증 금액·날짜·거래처로 출금 후보를 찾아 보여 줍니다.</p></div></div>
      {summary.length ? <div className={s.tableWrap}><table><thead><tr><th>계좌</th><th>기간</th><th>거래 수</th><th>출금 합계</th><th/></tr></thead><tbody>
        {summary.map(x => <tr key={x.bank.code}><td>{x.bank.name} <small className={s.tableSub}>{x.bank.code}</small></td><td>{x.first} ~ {x.last}</td><td>{x.count}건</td><td>{money(x.out)}원</td>
          <td><button className={s.iconButton} aria-label={`${x.bank.name} 거래 삭제`} onClick={() => removeBank(x.bank.code, x.bank.name)}><Trash2 size={14}/></button></td></tr>)}
      </tbody></table></div> : <p className={s.helper}>아직 가져온 거래 내역이 없습니다. 거래 내역 없이도 지급 정보를 직접 입력해 검토할 수 있습니다.</p>}
      <div className={s.connection}><span>영수증<br/><b>일자 · 상호 · 금액 · 승인번호</b></span><ArrowRight size={20}/><span>거래 내역<br/><b>출금일 · 수취인 · 금액</b></span><ArrowRight size={20}/><span>검토자가 연결<br/><b>후보 중 선택 · 중복 연결 경고</b></span></div>
    </section>
    <div className={s.next}><button className={s.primary} disabled={busy} onClick={onNext}>분개 검토로 이동 <ArrowRight size={16}/></button></div>
  </>;
}

function ProgressList({ items }: { items: Progress[] }) {
  return <>{items.map((p, i) => <div className={s.fileName} key={`${p.name}-${i}`}>
    <span className={s.progressIcon}>{p.state === "working" ? <Loader2 className={s.spin} size={13}/> : p.state === "done" ? <CheckCircle2 size={13} color="#478267"/> : <TriangleAlert size={13} color={p.state === "warn" ? "#9b7c43" : "#b54747"}/>}</span>
    {p.name}<small className={p.state === "failed" ? s.failText : undefined}>{p.detail}</small>
  </div>)}</>;
}
