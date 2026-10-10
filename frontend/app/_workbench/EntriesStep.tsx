"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, ChevronDown, ChevronRight, Link2, Loader2, RefreshCw, Sparkles, Undo2 } from "lucide-react";
import { api, autoMessage, isAi, money, type Entry, type EntryReview, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import MoneyInput from "./MoneyInput";
import s from "./workbench.module.css";

type Filter = "all" | "person" | "ai" | "missing" | "receipt" | "confirmed";
type Props = { workspace: Workspace; notify: Notify; onReceipts: () => void; onLedger: () => void };

const LOW_CONFIDENCE = 0.6;
const open = (e: Entry) => !e.confirmed && !e.covered_by && !e.skip;
const hasAi = (e: Entry) => open(e) && Object.values(e.autofilled).some(isAi);
/** A transfer with no receipt: only a person can say what it was for, so it is never posted automatically. */
const mustCheck = (e: Entry) => open(e) && e.requires_person;

/** Every bank row as a journal entry. The model's recommendation is highlighted until a person confirms it. */
export default function EntriesStep({ workspace, notify, onReceipts, onLedger }: Props) {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState("");
  const [busy, setBusy] = useState("");

  const opened = useRef(false);
  const load = useCallback(async () => {
    try {
      const fresh = await api.entries();
      setEntries(fresh);
      // Open on the transfers that wait for a person, the first time there are any.
      if (!opened.current && fresh.some(mustCheck)) setFilter("person");
      opened.current = true;
    } catch (error) {
      notify((error as Error).message, "error");
    }
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  const year = (e: Entry) => e.transaction.posted_at.startsWith(workspace.year);
  const list = useMemo(() => (entries ?? []).filter(year), [entries, workspace.year]); // eslint-disable-line react-hooks/exhaustive-deps
  const counts = useMemo(() => ({
    all: list.length,
    person: list.filter(mustCheck).length,
    ai: list.filter(hasAi).length,
    missing: list.filter(e => open(e) && e.missing.length > 0).length,
    receipt: list.filter(e => e.covered_by).length,
    confirmed: list.filter(e => e.confirmed).length,
  }), [list]);
  const shown = list.filter(e => filter === "all" ? true : filter === "person" ? mustCheck(e) : filter === "ai" ? hasAi(e) : filter === "missing" ? open(e) && e.missing.length > 0
    : filter === "receipt" ? !!e.covered_by : e.confirmed);
  const confirmable = shown.filter(e => open(e) && e.missing.length === 0 && e.balanced);

  const accounts = workspace.accounts.filter(a => !workspace.banks.some(b => b.account === a.code) && a.code !== "40000");
  const name = (code: string) => workspace.accounts.find(a => a.code === code)?.name ?? code;

  const replace = (updated: Entry) => setEntries(all => all?.map(e => e.tx_id === updated.tx_id ? updated : e) ?? all);

  const run = async (label: string, task: () => Promise<void>) => {
    setBusy(label);
    try {
      await task();
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setBusy("");
    }
  };

  const update = (e: Entry, changes: Partial<EntryReview>) => run(e.tx_id, async () => replace(await api.updateEntry(e.tx_id, changes)));

  const judge = (ids: string[] = []) => run("judge", async () => {
    const result = ids.length ? await api.judge(ids) : await api.autoProcess();
    await load();
    notify(autoMessage(result), result.error && !result.judged && !result.auto?.entries ? "error" : "success");
  });

  const confirm = (ids: string[]) => run("confirm", async () => {
    const result = await api.bulkConfirmEntries(ids);
    await load();
    setChecked(new Set());
    const skipped = Object.keys(result.skipped).length;
    notify(`${result.confirmed.length}건을 확정했습니다.${skipped ? ` ${skipped}건은 누락 항목이 있어 건너뛰었습니다.` : ""}`, skipped && !result.confirmed.length ? "error" : "success");
  });

  const toggle = (id: string) => setChecked(set => { const next = new Set(set); if (next.has(id)) next.delete(id); else next.add(id); return next; });

  if (!entries) return <section className={s.card}><p className={s.helper}><Loader2 className={s.spin} size={12}/> 거래를 불러오는 중</p></section>;
  if (!list.length) return <section className={s.card}><h2>{workspace.year}년 은행 거래가 없습니다</h2><p>회사 기준 또는 자료 연결에서 은행 거래내역 파일을 올리면 거래마다 전표가 만들어집니다.</p></section>;

  return <>
    <section className={s.card}>
      <div className={s.cardHeading}><div><h2>통장 거래 {list.length}건 · 분개 확인</h2>
        <p>거래마다 전표를 만들고 자동으로 장부에 반영합니다. 카드 사용·계좌 간 이체·결제 취소는 규칙으로, 계정과 부가세는 Claude Haiku 5.5가 판단합니다.
          영수증과 연결된 거래는 영수증에서 분개합니다. <b>영수증 없는 이체</b>(개인·거래처 송금, 입금)는 AI가 추천만 하고, ‘이체 확인 (필수)’ 탭에서 직접 확인해 확정해야 장부에 들어갑니다. AI가 정한 값은 <mark className={s.aiMark}>AI</mark> 표시가 붙고, 고치려면 행을 펼쳐 ‘검토로 되돌리기’를 누르세요.</p></div>
        <div className={s.headingActions}>
          <button className={s.secondary} disabled={!!busy} onClick={() => judge()}>{busy === "judge" ? <Loader2 className={s.spin} size={15}/> : <Sparkles size={15}/>} AI로 전부 처리</button>
          <button className={s.primary} disabled={!!busy || !confirmable.length} onClick={() => confirm(confirmable.map(e => e.tx_id))}><Check size={15}/> 보이는 {confirmable.length}건 확정</button>
        </div></div>
      <div className={s.tabs}>{([["all", "전체"], ["person", "이체 확인 (필수)"], ["ai", "AI 추천 확인"], ["missing", "입력 필요"], ["receipt", "영수증 연결"], ["confirmed", "확정"]] as const).map(([v, label]) =>
        <button key={v} className={filter === v ? s.tabActive : ""} onClick={() => setFilter(v)}>{label} {counts[v]}</button>)}</div>
      {checked.size > 0 && <div className={s.selectBar}><span>{checked.size}건 선택</span>
        <button className={s.secondary} disabled={!!busy} onClick={() => judge([...checked])}><RefreshCw size={13}/> 선택 다시 추천</button>
        <button className={s.primary} disabled={!!busy} onClick={() => confirm([...checked])}><Check size={13}/> 선택 확정</button>
        <button className={s.linkButton} onClick={() => setChecked(new Set())}>선택 해제</button></div>}

      <div className={s.tableWrap}><table className={s.entryTable}><thead><tr>
        <th><input type="checkbox" aria-label="보이는 거래 모두 선택" checked={shown.length > 0 && shown.every(e => checked.has(e.tx_id))}
          onChange={ev => setChecked(ev.target.checked ? new Set(shown.filter(open).map(e => e.tx_id)) : new Set())}/></th>
        <th>날짜</th><th>계좌</th><th>거래 내용</th><th>출금</th><th>입금</th><th>계정</th><th>부가세</th><th>거래처</th><th>크로스체크</th><th>상태</th>
      </tr></thead><tbody>
        {shown.map(e => {
          const tx = e.transaction;
          const editable = open(e) && busy !== e.tx_id;
          const lowConfidence = !!e.ai && e.ai.confidence < LOW_CONFIDENCE;
          const mask = (field: string) => !e.confirmed && isAi(e.autofilled[field]) ? `${s.aiCell} ${lowConfidence ? s.aiLow : ""}` : "";
          return <Fragment key={e.tx_id}>
            <tr className={e.confirmed ? s.doneRow : e.covered_by || e.skip ? s.dimRow : undefined}>
              <td>{open(e) && <input type="checkbox" aria-label={`${tx.description} 선택`} checked={checked.has(e.tx_id)} onChange={() => toggle(e.tx_id)}/>}</td>
              <td className={s.mono}>{tx.posted_at.slice(5, 10)}</td>
              <td>{tx.bank_name}</td>
              <td><button className={s.rowToggle} onClick={() => setExpanded(x => x === e.tx_id ? "" : e.tx_id)} aria-expanded={expanded === e.tx_id}>
                {expanded === e.tx_id ? <ChevronDown size={13}/> : <ChevronRight size={13}/>}{tx.description || "(적요 없음)"}</button>
                {e.card && <small className={s.tableSub}>카드 {workspace.cards.find(c => c.code === e.card)?.number} · 미지급금 경유</small>}
                {e.transfer_to && <small className={s.tableSub}>회사 계좌 간 이체 · 수수료 {money(e.fee)}원</small>}</td>
              <td className={s.num}>{tx.withdrawal ? money(tx.withdrawal) : ""}</td>
              <td className={s.num}>{tx.deposit ? money(tx.deposit) : ""}</td>
              <td className={mask("account")} title={e.autofilled.account}>
                {e.covered_by ? <span className={s.noteText}>{e.covered_by.startsWith("R-") ? "영수증에서 분개" : "이체 출금에서 분개"}</span>
                  : e.transfer_to ? <span>{e.account} {name(e.account)}</span>
                  : <select aria-label={`${tx.description} 계정`} disabled={!editable} value={e.account} onChange={ev => update(e, { account: ev.target.value })}>
                    <option value="">계정 선택</option>{accounts.map(a => <option key={a.code} value={a.code}>{a.code} {a.name}</option>)}
                    {e.account && !accounts.some(a => a.code === e.account) && <option value={e.account}>{e.account} · 계정표에 없음</option>}</select>}
                {isAi(e.autofilled.account) && <small className={s.aiBadge}><Sparkles size={9}/> AI {e.ai ? Math.round(e.ai.confidence * 100) : ""}%</small>}</td>
              <td className={mask("tax")} title={e.autofilled.tax}>{!e.covered_by && !e.transfer_to && tx.withdrawal > 0
                ? <MoneyInput aria-label={`${tx.description} 부가세`} className={s.amountInput} disabled={!editable} value={e.tax} commitOnBlur onChange={v => update(e, { tax: v })}/> : ""}</td>
              <td className={mask("vendor")} title={e.autofilled.vendor}>{e.covered_by ? "" : e.vendor}{e.vendor_code && <small className={s.tableSub}>{e.vendor_code}</small>}</td>
              <td>{e.checks.map(c => <small key={c} className={c.includes("차이") || c.includes("다름") ? s.checkWarn : s.checkOk}>{c}</small>)}
                {e.covered_by.startsWith("R-") && <button className={s.linkButton} onClick={onReceipts}><Link2 size={11}/> {e.covered_by}</button>}</td>
              <td>{e.confirmed ? <span className={s.doneTag}>확정</span>
                : e.covered_by || e.skip ? <span className={s.pendingTag}>{e.skip ? "제외" : "연결됨"}</span>
                : e.missing.length ? <span className={s.pendingTag} title={e.missing.join(", ")}>{e.missing[0]}</span>
                : e.requires_person ? <button className={s.personConfirm} disabled={!!busy} title="영수증 없는 이체입니다. 계정을 확인하고 확정하세요." onClick={() => run(e.tx_id, async () => { await api.confirmEntry(e.tx_id); await load(); })}><Check size={12}/> 확인 후 확정</button>
                : <button className={s.linkButton} disabled={!!busy} onClick={() => run(e.tx_id, async () => { await api.confirmEntry(e.tx_id); await load(); })}><Check size={12}/> 확정</button>}</td>
            </tr>
            {expanded === e.tx_id && <tr className={s.detailRow}><td/><td colSpan={10}>
              <div className={s.entryDetail}>
                <div>
                  <h3>분개</h3>
                  {e.lines.length ? <table className={s.miniTable}><tbody>{e.lines.map((l, i) => <tr key={i}>
                    <td>{l.account} {name(l.account)}</td><td>{l.vendor}</td><td className={s.num}>{l.debit ? money(l.debit) : ""}</td><td className={s.num}>{l.credit ? money(l.credit) : ""}</td></tr>)}</tbody></table>
                    : <p>{e.covered_by ? `${e.covered_by}의 전표에 포함됩니다.` : "분개할 내용이 없습니다."}</p>}
                  {e.ai && <p className={s.aiReason}><Sparkles size={11}/> {e.ai.model} · 신뢰도 {Math.round(e.ai.confidence * 100)}%{lowConfidence ? " (낮음 · 꼭 확인)" : ""} — {e.ai.reason}</p>}
                  {Object.entries(e.autofilled).filter(([, why]) => !isAi(why)).map(([f, why]) => <p key={f} className={s.noteText}>규칙 · {why}</p>)}
                </div>
                {!e.covered_by && <div className={s.fields}>
                  <label>적요<input disabled={!editable} defaultValue={e.memo} key={`${e.tx_id}-memo-${e.memo}`} onBlur={ev => ev.target.value !== e.memo && update(e, { memo: ev.target.value })}/></label>
                  <label>거래처<input disabled={!editable} defaultValue={e.vendor} key={`${e.tx_id}-vendor-${e.vendor}`} onBlur={ev => ev.target.value !== e.vendor && update(e, { vendor: ev.target.value })}/></label>
                  <label>거래처 코드<input disabled={!editable} list="entry-vendors" defaultValue={e.vendor_code} key={`${e.tx_id}-code-${e.vendor_code}`} onBlur={ev => ev.target.value !== e.vendor_code && update(e, { vendor_code: ev.target.value })}/></label>
                  {tx.withdrawal > 0 && <label>결제 카드<select disabled={!editable} value={e.card} onChange={ev => update(e, { card: ev.target.value })}><option value="">카드 아님 · 보통예금 직접 출금</option>
                    {workspace.cards.map(c => <option key={c.code} value={c.code}>{c.name} {c.number} ({c.payable ? "미지급금 경유" : "직접 출금"})</option>)}</select></label>}
                  <label className={s.checkbox}><input type="checkbox" disabled={!editable && !e.skip} checked={e.skip} onChange={ev => update(e, { skip: ev.target.checked })}/> 분개하지 않음 (은행이 같은 날 되돌린 거래 등)</label>
                </div>}
                <div className={s.bulkActions}>{e.confirmed && <button className={s.secondary} onClick={() => run(e.tx_id, async () => { replace(await api.reopenEntry(e.tx_id)); })}><Undo2 size={13}/> 검토로 되돌리기</button>}</div>
              </div>
            </td></tr>}
          </Fragment>;
        })}
        {!shown.length && <tr><td colSpan={11} className={s.empty}>이 조건의 거래가 없습니다.</td></tr>}
      </tbody></table></div>
      <datalist id="entry-vendors">{workspace.vendors.filter(v => v.code).map(v => <option key={v.code} value={v.code}>{v.name}</option>)}</datalist>
    </section>
    <div className={s.next}><button className={s.secondary} onClick={onReceipts}>영수증 검토</button><button className={s.primary} onClick={onLedger}>계정별원장 보기 <ArrowRight size={16}/></button></div>
  </>;
}
