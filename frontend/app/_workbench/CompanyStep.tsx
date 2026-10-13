"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowRight, FileText, Loader2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, money, type Account, type BankAccount, type Card, type Vendor, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import StatementSetup, { type PendingImport } from "./StatementSetup";
import MoneyInput from "./MoneyInput";
import PolicyPanel from "./PolicyPanel";
import s from "./workbench.module.css";

const kinds: [Account["kind"], string][] = [["expense", "비용"], ["revenue", "수익"], ["asset", "자산"], ["liability", "부채"], ["equity", "자본"], ["other", "기타"]];
const thisYear = new Date().getFullYear();
const years = Array.from({ length: 8 }, (_, i) => String(thisYear - 5 + i));
const mmdd = (v = "") => /^\d{2}-\d{2}$/.test(v) ? v : "";

/** "2026.01.01 ~ 2026.12.31", following the 사업연도 on the 사업자등록증 when it was read. */
function period(year: string, start = "", end = "") {
  const [from, to] = [mmdd(start) || "01-01", mmdd(end) || "12-31"];
  const endYear = to < from ? String(Number(year) + 1) : year;
  return { text: `${year}.${from.replace("-", ".")} ~ ${endYear}.${to.replace("-", ".")}`, calendar: from === "01-01" && to === "12-31" };
}

type Props = { workspace: Workspace; onSaved: (ws: Workspace) => Promise<void>; onTransactionsChanged: () => Promise<void>; notify: Notify; onNext: () => void };

