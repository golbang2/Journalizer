"use client";

import { Fragment, useEffect, useState } from "react";
import { ArrowRight, Download, FileSpreadsheet, FileText, Loader2, Plus, Trash2 } from "lucide-react";
import { api, closingReportUrl, incomeStatementUrl, ledgerBookUrl, ledgerCsvUrl, money, workbookUrl, type LedgerView, type Reconcile, type Statement, type Statements, type VatReturn, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import MoneyInput from "./MoneyInput";
import s from "./workbench.module.css";

type Props = { workspace: Workspace; confirmed: number; account: string; onAccount: (code: string) => void; notify: Notify; onSources: () => void };

export default function LedgerStep({ workspace, confirmed, account, onAccount, notify, onSources }: Props) {
  const [view, setView] = useState<LedgerView | null>(null);
  const [version, setVersion] = useState(0);  // bumped when a VAT return changes the books

  useEffect(() => {
    if (!account) return;
    let live = true;
    setView(null);
    api.ledger(account).then(v => live && setView(v)).catch(error => live && notify((error as Error).message, "error"));
    return () => { live = false; };
  }, [account, notify, version]);

  return <>
    <Reports workspace={workspace} notify={notify} version={version}/>
    <VatReturns workspace={workspace} notify={notify} onChanged={() => setVersion(v => v + 1)}/>
    <section className={s.card}>
      <div className={s.cardHeading}><div><h2>{workspace.company || "회사"} · {workspace.year} 계정별원장</h2><p>확정한 영수증 전표와 통장 거래 전표로 만든 원장입니다 (영수증 확정 {confirmed}건). 12월 31일 손익 대체(결산)는 자동으로 붙습니다.</p></div>
        <div className={s.headingActions}>
          <a className={s.secondary} href={account ? ledgerCsvUrl(account) : undefined}><Download size={16}/> 이 계정 CSV</a>
          <a className={s.secondary} href={workbookUrl()}><FileSpreadsheet size={16}/> 분개장 + 원장</a>
          <a className={s.primary} href={ledgerBookUrl()}><FileSpreadsheet size={16}/> 계정별원장 (원장 양식)</a>
        </div></div>
      <div className={s.ledgerControls}>
        <label>조회 계정<select value={account} onChange={e => onAccount(e.target.value)}>{workspace.accounts.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label>
        {view && <><div><small>기초잔액</small><strong>{money(view.account.opening)} 원</strong></div><div><small>조회 기말잔액</small><strong>{money(view.closing)} 원</strong></div><div><small>잔액 기준</small><strong>{view.account.normal === "debit" ? "차변" : "대변"}</strong></div></>}
      </div>
      {!view ? <p className={s.helper}><Loader2 className={s.spin} size={12}/> 원장을 불러오는 중</p> :
        <div className={s.tableWrap}><table className={s.ledgerTable}><thead><tr><th>날짜</th><th>적요</th><th>거래처 코드</th><th>거래처</th><th>차변</th><th>대변</th><th>잔액</th></tr></thead><tbody>
          <tr><td>{workspace.year}-01-01</td><td>기초잔액 (입력값)</td><td>—</td><td>—</td><td>—</td><td>—</td><td>{money(view.account.opening)}</td></tr>
          {view.rows.map((l, i) => <tr key={i}><td>{l.date}</td><td>{l.memo}<small className={s.tableSub}>{l.voucher}</small></td><td>{l.vendor_code}</td><td>{l.vendor}</td><td>{l.debit ? money(l.debit) : "—"}</td><td>{l.credit ? money(l.credit) : "—"}</td><td>{money(l.balance)}</td></tr>)}
          {!view.rows.length && <tr><td colSpan={7} className={s.empty}>이 계정에 확정된 분개가 없습니다. 분개 검토에서 내용을 확정하세요.</td></tr>}
        </tbody><tfoot><tr><td colSpan={4}>조회 전표 합계</td><td>{money(view.debit_total)}</td><td>{money(view.credit_total)}</td><td>{money(view.closing)}</td></tr></tfoot></table></div>}
      <p className={s.helper}>원장 양식의 ‘코드’는 거래처 코드입니다. 계정 코드는 위 조회 계정에 표시합니다. 기초잔액은 회사 기준에서 입력합니다.</p>
    </section>
    <section className={s.card}><h2>완성된 원장을 위해 더 필요한 자료</h2><div className={s.completionGrid}>{["부가세 신고 상계 (부가세대급금·예수금)", "기말 부가세대급금 정리", "기초잔액·전기 이월 채무"].map(t => <div key={t}><FileText size={19}/><b>{t}</b><small>아직 자동으로 만들지 않는 전표</small></div>)}</div>
      <button className={s.textButton} onClick={onSources}>자료 연결로 이동 <ArrowRight size={14}/></button></section>
  </>;
}

type ReportTab = "income_statement" | "balance_sheet" | "deficit";

/** 결산보고서·손익계산서·계정별원장 downloads, with the statements previewed on screen. */
function Reports({ workspace, notify, version }: { workspace: Workspace; notify: Notify; version: number }) {
  const [data, setData] = useState<Statements | null>(null);
  const [tab, setTab] = useState<ReportTab>("income_statement");

  useEffect(() => {
    let live = true;
    api.statements().then(d => live && setData(d)).catch(error => live && notify((error as Error).message, "error"));
    return () => { live = false; };
  }, [notify, version]);

  const names: [ReportTab, string][] = [["income_statement", "손익계산서"], ["balance_sheet", "재무상태표"], ["deficit", data?.deficit.title.split(" ")[0] ?? "결손금처리계산서"]];
  return <section className={s.card}>
    <div className={s.cardHeading}><div><h2>{workspace.year} 결산 보고서</h2><p>확정한 전표로 만든 재무제표입니다. 금액은 계정별원장과 같은 전표에서 계산하고, 원 단위입니다.</p></div>
      <div className={s.headingActions}>
        <a className={s.primary} href={closingReportUrl()}><FileSpreadsheet size={16}/> 결산보고서</a>
        <a className={s.secondary} href={incomeStatementUrl()}><FileSpreadsheet size={16}/> 손익계산서</a>
        <a className={s.secondary} href={ledgerBookUrl()}><FileSpreadsheet size={16}/> 계정별원장</a>
      </div></div>
    <div className={s.tabs}>{names.map(([v, label]) => <button key={v} className={tab === v ? s.tabActive : ""} onClick={() => setTab(v)}>{label}</button>)}</div>
    {!data ? <p className={s.helper}><Loader2 className={s.spin} size={12}/> 재무제표를 만드는 중</p> : <StatementTable st={data[tab]}/>}
    <p className={s.helper}>결산보고서 파일에는 재무상태표, 손익계산서, {data?.deficit.title.split(" ")[0] ?? "결손금처리계산서"}, 합계잔액시산표가 시트별로 들어 있습니다.</p>
  </section>;
}

function StatementTable({ st }: { st: Statement }) {
  const amount = (v: number) => v < 0 ? `(${money(-v)})` : money(v);
  return <div className={s.tableWrap}>
    <p className={s.statementTitle}>{st.title}</p>
    <table className={s.statementTable}><thead><tr><th>과목</th><th>금액</th><th/></tr></thead><tbody>
      {st.items.map((i, n) => i.level === 1
        ? <tr key={n}><td className={s.statementAccount}>{i.label}</td><td>{amount(i.amount)}</td><td/></tr>
        : <tr key={n} className={i.level === 0 ? s.statementTotal : s.statementSub}><td>{i.label}</td><td/><td>{amount(i.amount)}</td></tr>)}
    </tbody></table>
    {st.checks.map(c => <p key={c} className={s.checkWarn}>{c}</p>)}
  </div>;
}

const PERIODS: [string, string, string, string][] = [
  // label, start (MM-DD), end, usual filing date (next year when it starts with +)
  ["1기 (1~6월)", "01-01", "06-30", "07-25"],
  ["2기 (7~12월)", "07-01", "12-31", "+01-25"],
  ["1기 예정 (1~3월)", "01-01", "03-31", "04-25"],
  ["1기 확정 (4~6월)", "04-01", "06-30", "07-25"],
  ["2기 예정 (7~9월)", "07-01", "09-30", "10-25"],
  ["2기 확정 (10~12월)", "10-01", "12-31", "+01-25"],
];

/** 부가세 신고 정리: offsets 부가세예수금 and 부가세대급금 at the filing and leaves the refund or the tax due. */
function VatReturns({ workspace, notify, onChanged }: { workspace: Workspace; notify: Notify; onChanged: () => void }) {
  const [items, setItems] = useState<VatReturn[]>([]);
  const [period, setPeriod] = useState(0);
  const [form, setForm] = useState({ filed: "", output_tax: 0, input_tax: 0, nondeductible_account: "83000" });
  const [busy, setBusy] = useState(false);
  const year = Number(workspace.year);
  const [label, startDay, endDay, filedDay] = PERIODS[period];
  const start = `${year}-${startDay}`, end = `${year}-${endDay}`;
  const defaultFiled = filedDay.startsWith("+") ? `${year + 1}-${filedDay.slice(1)}` : `${year}-${filedDay}`;
  const expenses = workspace.accounts.filter(a => a.kind === "expense");

  useEffect(() => { api.vatReturns().then(setItems).catch(() => undefined); }, []);

  const loadBooked = async () => {
    try {
      const booked = await api.bookedVat(start, end);
      setForm(f => ({ ...f, ...booked }));
      notify(`${label} 장부 금액: 매출세액 ${money(booked.output_tax)}원, 매입세액 ${money(booked.input_tax)}원. 실제 신고서와 다르면 신고서 금액으로 고치세요.`);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  const save = async () => {
    setBusy(true);
    try {
      await api.addVatReturn({ period_start: start, period_end: end, filed: form.filed || defaultFiled, output_tax: form.output_tax, input_tax: form.input_tax, nondeductible_account: form.nondeductible_account, note: label });
      setItems(await api.vatReturns());
      onChanged();
      const due = form.output_tax - form.input_tax;
      notify(`${label} 신고를 정리했습니다. ${due < 0 ? `환급받을 세액 ${money(-due)}원은 미수금으로, 세무서 입금이 들어오면 자동으로 회수합니다.` : `납부할 세액 ${money(due)}원은 미지급세금으로 잡았습니다.`}`);
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async (id: string) => {
    if (!window.confirm("이 부가세 신고 정리를 장부에서 뺄까요?")) return;
    await api.deleteVatReturn(id).catch(e => notify((e as Error).message, "error"));
    setItems(await api.vatReturns());
    onChanged();
  };
  const due = form.output_tax - form.input_tax;
  const [open, setOpen] = useState("");
  const [rec, setRec] = useState<Reconcile | null>(null);
  const [uploading, setUploading] = useState("");
  const toggle = async (id: string) => {
    if (open === id) { setOpen(""); return; }
    try {
      setRec(await api.reconcileVat(id));
      setOpen(id);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };
  const uploadSchedule = async (id: string, file: File) => {
    setUploading(id);
    try {
      const result = await api.uploadVatSchedule(id, file);
      setItems(await api.vatReturns());
      setRec(result);
      setOpen(id);
      onChanged();
      notify(`부속서류 ${result.rows.length}개 거래처를 대조했습니다: 일치 ${result.matched}곳.${result.fixed?.length ? ` 자동 추출이 부가세를 0원으로 읽은 영수증 ${result.fixed.length}건을 신고서 금액으로 고쳤습니다.` : ""}`);
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setUploading("");
    }
  };

  return <section className={s.card}>
    <div className={s.cardHeading}><div><h2>부가세 신고 정리</h2><p>신고한 매출세액·매입세액을 넣으면 신고일에 부가세예수금과 부가세대급금을 상계하고, 차액을 미수금(환급) 또는 미지급세금(납부)으로 남깁니다. 장부에 잡힌 매입세액이 신고액과 다르면 그 차이는 비용으로 대체(또는 환입)해 부가세대급금이 신고와 맞게 됩니다.</p></div></div>
    {items.length > 0 && <div className={s.tableWrap}><table><thead><tr><th>기간</th><th>신고일</th><th>매출세액</th><th>매입세액</th><th>환급·납부</th><th>부속서류 대조</th><th/></tr></thead><tbody>
      {items.map(i => { const d = i.output_tax - i.input_tax; return <Fragment key={i.id}><tr><td>{i.note || `${i.period_start} ~ ${i.period_end}`}<small className={s.tableSub}>{i.period_start} ~ {i.period_end}</small></td><td>{i.filed}</td>
        <td className={s.num}>{money(i.output_tax)}</td><td className={s.num}>{money(i.input_tax)}</td><td className={s.num}>{d < 0 ? `환급 ${money(-d)}` : `납부 ${money(d)}`}</td>
        <td><label className={s.linkButton}>{uploading === i.id ? "읽는 중…" : i.schedule_file ? "다시 올리기" : "수령명세서·합계표 올리기"}
          <input className={s.hiddenInput} type="file" accept=".pdf,image/*,.xlsx,.xls,.csv" disabled={!!uploading} onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) uploadSchedule(i.id, f); }}/></label>
          {i.schedule_file && <button className={s.linkButton} onClick={() => toggle(i.id)}>{open === i.id ? "대조 닫기" : "대조 보기"}</button>}</td>
        <td><button className={s.iconButton} aria-label={`${i.note} 신고 정리 삭제`} onClick={() => remove(i.id)}><Trash2 size={14}/></button></td></tr>
        {open === i.id && rec && <tr className={s.detailRow}><td colSpan={7}><ReconcileTable rec={rec}/></td></tr>}</Fragment>; })}
    </tbody></table></div>}
    <div className={s.fields}>
      <label>신고 기간<select value={period} onChange={e => { setPeriod(Number(e.target.value)); setForm(f => ({ ...f, filed: "" })); }}>{PERIODS.map(([l], i) => <option key={l} value={i}>{year}년 {l}</option>)}</select></label>
      <label>신고일 (정리 전표 날짜)<input type="date" value={form.filed || defaultFiled} onChange={e => setForm(f => ({ ...f, filed: e.target.value }))}/></label>
      <label>매출세액 (원)<MoneyInput value={form.output_tax} onChange={v => setForm(f => ({ ...f, output_tax: v }))}/></label>
      <label>공제받은 매입세액 (원)<MoneyInput value={form.input_tax} onChange={v => setForm(f => ({ ...f, input_tax: v }))}/></label>
      <label>장부와 신고 매입세액 차이 대체 계정<select value={form.nondeductible_account} onChange={e => setForm(f => ({ ...f, nondeductible_account: e.target.value }))}>{expenses.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label>
      <div className={s.precedentBox}><small>신고 결과</small><b>{due < 0 ? `환급 ${money(-due)}원 → 미수금` : due > 0 ? `납부 ${money(due)}원 → 미지급세금` : "납부·환급 없음"}</b></div>
    </div>
    <div className={s.bulkActions}>
      <button className={s.secondary} onClick={loadBooked}>장부 금액 불러오기</button>
      <button className={s.primary} disabled={busy || !(form.output_tax || form.input_tax)} onClick={save}><Plus size={14}/> 신고 정리 전표 만들기</button>
    </div>
  </section>;
}

/** The return's 부속서류 against the books, per supplier. */
function ReconcileTable({ rec }: { rec: Reconcile }) {
  const tone = (status: string) => status === "일치" ? s.checkOk : s.checkWarn;
  return <div className={s.reconcile}>
    <p>신고서 부속서류 세액 {money(rec.filed_total)}원 · 장부 공제 세액 {money(rec.book_total)}원 · 신고서 매입세액 {money(rec.return_input_tax)}원
      {rec.filed_total !== rec.return_input_tax && <b className={s.checkWarn}> (부속서류 합계가 신고 매입세액과 다릅니다. 다른 부속서류도 올리세요.)</b>}</p>
    <table className={s.miniTable}><thead><tr><th>사업자번호</th><th>거래처</th><th>신고 건수·세액</th><th>장부 건수·세액</th><th>차이</th><th>상태</th></tr></thead><tbody>
      {rec.rows.map(r => <tr key={r.business_number}><td className={s.mono}>{r.business_number}</td><td>{r.name}</td>
        <td className={s.num}>{r.filed_count}건 · {money(r.filed_tax)}</td><td className={s.num}>{r.book_count}건 · {money(r.book_tax)}</td>
        <td className={s.num}>{r.difference ? money(r.difference) : ""}</td>
        <td><small className={tone(r.status)}>{r.status}{r.status === "장부 누락" && r.zero_vat_receipts.length ? ` · 부가세 0원 영수증 ${r.zero_vat_receipts.join(", ")}` : ""}</small></td></tr>)}
    </tbody></table>
  </div>;
}
