"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Check, CheckCircle2, Copy, ExternalLink, FileText, Layers, Link2, Loader2, RefreshCw, Search, Sparkles, Trash2, TriangleAlert, Upload, Unlink, Wand2, X } from "lucide-react";
import { api, autoMessage, fileUrl, isAi, isDone, money, type BankTransaction, type PaymentCandidate, type Receipt, type Review, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import MoneyInput from "./MoneyInput";
import s from "./workbench.module.css";

type Props = {
  workspace: Workspace; receipts: Receipt[]; transactions: BankTransaction[]; selected: string;
  onSelect: (id: string) => void; onReceipt: (r: Receipt) => void; onReceipts: (list: Receipt[]) => void;
  onWorkspaceChanged: () => Promise<void>; onDeleted: (ids: string[]) => void;
  notify: Notify; onUpload: () => void; onLedger: (account: string) => void;
};

type Filter = "all" | "pending" | "ready" | "confirmed";
const ready = (x: Receipt) => !x.confirmed && x.missing.length === 0;

export default function ReviewStep(props: Props) {
  const { receipts, selected, onSelect, onUpload, notify } = props;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const current = receipts.find(x => x.id === selected) ?? receipts.find(x => !isDone(x)) ?? receipts[0];
  const failed = receipts.filter(x => x.extraction_error && !x.confirmed);
  const duplicates = receipts.filter(x => x.duplicate_of && !x.confirmed);
  const zeroVat = receipts.filter(x => x.notices.some(n => n.startsWith("법인 가맹점")));

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

  const retryAll = () => run("retry", async () => {
    let fixed = 0;
    for (const x of failed) {
      const updated = await api.reextract(x.id);
      props.onReceipt(updated);
      if (!updated.extraction_error) fixed += 1;
    }
    await props.onWorkspaceChanged();
    notify(`${failed.length}건 중 ${fixed}건을 추출했습니다.${fixed < failed.length ? " 나머지는 원본을 보고 직접 입력하세요." : ""}`);
  });

  const judge = () => run("judge", async () => {
    const result = await api.autoProcess();
    props.onReceipts(await api.receipts());
    notify(autoMessage(result), result.error && !result.judged && !result.auto?.receipts ? "error" : "success");
  });

  const reapply = () => run("autofill", async () => {
    props.onReceipts(await api.autofill());
    await props.onWorkspaceChanged();
    notify("거래처 규칙·카드전표·은행 거래로 빈 항목을 다시 채웠습니다. 직접 입력한 값은 바꾸지 않았습니다.");
  });

  const recheckVat = () => run("vat", async () => {
    const result = await api.recheckVat();
    props.onReceipts(await api.receipts());
    notify(`${result.checked}건을 다시 읽어 ${result.fixed}건의 부가세를 찾았습니다.${result.checked > result.fixed ? " 나머지는 면세 품목이거나 영수증에 부가세가 없습니다." : ""}${result.error ? ` 일부 실패: ${result.error}` : ""}`);
  });

  // One click: every later copy goes, the first upload of each receipt stays. Confirmed receipts are never touched.
  const removeDuplicates = () => run("dups", async () => {
    const { deleted } = await api.deleteDuplicates();
    props.onDeleted(deleted);
    notify(`중복 영수증 ${deleted.length}건을 정리했습니다. 각 영수증은 처음 올린 것만 남겼습니다.`);
  });

  if (!current) {
    return <section className={`${s.card} ${s.emptyState}`}><FileText size={28}/><h2>검토할 영수증이 없습니다</h2><p>자료 연결 단계에서 영수증 파일을 올리면 이곳에 나타납니다.</p><button className={s.primary} onClick={onUpload}><Upload size={16}/> 영수증 올리기</button></section>;
  }

  const counts: Record<Filter, number> = { all: receipts.length, pending: receipts.filter(x => !isDone(x) && !ready(x)).length, ready: receipts.filter(ready).length, confirmed: receipts.filter(isDone).length };
  const visible = receipts.filter(x =>
    (x.merchant.toLowerCase().includes(query.toLowerCase()) || x.id.includes(query.toUpperCase()) || x.file_name.includes(query)) &&
    (filter === "all" || (filter === "confirmed" ? isDone(x) : filter === "ready" ? ready(x) : !isDone(x) && !ready(x))));
  const selectable = visible.filter(x => !x.confirmed);
  const toggle = (id: string) => setChecked(set => { const next = new Set(set); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const selectedReceipts = receipts.filter(x => checked.has(x.id) && !x.confirmed);

  return <>
    {zeroVat.length > 0 && <div className={s.dupBanner} role="status">
      <TriangleAlert size={17}/>
      <div><b>법인 가맹점인데 부가세가 0원으로 읽힌 영수증 {zeroVat.length}건</b>
        <span>법인은 일반과세자라 보통 부가세가 찍힙니다. 자동 추출이 부가세를 놓쳤을 수 있어 Claude로 부가세만 다시 읽습니다. 직접 정한 부가세는 바꾸지 않습니다.</span>
        <small>{zeroVat.map(x => `${x.id}(${x.merchant} ${money(x.total)}원)`).join(" · ")}</small></div>
      <button className={s.primary} disabled={!!busy} onClick={recheckVat}>{busy === "vat" ? <Loader2 className={s.spin} size={14}/> : <RefreshCw size={14}/>} 부가세 다시 읽기</button>
    </div>}
    {duplicates.length > 0 && <div className={s.dupBanner} role="status">
      <Copy size={17}/>
      <div><b>같은 영수증이 {duplicates.length}건 더 올라와 있습니다</b>
        <span>승인번호·금액·날짜가 같은 영수증입니다. 그대로 두면 비용이 두 번 잡힐 수 있어 장부에 넣지 않았습니다. 처음 올린 영수증만 남기고 나머지를 정리합니다.</span>
        <small>{duplicates.map(x => `${x.id}(${x.merchant || x.file_name}) → ${x.duplicate_of}`).join(" · ")}</small></div>
      <button className={s.primary} disabled={!!busy} onClick={removeDuplicates}>{busy === "dups" ? <Loader2 className={s.spin} size={14}/> : <Trash2 size={14}/>} 중복 {duplicates.length}건 정리</button>
    </div>}
    <div className={s.reviewLayout}>
    <section className={s.queue}>
      <div className={s.queueTitle}><h2>검토할 영수증</h2><span>{receipts.length}</span></div>
      <label className={s.search}><Search size={16}/><input aria-label="영수증 검색" placeholder="거래처·번호 검색" value={query} onChange={e => setQuery(e.target.value)}/></label>
      <div className={s.tabs}>{([["all", "전체"], ["pending", "입력 필요"], ["ready", "확정 가능"], ["confirmed", "확정"]] as const).map(([v, label]) => <button key={v} className={filter === v ? s.tabActive : ""} onClick={() => setFilter(v)}>{label} {counts[v]}</button>)}</div>
      <div className={s.selectBar}>
        <label className={s.checkAll}><input type="checkbox" aria-label="보이는 영수증 모두 선택" checked={selectable.length > 0 && selectable.every(x => checked.has(x.id))} onChange={e => setChecked(e.target.checked ? new Set(selectable.map(x => x.id)) : new Set())}/> 모두 선택</label>
        {checked.size > 0 && <button className={s.linkButton} onClick={() => setChecked(new Set())}>선택 해제 ({checked.size})</button>}
      </div>
      <div className={s.queueItems}>{visible.map(x => <div key={x.id} className={`${s.receiptRow} ${current.id === x.id && !checked.size ? s.selected : ""} ${checked.has(x.id) ? s.checkedRow : ""}`}>
        <input type="checkbox" aria-label={`${x.id} 선택`} disabled={x.confirmed} checked={checked.has(x.id)} onChange={() => toggle(x.id)}/>
        <button className={s.receiptItem} onClick={() => { setChecked(new Set()); onSelect(x.id); }}>
          <div><strong>{x.merchant || "상호 미확인"}</strong><span className={isDone(x) ? s.doneTag : ready(x) ? s.readyTag : s.pendingTag}>{isDone(x) ? "확정" : x.confirmed ? "재검토" : x.duplicate_of ? "중복" : ready(x) ? "확정 가능" : "입력 필요"}</span></div>
          <small>{x.date || "날짜 미확인"} · {x.id}</small><b>{money(x.total)}<small> 원</small></b>
          <p>{x.extraction_error ? "자동 추출 실패 · 직접 입력" : x.duplicate_of ? `${x.duplicate_of}와 같은 영수증` : isDone(x) ? `${x.account} · 확정` : ready(x) ? "모든 항목 입력됨" : x.missing.slice(0, 2).join(", ") + (x.missing.length > 2 ? ` 외 ${x.missing.length - 2}개` : "")}</p>
        </button>
      </div>)}
        {!visible.length && <p className={s.queueEmpty}>조건에 맞는 영수증이 없습니다.</p>}
      </div>
      <div className={s.queueFoot}>
        {duplicates.length > 0 && <button className={s.textButton} disabled={!!busy} onClick={removeDuplicates}>{busy === "dups" ? <Loader2 className={s.spin} size={13}/> : <Copy size={13}/>} 중복 영수증 {duplicates.length}건 삭제</button>}
        {failed.length > 0 && <button className={s.textButton} disabled={!!busy} onClick={retryAll}>{busy === "retry" ? <Loader2 className={s.spin} size={13}/> : <RefreshCw size={13}/>} 자동 추출 실패 {failed.length}건 다시 실행</button>}
        <button className={s.textButton} disabled={!!busy} onClick={judge}>{busy === "judge" ? <Loader2 className={s.spin} size={13}/> : <Sparkles size={13}/>} AI로 전부 처리</button>
        <button className={s.textButton} disabled={!!busy} onClick={reapply}>{busy === "autofill" ? <Loader2 className={s.spin} size={13}/> : <Wand2 size={13}/>} 빈 항목 자동 입력 다시 적용</button>
        <button className={s.textButton} onClick={onUpload}><Upload size={13}/> 영수증 더 올리기</button>
      </div>
    </section>
    {selectedReceipts.length > 0
      ? <BulkPanel {...props} items={selectedReceipts} onDone={() => setChecked(new Set())}/>
      : <ReceiptEditor key={current.id} {...props} receipt={current} onSelectGroup={ids => setChecked(new Set(ids))}/>}
    </div>
  </>;
}

function BulkPanel({ workspace, items, onReceipts, onWorkspaceChanged, notify, onDone }: Props & { items: Receipt[]; onDone: () => void }) {
  // A field shows the selection's shared value; "" means the receipts differ (or it is empty everywhere).
  const common = <K extends "purpose" | "project" | "account" | "vat">(key: K) => {
    const values = new Set(items.map(x => String(x[key] ?? "")));
    const only = values.size === 1 ? [...values][0] : "";
    return key === "vat" && only === "pending" ? "" : only;
  };
  const mixed = (key: "purpose" | "project" | "account" | "vat") => new Set(items.map(x => x[key])).size > 1;
  const ids = items.map(x => x.id);
  const selectionKey = ids.join(",");
  const [form, setForm] = useState({ purpose: common("purpose"), project: common("project"), account: common("account"), vat: common("vat") });
  // New selection -> start from its shared values. Applying keeps what was typed.
  useEffect(() => {
    setForm({ purpose: common("purpose"), project: common("project"), account: common("account"), vat: common("vat") });
    setApplied(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey]);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState(false);
  const [skipped, setSkipped] = useState<Record<string, string[]>>({});
  const set = (patch: Partial<typeof form>) => { setForm(f => ({ ...f, ...patch })); setApplied(false); };
  const pendingChanges: Partial<Review> = {};
  if (form.purpose.trim() && form.purpose.trim() !== common("purpose")) pendingChanges.purpose = form.purpose.trim();
  if (form.project.trim() && form.project.trim() !== common("project")) pendingChanges.project = form.project.trim();
  if (form.account && form.account !== common("account")) pendingChanges.account = form.account;
  if (form.vat && form.vat !== common("vat")) pendingChanges.vat = form.vat as Review["vat"];
  const changeCount = Object.keys(pendingChanges).length;
  const readyCount = items.filter(ready).length;
  const total = items.reduce((sum, x) => sum + x.total, 0);
  const merchants = Array.from(new Set(items.map(x => x.merchant || "상호 미확인")));
  const allChecked = items.every(x => x.checked);
  const name = (code: string) => workspace.accounts.find(a => a.code === code)?.name || code;

  const go = async (task: () => Promise<string>) => {
    setBusy(true);
    try {
      notify(await task());
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const apply = () => go(async () => {
    onReceipts(await api.bulkUpdate(ids, pendingChanges));
    setApplied(true);
    return `${ids.length}건에 ${changeCount}개 항목을 적용했습니다.`;
  });

  const markChecked = () => go(async () => {
    onReceipts(await api.bulkUpdate(ids, { checked: !allChecked }));
    return allChecked ? `${ids.length}건의 확인 표시를 해제했습니다.` : `${ids.length}건을 원본·거래 확인 완료로 표시했습니다.`;
  });

  const confirmAll = () => go(async () => {
    const result = await api.bulkConfirm(ids);
    onReceipts(result.receipts);
    setSkipped(result.skipped);
    await onWorkspaceChanged();
    if (!Object.keys(result.skipped).length) onDone();
    return `${result.confirmed.length}건을 확정했습니다.${Object.keys(result.skipped).length ? ` ${Object.keys(result.skipped).length}건은 입력할 항목이 남아 확정하지 않았습니다.` : ""}`;
  });

  return <div className={s.bulkMain}>
    <section className={s.card}>
      <div className={s.cardHeading}><div><div className={s.eyebrow}>BULK / {items.length}건 선택</div><h2>여러 영수증 한 번에 처리</h2><p>{merchants.slice(0, 3).join(", ")}{merchants.length > 3 ? ` 외 ${merchants.length - 3}곳` : ""} · 합계 {money(total)}원</p></div>
        <button className={s.iconButton} aria-label="선택 해제" onClick={onDone}><X size={16}/></button></div>
      <h3 className={s.bulkStepTitle}><span>1</span> 항목 일괄 입력 <small>입력한 항목만 선택한 영수증 모두에 덮어씁니다. 빈칸은 바꾸지 않습니다</small></h3>
      <div className={s.fields}>
        <label className={s.wide}>사용 목적 · 적요<input placeholder={mixed("purpose") ? "여러 값 · 입력하면 모두 이 값으로 바뀝니다" : "예: 게임 에셋 구매"} value={form.purpose} onChange={e => set({ purpose: e.target.value })}/></label>
        <label>비용 계정<select value={form.account} onChange={e => set({ account: e.target.value })}><option value="">{mixed("account") ? "여러 값 · 바꾸지 않음" : "선택 안 함"}</option>{workspace.accounts.filter(a => a.kind === "expense").map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label>
        <label>부가세 처리<select value={form.vat} onChange={e => set({ vat: e.target.value })}><option value="">{mixed("vat") ? "여러 값 · 바꾸지 않음" : "검토 필요"}</option><option value="deductible">부가세대급금으로 분리</option><option value="included">부가세를 비용에 포함</option></select></label>
        <label>프로젝트 · 부서<input placeholder={mixed("project") ? "여러 값 · 입력하면 모두 이 값으로 바뀝니다" : "예: 서비스 개발"} value={form.project} onChange={e => set({ project: e.target.value })}/></label>
      </div>
      <div className={s.bulkActions}><button className={changeCount ? s.primary : s.secondary} disabled={busy || !changeCount} onClick={apply}>
        {changeCount ? `선택한 ${items.length}건에 ${changeCount}개 항목 적용` : applied ? <><Check size={14}/> {items.length}건에 적용됨</> : "바꿀 값을 입력하세요"}</button></div>

      <h3 className={s.bulkStepTitle}><span>2</span> 원본 확인 <small>원본 증빙과 연결된 출금 거래를 확인한 뒤 표시하세요</small></h3>
      <div className={s.bulkActions}><button className={s.secondary} disabled={busy} onClick={markChecked}>{allChecked ? "확인 표시 해제" : `${items.length}건 원본·거래 확인 완료로 표시`}</button></div>

      <h3 className={s.bulkStepTitle}><span>3</span> 확정 <small>입력할 항목이 남은 영수증은 건너뜁니다</small></h3>
      <div className={s.bulkActions}><button className={s.primary} disabled={busy || !readyCount} onClick={confirmAll}>{busy ? <Loader2 className={s.spin} size={15}/> : <Check size={15}/>} 확정 가능한 {readyCount}건 확정</button></div>
    </section>

    <section className={s.card}><div className={s.cardHeading}><h2>선택한 영수증</h2><span>확정 가능 {readyCount} / {items.length}</span></div>
      <div className={s.tableWrap}><table><thead><tr><th>번호</th><th>거래처</th><th>날짜</th><th>금액</th><th>계정</th><th>지급</th><th>남은 항목</th></tr></thead><tbody>
        {items.map(x => <tr key={x.id}><td>{x.id}</td><td>{x.merchant}</td><td>{x.date}</td><td>{money(x.total)}</td><td>{x.account ? `${x.account} ${name(x.account)}` : "—"}</td>
          <td>{x.payment === "paid" ? (x.payment_transaction_id ? "출금 연결" : "지급") : x.payment === "unpaid" ? "미지급" : "—"}</td>
          <td className={(skipped[x.id] ?? x.missing).length ? s.failText : undefined}>{(skipped[x.id] ?? x.missing).join(", ") || "없음"}</td></tr>)}
      </tbody></table></div></section>
  </div>;
}

function ReceiptEditor({ receipt, receipts, workspace, transactions, onReceipt, onReceipts, onWorkspaceChanged, onDeleted, notify, onLedger, onSelectGroup }: Props & { receipt: Receipt; onSelectGroup: (ids: string[]) => void }) {
  const [r, setR] = useState(receipt);
  const [saving, setSaving] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [showFile, setShowFile] = useState(true);
  const [candidates, setCandidates] = useState<PaymentCandidate[] | null>(null);
  const pending = useRef<Partial<Review>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chain = useRef<Promise<unknown>>(Promise.resolve());

  // Server state wins except for fields the reviewer typed but we have not sent yet.
  useEffect(() => setR({ ...receipt, ...pending.current }), [receipt]);

  const flush = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const changes = pending.current;
    if (!Object.keys(changes).length) return chain.current;
    pending.current = {};
    setSaving(true);
    chain.current = chain.current.then(async () => {
      try {
        onReceipt(await api.updateReceipt(receipt.id, changes));
        if (changes.payment_transaction_id || changes.confirm_vendor) await onWorkspaceChanged();
      } catch (error) {
        notify((error as Error).message, "error");
      } finally {
        setSaving(false);
      }
    });
    return chain.current;
  }, [receipt.id, onReceipt, notify, onWorkspaceChanged]);

  useEffect(() => () => { flush(); }, [flush]);

  const update = (patch: Partial<Review>, immediate = false) => {
    setR(x => ({ ...x, ...patch }));
    pending.current = { ...pending.current, ...patch };
    if (timer.current) clearTimeout(timer.current);
    if (immediate) flush();
    else timer.current = setTimeout(flush, 450);
  };

  useEffect(() => {
    if (!transactions.length) { setCandidates([]); return; }
    let live = true;
    setCandidates(null);
    api.candidates(receipt.id).then(list => live && setCandidates(list)).catch(() => live && setCandidates([]));
    return () => { live = false; };
  }, [receipt.id, receipt.total, receipt.date, receipt.vendor_code, transactions]);

  const act = async (action: () => Promise<Receipt>, message: string) => {
    await flush();
    try {
      onReceipt(await action());
      // Confirming teaches the vendor its defaults, which the server copies to that vendor's other receipts.
      onReceipts(await api.receipts());
      await onWorkspaceChanged();
      notify(message);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  const reextract = async () => {
    setExtracting(true);
    await flush();
    try {
      const updated = await api.reextract(r.id);
      onReceipt(updated);
      notify(updated.extraction_error ? `${r.id} OCR을 다시 실행했지만 추출하지 못했습니다.` : `${r.id}: ${updated.merchant} · ${money(updated.total)}원으로 추출했습니다.`, updated.extraction_error ? "error" : "success");
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setExtracting(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`${r.id} ${r.merchant} 영수증과 원본 파일을 삭제할까요?`)) return;
    try {
      await api.deleteReceipt(r.id);
      onDeleted([r.id]);
      notify(`${r.id} 영수증을 삭제했습니다.`);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  const name = (code: string) => workspace.accounts.find(a => a.code === code)?.name || code;
  const sameVendor = r.vendor_code ? receipts.filter(x => x.vendor_code === r.vendor_code && !x.confirmed && !x.duplicate_of) : [];
  const expenses = workspace.accounts.filter(a => a.kind === "expense");
  const debit = r.lines.reduce((sum, l) => sum + l.debit, 0);
  const credit = r.lines.reduce((sum, l) => sum + l.credit, 0);
  const ext = r.extraction;
  const isPdf = r.file_name.toLowerCase().endsWith(".pdf");
  const linkedTx = transactions.find(t => t.id === r.payment_transaction_id);
  const deductibleTax = r.vat === "deductible" ? r.tax : 0;
  const ocrDiff = (field: "merchant" | "date" | "total") => {
    if (!ext) return null;
    if (field === "date" && ext.purchased_at.slice(0, 4) === r.date.slice(0, 4)) return null;
    const original = field === "merchant" ? ext.merchant : field === "date" ? ext.purchased_at : Number(ext.total);
    return original !== r[field] ? <small className={s.ocrHint}>OCR: {typeof original === "number" ? money(original) : original}</small> : null;
  };

  return <>
    <div className={s.reviewMain}>
      <section className={s.card}>
        <div className={s.cardHeading}><div><div className={s.eyebrow}>RECEIPT / {r.id}</div><h2>{r.merchant || "상호 미확인"}</h2></div>
          <div className={s.headingActions}>{ext ? <span className={s.sample}>{ext.extraction_provider === "claude" ? "Claude" : "Google Vision"} 추출 · 신뢰도 {Math.round(ext.confidence * 100)}%</span> : <span className={s.pendingTag}>직접 입력</span>}
            {sameVendor.length > 1 && <button className={s.secondary} onClick={() => onSelectGroup(sameVendor.map(x => x.id))}><Layers size={14}/> 같은 거래처 {sameVendor.length}건 함께 처리</button>}
            {!r.confirmed && <button className={s.iconButton} aria-label="영수증 삭제" onClick={remove}><Trash2 size={14}/></button>}</div></div>
        {r.extraction_error && <div className={s.warnBox}><TriangleAlert size={14}/><span>{r.extraction_error}</span>
          {!r.confirmed && <button className={s.secondary} disabled={extracting} onClick={reextract}>{extracting ? <Loader2 className={s.spin} size={13}/> : <RefreshCw size={13}/>} OCR 다시 실행</button>}</div>}
        <fieldset disabled={r.confirmed} className={s.formBody}>
          {r.year_review_required && <div className={s.warnBox}><TriangleAlert size={14}/><div>
            <b>OCR 연도 {ext?.purchased_at.slice(0, 4)}년이 회계연도 {workspace.year}년과 다릅니다.</b>
            <p>원본의 연도를 확인해 주세요. 월·일은 입력된 값을 유지합니다.</p>
            <button type="button" className={s.secondary} disabled={saving || !r.date} onClick={() => {
              const day = workspace.year + r.date.slice(4);
              update({ date: day, confirm_year: true,
                ...(r.posting_date === r.date || !r.posting_date ? { posting_date: day } : {}),
                ...(r.payment_date === r.date && !r.payment_transaction_id ? { payment_date: day } : {}),
              }, true);
            }}>{workspace.year}년으로 확인{r.date.slice(0, 4) !== workspace.year ? "하고 수정" : ""}</button>
          </div></div>}
          <div className={s.receiptFacts}>
            <label><span className={s.labelRow}>거래처명 *<Auto r={r} f="merchant"/></span><input value={r.merchant} onChange={e => update({ merchant: e.target.value })}/>{ocrDiff("merchant")}</label>
            <label><span className={s.labelRow}>영수증 거래일 *<Auto r={r} f="date"/></span><input type="date" value={r.date} onChange={e => update({ date: e.target.value, ...(r.posting_date === r.date || !r.posting_date ? { posting_date: e.target.value } : {}) })}/>{ocrDiff("date")}</label>
            <label><span className={s.labelRow}>결제 금액 (원) *<Auto r={r} f="total"/></span><MoneyInput value={r.total} onChange={v => update({ total: v })}/>{ocrDiff("total")}</label>
          </div>
        </fieldset>
        {ext && <div className={s.extractMeta}><span>추출 부가세 <b>{money(r.original_tax)}원</b></span><span>결제수단 <b>{ext.payment_method}</b></span>{ext.approval_number && <span>승인번호 <b>{ext.approval_number}</b></span>}{ext.business_number && <span>사업자번호 <b>{ext.business_number}</b></span>}</div>}
        {ext?.representative && <p className={s.helper}>공급자 대표자: {ext.representative}</p>}
        {!!ext?.warnings.length && <ul className={s.warnList}>{ext.warnings.map(w => <li key={w}>{w}</li>)}</ul>}
        <div className={s.sourceFile}><FileText size={15}/>{r.file_name}<button className={s.linkButton} onClick={() => setShowFile(v => !v)}>{showFile ? "원본 접기" : "원본 보기"}</button><a className={s.linkButton} href={fileUrl(r.id)} target="_blank" rel="noreferrer">새 창 <ExternalLink size={11}/></a></div>
        {showFile && <div className={s.filePreview}>{isPdf ? <iframe title={`${r.id} 원본`} src={fileUrl(r.id)}/> : (
          // Served by the API with auth-free inline headers; next/image optimisation does not apply.
          // eslint-disable-next-line @next/next/no-img-element
          <img alt={`${r.id} 원본 영수증`} src={fileUrl(r.id)}/>
        )}</div>}
      </section>

      <section className={s.card}><div className={s.cardHeading}><div><h2>회계 정보 입력</h2><p>필수 항목은 * 표시 · 금액 단위: 원 · 입력하면 자동 저장됩니다.</p></div>
        <div className={s.headingActions}>{saving && <span className={s.savingTag}><Loader2 className={s.spin} size={12}/> 저장 중</span>}{r.confirmed && <button className={s.secondary} onClick={() => act(() => api.reopen(r.id), `${r.id}를 검토 상태로 되돌렸습니다. 원장에서 빠집니다.`)}>검토로 되돌리기</button>}</div></div>
        {r.duplicate_of && <div className={s.warnBox}><Copy size={14}/><span>{r.duplicate_of}와 같은 영수증(승인번호·금액·날짜 동일)입니다. 중복으로 비용이 두 번 잡히지 않도록 이 영수증은 삭제하세요.</span>{!r.confirmed && <button className={s.secondary} onClick={remove}>이 영수증 삭제</button>}</div>}
        {!r.confirmed && Object.keys(r.autofilled).length > 0 && <div className={s.autoSummary}><Sparkles size={14}/><div><b>{Object.keys(r.autofilled).length}개 항목을 자동으로 채웠습니다.</b> 내용이 맞는지 확인하세요. 직접 고치면 자동 표시가 사라집니다.
          {r.ai && <p className={s.aiReason}>{r.ai.model} 추천 · 신뢰도 {Math.round(r.ai.confidence * 100)}% — {r.ai.reason}</p>}
          <ul>{Object.entries(r.autofilled).filter(([f]) => !["payment_date", "bank", "payment_transaction_id", "payment_link"].includes(f)).map(([f, why]) => <li key={f}><span>{FIELD_NAMES[f] ?? f}</span>{why}</li>)}</ul></div></div>}
        <fieldset disabled={r.confirmed} className={s.formBody}>
          <h3><span>01</span> 사용 목적과 귀속</h3><div className={s.fields}>
            <label className={s.wide}><span className={s.labelRow}>사용 목적 · 적요 *<Auto r={r} f="purpose"/></span><input placeholder="예: 앱 개발 외주 용역" value={r.purpose} onChange={e => update({ purpose: e.target.value })}/></label>
            <label>프로젝트 · 부서<input placeholder="예: 서비스 개발 / 공통 운영" value={r.project} onChange={e => update({ project: e.target.value })}/></label>
            <label>사용자 · 담당자<input placeholder="담당자 이름" value={r.owner} onChange={e => update({ owner: e.target.value })}/></label>
            <label><span className={s.labelRow}>전기일 *<Auto r={r} f="posting_date"/></span><input type="date" value={r.posting_date} onChange={e => update({ posting_date: e.target.value })}/></label>
            <label><span className={s.labelRow}>거래처 코드 *<Auto r={r} f="vendor_code"/></span><input list={`vendors-${r.id}`} placeholder="선택 또는 직접 입력" value={r.vendor_code} onChange={e => update({ vendor_code: e.target.value })}/>
              <datalist id={`vendors-${r.id}`}>{workspace.vendors.filter(v => v.code).map(v => <option key={v.code} value={v.code}>{v.name}{v.alias ? ` · ${v.alias}` : ""}</option>)}</datalist></label>
          </div>

          <h3><span>02</span> 계정과 부가세</h3>
          {!r.account && r.account_suggestion && <div className={s.suggestion}><Sparkles size={14}/><div><b>제안: {r.account_suggestion} · {name(r.account_suggestion)}</b><small>{r.account_suggestion_reason}</small></div><button className={s.secondary} onClick={() => update({ account: r.account_suggestion }, true)}>적용</button></div>}
          <div className={s.fields}>
            <label><span className={s.labelRow}>비용 계정 *<Auto r={r} f="account"/></span><select value={r.account} onChange={e => update({ account: e.target.value }, true)}><option value="">계정 선택</option>{expenses.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}{r.account && !expenses.some(a => a.code === r.account) && <option value={r.account}>{r.account} · 계정표에 없음</option>}</select></label>
            <label><span className={s.labelRow}>부가세 처리 *<Auto r={r} f="vat"/></span><select value={r.vat} onChange={e => update({ vat: e.target.value as Review["vat"], ...(e.target.value === "deductible" && !r.tax ? { tax: r.original_tax } : {}) }, true)}><option value="pending">검토 필요</option><option value="deductible">부가세대급금으로 분리</option><option value="included">부가세를 비용에 포함</option></select></label>
            {r.vat === "deductible" && <label><span className={s.labelRow}>확인한 공제 부가세 *<Auto r={r} f="tax"/></span><MoneyInput value={r.tax} onChange={v => update({ tax: v })}/>{r.tax !== r.original_tax && <small className={s.ocrHint}>추출값 {money(r.original_tax)}원</small>}</label>}
            <label className={s.wide}><span className={s.labelRow}>증빙·부가세 검토 근거 *<Auto r={r} f="evidence"/></span><input placeholder="예: 세금계산서 번호, 불공제 사유, 확인한 정산서" value={r.evidence} onChange={e => update({ evidence: e.target.value })}/></label>
          </div>

          <h3><span>03</span> 지급 정보와 기존 채무</h3><div className={s.fields}>
            <label><span className={s.labelRow}>지급 상태 *<Auto r={r} f="payment"/></span><select value={r.payment} onChange={e => update({ payment: e.target.value as Review["payment"] }, true)}><option value="pending">확인 필요</option><option value="paid">지급 완료 · 은행 출금</option><option value="unpaid">미지급 · 외상매입금</option></select></label>
            {r.payment === "paid" && <>
              <label><span className={s.labelRow}>지급일 *<Auto r={r} f="payment_date"/></span><input type="date" value={r.payment_date} onChange={e => update({ payment_date: e.target.value })}/></label>
              <label><span className={s.labelRow}>출금 계좌 *<Auto r={r} f="bank"/></span><select value={r.bank} onChange={e => update({ bank: e.target.value }, true)}><option value="">계좌 선택</option>{workspace.banks.map(b => <option key={b.code} value={b.code}>{b.name} · {b.account} {name(b.account)}</option>)}</select></label>
              <label><span className={s.labelRow}>출금 거래 연결 <small className={s.optional}>선택 · 확정에는 필요 없음</small><Auto r={r} f="payment_link"/></span>{linkedTx ? <div className={s.linkedTx}><Link2 size={13}/><span>{r.payment_link}</span><button type="button" className={s.linkButton} onClick={() => update({ payment_link: "", payment_transaction_id: "" }, true)}><Unlink size={11}/> 해제</button></div>
                : <input placeholder="거래 ID 또는 출금일·금액·수취인" value={r.payment_link} onChange={e => update({ payment_link: e.target.value })}/>}</label>
            </>}
            <label><span className={s.labelRow}>기존 외상매입금 정산액<Auto r={r} f="payable_offset"/></span><MoneyInput value={r.payable_offset} onChange={v => update({ payable_offset: v })}/></label>
            {r.payable_offset > 0 && <label><span className={s.labelRow}>기존 채무 전표 연결 *<Auto r={r} f="payable_link"/></span><input placeholder="기존 전표 번호 또는 원장 행" value={r.payable_link} onChange={e => update({ payable_link: e.target.value })}/></label>}
          </div>
          {r.vendor_review_required && <div className={s.warnBox}><TriangleAlert size={14}/><div>
            <b>세금계산서 공급자와 선택한 거래처가 같은지 확인해 주세요.</b>
            <p>{ext?.merchant} · 대표자 {ext?.representative} · 사업자번호 {ext?.business_number}<br/>선택한 거래처: {workspace.vendors.find(v => v.code === r.vendor_code)?.name || "미선택"}</p>
            <button type="button" className={s.secondary} disabled={saving || !r.vendor_code} onClick={() => update({ confirm_vendor: true }, true)}>같은 거래처로 확인</button>
          </div></div>}
          {r.payment !== "unpaid" && !linkedTx && <Candidates invoice={ext?.document_type === "tax_invoice"} candidates={candidates} hasTransactions={transactions.length > 0} onLink={tx => update({ payment_transaction_id: tx.id }, true)}/>}
          {r.payable_offset > 0 && <div className={s.inlineNote}>결제 총액 중 {money(r.payable_offset)}원은 기존 채무 정산으로 분리합니다. 신규 비용은 {money(r.total - r.payable_offset - deductibleTax)}원입니다.</div>}
          {r.payment === "paid" && r.payment_date !== r.posting_date && <div className={s.inlineNote}>전기일에는 비용과 외상매입금을, 지급일에는 외상매입금 정산과 은행 출금을 기록합니다.</div>}
          <p className={s.helper}>은행 지급·외상매입금 흐름을 다룹니다. 카드 결제, 선급금, 환불·취소는 별도 검토가 필요합니다.</p>
          <label className={s.checkbox}><input type="checkbox" checked={r.checked} onChange={e => update({ checked: e.target.checked }, true)}/> 원본 증빙과 연결 거래를 확인했습니다. *<Auto r={r} f="checked"/></label>
        </fieldset>
      </section>
    </div>

    <aside className={s.preview}><div className={s.previewHeading}><span className={s.eyebrow}>LIVE PREVIEW</span><h2>분개 미리보기</h2><p>입력한 정보로 서버가 계산한 분개입니다.</p></div>
      {Array.from(new Set(r.lines.map(l => l.voucher))).map(v => <div className={s.voucher} key={v}><div className={s.voucherTitle}>{r.lines.find(l => l.voucher === v)?.date}<small>{v}</small></div>
        {r.lines.filter(l => l.voucher === v).map((l, i) => <div className={s.journalLine} key={i}><span className={l.debit ? s.debit : s.credit}>{l.debit ? "차" : "대"}</span><div>{name(l.account)}<small>{l.account} · {l.vendor}</small></div><strong>{money(l.debit || l.credit)}</strong></div>)}</div>)}
      <div className={s.balance}><span>차변 합계 <b>{money(debit)}</b></span><span>대변 합계 <b>{money(credit)}</b></span><strong className={debit === credit ? undefined : s.failText}><Check size={14}/>{debit === credit ? "차변·대변 일치" : "금액 확인 필요"}</strong></div>
      <div className={s.checklist}><h3>{r.confirmed ? (r.missing.length ? "확정 후 기준이 바뀌어 재검토가 필요합니다" : "검토 확정됨") : r.missing.length ? `${r.missing.length}개 항목을 확인하세요` : "확정할 준비가 되었습니다"}</h3>
        {r.missing.map(g => {
          const f = gapField(g);
          if (!r.confirmed && f === "account") return <div key={g} className={s.quickFix}><span className={s.quickLabel}><span className={s.dot}/>{g}</span>
            <select aria-label="비용 계정 바로 선택" value={r.account} onChange={e => update({ account: e.target.value }, true)}><option value="">계정 선택</option>{expenses.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select>
            {r.vendor_code && <small>확정하면 이 거래처의 기본 계정으로 기억합니다.</small>}</div>;
          if (!r.confirmed && f === "purpose") return <div key={g} className={s.quickFix}><span className={s.quickLabel}><span className={s.dot}/>{g}</span>
            <input aria-label="사용 목적 바로 입력" placeholder="예: 게임 에셋 구매" value={r.purpose} onChange={e => update({ purpose: e.target.value })}/></div>;
          if (!r.confirmed && f === "checked") return <label key={g} className={s.quickCheck}><input type="checkbox" checked={r.checked} onChange={e => update({ checked: e.target.checked }, true)}/> 원본과 연결 거래를 확인했습니다</label>;
          return <button key={g} type="button" className={s.gapLink} disabled={!f} onClick={() => f && focusField(f)}><span className={s.dot}/>{g}{f && <ArrowRight size={11}/>}</button>;
        })}{!r.missing.length && <p><CheckCircle2 size={17}/> 필요한 정보를 모두 입력했습니다.</p>}
        {!r.confirmed && r.notices.map(n => <button key={n} type="button" className={s.noticeLink} onClick={() => focusField("payment_link")} title="확정은 할 수 있습니다. 은행 거래내역을 올리면 자동으로 연결됩니다.">{n} <ArrowRight size={11}/></button>)}</div>
      <button className={s.primary} disabled={r.confirmed || r.missing.length > 0 || debit !== credit || saving} onClick={() => act(() => api.confirm(r.id), `${r.merchant} 분개를 확정했습니다. 계정별원장에 반영됐습니다.`)}><Check size={16}/>{r.confirmed ? "확정 완료" : r.missing.length === 1 ? `${r.missing[0].split(" (")[0]} 입력 후 확정` : r.missing.length ? `${r.missing.length}개 항목 입력 후 확정` : "분개 확정"}</button>
      <button className={s.textButton} onClick={() => onLedger(r.account || "")}>계정별원장 보기 <ArrowRight size={14}/></button>
      <small className={s.previewFoot}>{r.confirmed_at ? `확정: ${r.confirmed_at.replace("T", " ")}` : "확정한 전표만 원장과 내보내기에 포함됩니다."}</small>
    </aside>
  </>;
}

function Candidates({ candidates, hasTransactions, onLink, invoice }: { candidates: PaymentCandidate[] | null; hasTransactions: boolean; onLink: (tx: BankTransaction) => void; invoice: boolean }) {
  if (!hasTransactions) return <p className={s.helper}>은행 거래 내역을 가져오면 금액이 같은 출금 거래를 후보로 보여 줍니다.</p>;
  if (candidates === null) return <p className={s.helper}><Loader2 className={s.spin} size={12}/> 출금 거래 후보를 찾는 중</p>;
  if (!candidates.length) return <p className={s.helper}>금액이 같은 출금 거래를 찾지 못했습니다 ({invoice ? "같은 회계연도" : "영수증 3일 전 ~ 45일 후"}). 직접 입력하거나 미지급으로 처리하세요.</p>;
  return <div className={s.candidates}><h4>출금 거래 후보</h4>{candidates.map(c => <div key={c.transaction.id} className={s.candidate}>
    <div><b>{c.transaction.posted_at.slice(0, 16).replace("T", " ")} · {c.transaction.bank_name}</b><span>{c.transaction.description}{c.transaction.kind ? ` · ${c.transaction.kind}` : ""}</span><small>{c.reasons.join(" · ")}</small></div>
    <strong>{money(c.transaction.withdrawal)}원</strong>
    <button type="button" className={s.secondary} disabled={invoice && !!c.linked_receipt_id} onClick={() => onLink(c.transaction)}>{invoice ? "거래처·출금 연결 확인" : c.linked_receipt_id ? "그래도 연결" : "연결"}</button>
  </div>)}</div>;
}

const FIELD_NAMES: Record<string, string> = {
  purpose: "사용 목적", posting_date: "전기일", vendor_code: "거래처 코드", account: "비용 계정", vat: "부가세 처리",
  tax: "공제 부가세", evidence: "증빙 근거", payment: "지급", payment_date: "지급일", bank: "출금 계좌",
};

// Maps a server "missing" label to the form field that fixes it.
function gapField(gap: string): string | null {
  const rules: [RegExp, string][] = [
    [/^OCR 연도/, "date"], [/^거래처 연결 확인/, "vendor_code"],
    [/^거래처명/, "merchant"], [/^결제 금액/, "total"], [/^사용 목적/, "purpose"], [/^거래처 코드/, "vendor_code"],
    [/비용 계정/, "account"], [/전기일/, "posting_date"], [/^부가세 처리/, "vat"], [/공제 부가세/, "tax"],
    [/^증빙/, "evidence"], [/^지급 상태/, "payment"], [/^지급일/, "payment_date"], [/^출금 계좌/, "bank"],
    [/^출금 거래 연결/, "payment_link"], [/기존 채무 전표/, "payable_link"], [/채무 정산/, "payable_offset"], [/^원본/, "checked"],
  ];
  return rules.find(([re]) => re.test(gap))?.[1] ?? null;
}

function focusField(f: string) {
  const label = document.querySelector(`[data-field="${f}"]`)?.closest("label");
  if (!label) return;
  label.scrollIntoView({ behavior: "smooth", block: "center" });
  (label.querySelector("input, select") as HTMLElement | null)?.focus({ preventScroll: true });
}

// Marks a field: "자동" chip when prefilled, and data-missing so CSS can highlight what blocks confirmation.
function Auto({ r, f }: { r: Receipt; f: string }) {
  const why = r.autofilled[f];
  const missing = !r.confirmed && r.missing.some(g => gapField(g) === f);
  return <span data-field={f} data-missing={missing || undefined} className={s.fieldMark}>
    {why && !r.confirmed && (isAi(why)
      ? <small className={s.aiTag} title={why}><Sparkles size={10}/> AI 추천</small>
      : <small className={s.autoTag} title={why}><Sparkles size={10}/> {why.startsWith("추정") ? "추정 · 확인" : "자동"}</small>)}
  </span>;
}