export default function CompanyStep({ workspace, onSaved, onTransactionsChanged, notify, onNext }: Props) {
  const [draft, setDraft] = useState(workspace);
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<PendingImport>([]);
  useEffect(() => setDraft(workspace), [workspace]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(workspace) || pending.length > 0;

  const set = (patch: Partial<Workspace>) => setDraft(d => ({ ...d, ...patch }));
  const editRow = <K extends "accounts" | "vendors" | "banks" | "cards">(key: K, index: number, patch: Partial<Workspace[K][number]>) =>
    setDraft(d => ({ ...d, [key]: (d[key] as Workspace[K][number][]).map((row, i) => i === index ? { ...row, ...patch } : row) }));
  const removeRow = (key: "accounts" | "vendors" | "banks" | "cards", index: number) => setDraft(d => ({ ...d, [key]: d[key].filter((_, i) => i !== index) }));

  const [busyLedger, setBusyLedger] = useState(false);
  const learnLedger = async (file: File) => {
    setBusyLedger(true);
    try {
      const result = await api.uploadPrecedents(file);
      const ws = await api.workspace();
      setDraft(d => ({ ...d, precedents: ws.precedents, precedent_source: ws.precedent_source }));
      await onSaved({ ...workspace, precedents: ws.precedents, precedent_source: ws.precedent_source });
      notify(`${result.source}에서 ${result.rows}건의 기장, ${result.count}종의 적요·거래처 습관을 학습했습니다.`);
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setBusyLedger(false);
    }
  };
  const forgetLedger = async () => {
    await api.deletePrecedents();
    setDraft(d => ({ ...d, precedents: [], precedent_source: "" }));
    await onSaved({ ...workspace, precedents: [], precedent_source: "" });
  };

  const [reading, setReading] = useState(false);
  const readProfile = async (quiet = false) => {
    setReading(true);
    try {
      await onSaved(await api.readProfile());
      notify("등록한 회사 서류에서 회사 정보를 읽었습니다.");
    } catch (error) {
      if (!quiet) notify((error as Error).message, "error");
      await onSaved(await api.workspace()).catch(() => undefined);  // shows the stored reading error
    } finally {
      setReading(false);
    }
  };
  // Documents uploaded at signup but not read yet (e.g. no AI key then): read them when this tab opens.
  const autoRead = useRef(false);
  useEffect(() => {
    if (workspace.profile === null && !autoRead.current) { autoRead.current = true; readProfile(true); }
  }, [workspace.profile]); // eslint-disable-line react-hooks/exhaustive-deps

  const profile = workspace.profile;
  const fiscal = period(draft.year, profile?.fiscal_start, profile?.fiscal_end);
  const facts = profile && !profile.error ? ([
    ["상호", profile.company], ["사업자등록번호", profile.business_number], ["대표자", profile.representative],
    ["본점 소재지", profile.address ?? ""], ["개업일", profile.opened], ["업태·종목", profile.business_type],
    ["사업연도", mmdd(profile.fiscal_start) && mmdd(profile.fiscal_end) ? `${profile.fiscal_start} ~ ${profile.fiscal_end}` : ""],
    ["자본금", profile.capital ? `${money(profile.capital)}원` : ""], ["주주", profile.shareholders.join(", ")], ["임원", profile.officers.join(", ")],
  ] as [string, string][]).filter(([, v]) => v) : [];

  const save = async () => {
    setSaving(true);
    try {
      await onSaved(await api.saveWorkspace(draft));
      let message = "회사 기준을 저장했습니다.";
      if (pending.length) {
        // Bank codes now exist on the server, so the statements can be imported against them.
        let added = 0, linked = 0;
        for (const { file, bank } of pending) {
          const result = await api.importStatement(file, bank);
          added += result.added;
          linked += result.autofilled;
        }
        setPending([]);
        await onTransactionsChanged();
        message += ` 은행 거래 ${added}건을 가져왔습니다${linked ? ` (영수증 ${linked}건 자동 입력)` : ""}.`;
      }
      notify(message + " 검토 중인 영수증의 누락 항목도 다시 계산했습니다.");
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  return <>
    <PolicyPanel accounts={workspace.accounts} notify={notify}/>
    <StatementSetup draft={draft} onApply={(next, files) => { setDraft(next); setPending(files); }} notify={notify}/>
    <section className={s.card}><div className={s.cardHeading}><h2>회사 · 회계 기간</h2><span>기본 정보</span></div><div className={s.fields}>
      <label>회사명<input value={draft.company} placeholder="예: 주식회사 더블락" onChange={e => set({ company: e.target.value })}/></label>
      <label>회계연도<select value={draft.year} onChange={e => set({ year: e.target.value })}>{Array.from(new Set([...years, draft.year])).sort().map(y => <option key={y}>{y}</option>)}</select></label>
      <label>기장 통화<input value="KRW · 대한민국 원" readOnly/></label>
    </div><p className={s.helper}>기간: {fiscal.text} · 영수증 거래일과 전기일은 따로 관리합니다.
      {!fiscal.calendar && " 서류의 사업연도가 1월~12월이 아닙니다. 장부는 현재 회계연도의 1월~12월 거래로 집계하니 확인하세요."}</p>
    <div className={s.cardHeading}><div><h3><FileText size={14}/> 등록 서류에서 읽은 회사 정보</h3><p>회원 가입 때 올린 등기부등본·주주명부·사업자등록증 기준입니다.</p></div>
      <span>
        {profile?.company && profile.company !== draft.company && <button className={s.secondary} onClick={() => set({ company: profile.company })}>회사명을 서류 상호로</button>}
        <button className={s.secondary} disabled={reading || dirty} title={dirty ? "변경 사항을 저장하거나 되돌린 뒤 다시 읽으세요." : undefined} onClick={() => readProfile()}>
          {reading ? <Loader2 className={s.spin} size={14}/> : <RefreshCw size={14}/>} 서류 다시 읽기</button>
      </span></div>
    {reading && !facts.length ? <p className={s.helper}><Loader2 className={s.spin} size={12}/> 서류를 읽는 중입니다</p>
      : profile?.error ? <p className={s.helper}>서류를 읽지 못했습니다: {profile.error}</p>
      : facts.length ? <div className={s.proposalGrid}>{facts.map(([k, v]) => <div key={k} className={s.pick}><span><small>{k}</small><b>{v}</b></span></div>)}</div>
      : <p className={s.helper}>아직 읽은 회사 정보가 없습니다. 메인 화면의 ‘AI 연결’을 마친 뒤 ‘서류 다시 읽기’를 누르세요.</p>}
    </section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>회사 계정과 기초잔액</h2><p>분류가 ‘비용’인 계정만 영수증 비용 계정으로 고를 수 있습니다.</p></div>
      <button className={s.secondary} onClick={() => set({ accounts: [...draft.accounts, { code: "", name: "", normal: "debit", opening: 0, kind: "expense" }] })}><Plus size={14}/> 계정 추가</button></div>
      <div className={s.tableWrap}><table><thead><tr><th>코드</th><th>계정명</th><th>분류</th><th>잔액 기준</th><th>기초잔액 (원)</th><th/></tr></thead><tbody>
        {draft.accounts.map((a, i) => <tr key={i}>
          <td><input aria-label={`${i + 1}번 계정 코드`} className={s.mono} value={a.code} onChange={e => editRow("accounts", i, { code: e.target.value.trim() })}/></td>
          <td><input aria-label={`${a.code} 계정명`} value={a.name} onChange={e => editRow("accounts", i, { name: e.target.value })}/></td>
          <td><select aria-label={`${a.code} 분류`} value={a.kind} onChange={e => editRow("accounts", i, { kind: e.target.value as Account["kind"] })}>{kinds.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
          <td><select aria-label={`${a.code} 잔액 기준`} value={a.normal} onChange={e => editRow("accounts", i, { normal: e.target.value as Account["normal"] })}><option value="debit">차변</option><option value="credit">대변</option></select></td>
          <td><MoneyInput aria-label={`${a.code} 기초잔액`} allowNegative value={a.opening} onChange={v => editRow("accounts", i, { opening: v })}/></td>
          <td><button className={s.iconButton} aria-label={`${a.code} 계정 삭제`} onClick={() => removeRow("accounts", i)}><Trash2 size={14}/></button></td>
        </tr>)}
      </tbody></table></div></section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>회사 은행 계좌</h2><p>거래내역 파일을 올리면 계좌번호나 은행 이름으로 이 목록에서 찾아 연결합니다. 코드는 보통예금 전표의 거래처 코드로 쓰입니다.</p></div>
      <button className={s.secondary} onClick={() => set({ banks: [...draft.banks, { code: "", name: "", number: "", account: "10300" }] })}><Plus size={14}/> 계좌 추가</button></div>
      <div className={s.tableWrap}><table><thead><tr><th>거래처 코드</th><th>은행</th><th>계좌번호</th><th>연결 계정</th><th/></tr></thead><tbody>
        {draft.banks.map((b: BankAccount, i) => <tr key={i}>
          <td><input aria-label={`${i + 1}번 계좌 코드`} value={b.code} onChange={e => editRow("banks", i, { code: e.target.value.trim() })}/></td>
          <td><input aria-label={`${i + 1}번 계좌 은행`} value={b.name} placeholder="예: 우리은행" onChange={e => editRow("banks", i, { name: e.target.value })}/></td>
          <td><input aria-label={`${i + 1}번 계좌번호`} value={b.number} placeholder="선택 입력" onChange={e => editRow("banks", i, { number: e.target.value })}/></td>
          <td><select aria-label={`${i + 1}번 계좌 연결 계정`} value={b.account} onChange={e => editRow("banks", i, { account: e.target.value })}>{draft.accounts.filter(a => a.kind === "asset").map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></td>
          <td><button className={s.iconButton} aria-label={`${b.name} 계좌 삭제`} onClick={() => removeRow("banks", i)}><Trash2 size={14}/></button></td>
        </tr>)}
      </tbody></table></div></section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>회계 처리 기준</h2><p>AI와 자동 처리가 따르는 회사 기준입니다.</p></div></div>
      <div className={s.fields}>
        <label>카드 영수증 부가세<select value={draft.card_vat} onChange={e => set({ card_vat: e.target.value as Workspace["card_vat"] })}>
          <option value="deduct">공제 요건을 갖춘 카드 영수증은 부가세 분리 (부가세 신고와 맞춤 · 권장)</option>
          <option value="expense">카드 영수증 부가세는 비용에 포함 (카드 매입세액을 신고하지 않는 회사)</option></select></label>
        <div className={s.precedentBox}><small>이전 연도 계정별원장 (회사 분류 습관 학습)</small>
          {draft.precedents.length ? <b>{draft.precedent_source} · 적요·거래처 {draft.precedents.length}종 학습</b> : <b>없음</b>}
          <span>
            <label className={s.linkButton}>{busyLedger ? "읽는 중…" : "계정별원장 엑셀 올리기"}<input className={s.hiddenInput} type="file" accept=".xls,.xlsx" disabled={busyLedger}
              onChange={e => { const f = e.target.files?.[0]; e.target.value = ""; if (f) learnLedger(f); }}/></label>
            {draft.precedents.length > 0 && <button className={s.linkButton} onClick={forgetLedger}>지우기</button>}
          </span></div>
      </div>
      <p className={s.helper}>이전 연도 원장을 올리면 같은 적요·거래처를 그 해에 어떤 계정으로 기장했는지 AI가 참고합니다. 같은 연도의 원장을 올리면 정답을 미리 보여 주는 셈이니 비교 평가용으로는 쓰지 마세요.</p>
    </section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>회사 카드</h2><p>통장에 ‘2462 가맹점’처럼 카드번호 네 자리로 찍히는 거래를 카드 사용으로 봅니다. 체크카드는 바로 출금되므로 비용/보통예금으로 분개합니다. 신용카드처럼 나중에 결제되면 ‘미지급금 경유’를 켜세요.</p></div>
      <button className={s.secondary} onClick={() => set({ cards: [...draft.cards, { code: "", name: "", number: "", bank: draft.banks[0]?.code ?? "", payable: false }] })}><Plus size={14}/> 카드 추가</button></div>
      <div className={s.tableWrap}><table><thead><tr><th>거래처 코드</th><th>카드 이름</th><th>카드번호 끝 4자리</th><th>결제 계좌</th><th>미지급금 경유</th><th/></tr></thead><tbody>
        {draft.cards.map((c: Card, i) => <tr key={i}>
          <td><input aria-label={`${i + 1}번 카드 코드`} value={c.code} placeholder="예: 98001" onChange={e => editRow("cards", i, { code: e.target.value.trim() })}/></td>
          <td><input aria-label={`${i + 1}번 카드 이름`} value={c.name} placeholder="예: 광주은행카드" onChange={e => editRow("cards", i, { name: e.target.value })}/></td>
          <td><input aria-label={`${i + 1}번 카드번호`} value={c.number} maxLength={4} onChange={e => editRow("cards", i, { number: e.target.value.replace(/\D/g, "") })}/></td>
          <td><select aria-label={`${i + 1}번 카드 결제 계좌`} value={c.bank} onChange={e => editRow("cards", i, { bank: e.target.value })}><option value="">모든 계좌</option>{draft.banks.map(b => <option key={b.code} value={b.code}>{b.name} ({b.code})</option>)}</select></td>
          <td><input type="checkbox" aria-label={`${i + 1}번 카드 미지급금 경유`} checked={c.payable} onChange={e => editRow("cards", i, { payable: e.target.checked })}/></td>
          <td><button className={s.iconButton} aria-label={`${c.name} 카드 삭제`} onClick={() => removeRow("cards", i)}><Trash2 size={14}/></button></td>
        </tr>)}
        {!draft.cards.length && <tr><td colSpan={6} className={s.empty}>등록된 카드가 없습니다. 거래내역으로 자동 채우기를 하면 카드번호를 찾아 제안합니다.</td></tr>}
      </tbody></table></div></section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>거래처 기준 정보</h2><p>상호와 입출금 표시명(별칭)이 달라도 같은 거래처로 연결합니다. 기본 계정·적요는 영수증을 확정하면 자동으로 학습되고, 같은 거래처의 새 영수증에 미리 채워집니다.</p></div>
      <button className={s.secondary} onClick={() => set({ vendors: [...draft.vendors, { code: "", name: "", alias: "", business: "", account: "", purpose: "", auto: false }] })}><Plus size={14}/> 거래처 추가</button></div>
      <div className={s.tableWrap}><table><thead><tr><th>거래처 코드</th><th>정식 상호</th><th>상호·수취인 별칭</th><th>사업자등록번호</th><th>기본 계정</th><th>기본 적요</th><th/></tr></thead><tbody>
        {draft.vendors.map((v: Vendor, i) => <tr key={i}>
          {(["code", "name", "alias", "business"] as const).map(key => <td key={key}><input aria-label={`거래처 ${i + 1} ${key}`} value={v[key]} placeholder="입력" onChange={e => editRow("vendors", i, { [key]: e.target.value, auto: false })}/>{key === "code" && v.auto && <small className={s.autoTag} title="영수증에서 자동으로 등록했습니다. 확인 후 수정하세요.">자동 등록</small>}</td>)}
          <td><select aria-label={`거래처 ${i + 1} 기본 계정`} value={v.account} onChange={e => editRow("vendors", i, { account: e.target.value })}><option value="">없음</option>{draft.accounts.filter(a => a.kind === "expense").map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></td>
          <td><input aria-label={`거래처 ${i + 1} 기본 적요`} value={v.purpose} placeholder="예: 외주 개발 용역" onChange={e => editRow("vendors", i, { purpose: e.target.value })}/></td>
          <td><button className={s.iconButton} aria-label={`거래처 ${i + 1} 삭제`} onClick={() => removeRow("vendors", i)}><Trash2 size={14}/></button></td></tr>)}
        {!draft.vendors.length && <tr><td colSpan={7} className={s.empty}>등록된 거래처가 없습니다. 영수증을 올리면 자동으로 등록됩니다.</td></tr>}
      </tbody></table></div></section>

    <div className={s.stickyActions}>
      {dirty && <span>저장하지 않은 변경 사항이 있습니다.</span>}
      {pending.length > 0 && <span>저장하면 거래내역 {pending.length}개 파일도 함께 가져옵니다.</span>}
      <button className={s.secondary} disabled={!dirty || saving} onClick={() => { setDraft(workspace); setPending([]); }}>되돌리기</button>
      <button className={s.primary} disabled={!dirty || saving} onClick={save}><Save size={16}/> {saving ? "저장 중" : "회사 기준 저장"}</button>
      <button className={s.secondary} disabled={dirty} onClick={onNext}>자료 연결로 이동 <ArrowRight size={16}/></button>
    </div>
  </>;
}
