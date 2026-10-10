"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Plus, Save, Trash2 } from "lucide-react";
import { api, type Account, type BankAccount, type Vendor, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import StatementSetup, { type PendingImport } from "./StatementSetup";
import s from "./workbench.module.css";

const kinds: [Account["kind"], string][] = [["expense", "비용"], ["asset", "자산"], ["liability", "부채"], ["other", "기타"]];
const thisYear = new Date().getFullYear();
const years = Array.from({ length: 8 }, (_, i) => String(thisYear - 5 + i));

type Props = { workspace: Workspace; onSaved: (ws: Workspace) => Promise<void>; onTransactionsChanged: () => Promise<void>; notify: Notify; onNext: () => void };

export default function CompanyStep({ workspace, onSaved, onTransactionsChanged, notify, onNext }: Props) {
  const [draft, setDraft] = useState(workspace);
  const [saving, setSaving] = useState(false);
  const [pending, setPending] = useState<PendingImport>([]);
  useEffect(() => setDraft(workspace), [workspace]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(workspace) || pending.length > 0;

  const set = (patch: Partial<Workspace>) => setDraft(d => ({ ...d, ...patch }));
  const editRow = <K extends "accounts" | "vendors" | "banks">(key: K, index: number, patch: Partial<Workspace[K][number]>) =>
    setDraft(d => ({ ...d, [key]: (d[key] as Workspace[K][number][]).map((row, i) => i === index ? { ...row, ...patch } : row) }));
  const removeRow = (key: "accounts" | "vendors" | "banks", index: number) => setDraft(d => ({ ...d, [key]: d[key].filter((_, i) => i !== index) }));

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
    <StatementSetup draft={draft} onApply={(next, files) => { setDraft(next); setPending(files); }} notify={notify}/>
    <section className={s.card}><div className={s.cardHeading}><h2>회사 · 회계 기간</h2><span>기본 정보</span></div><div className={s.fields}>
      <label>회사명<input value={draft.company} placeholder="예: 주식회사 더블락" onChange={e => set({ company: e.target.value })}/></label>
      <label>회계연도<select value={draft.year} onChange={e => set({ year: e.target.value })}>{Array.from(new Set([...years, draft.year])).sort().map(y => <option key={y}>{y}</option>)}</select></label>
      <label>기장 통화<input value="KRW · 대한민국 원" readOnly/></label>
    </div><p className={s.helper}>기간: {draft.year}.01.01 ~ {draft.year}.12.31 · 영수증 거래일과 전기일은 따로 관리합니다.</p></section>

    <section className={s.card}><div className={s.cardHeading}><div><h2>회사 계정과 기초잔액</h2><p>분류가 ‘비용’인 계정만 영수증 비용 계정으로 고를 수 있습니다.</p></div>
      <button className={s.secondary} onClick={() => set({ accounts: [...draft.accounts, { code: "", name: "", normal: "debit", opening: 0, kind: "expense" }] })}><Plus size={14}/> 계정 추가</button></div>
      <div className={s.tableWrap}><table><thead><tr><th>코드</th><th>계정명</th><th>분류</th><th>잔액 기준</th><th>기초잔액 (원)</th><th/></tr></thead><tbody>
        {draft.accounts.map((a, i) => <tr key={i}>
          <td><input aria-label={`${i + 1}번 계정 코드`} className={s.mono} value={a.code} onChange={e => editRow("accounts", i, { code: e.target.value.trim() })}/></td>
          <td><input aria-label={`${a.code} 계정명`} value={a.name} onChange={e => editRow("accounts", i, { name: e.target.value })}/></td>
          <td><select aria-label={`${a.code} 분류`} value={a.kind} onChange={e => editRow("accounts", i, { kind: e.target.value as Account["kind"] })}>{kinds.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
          <td><select aria-label={`${a.code} 잔액 기준`} value={a.normal} onChange={e => editRow("accounts", i, { normal: e.target.value as Account["normal"] })}><option value="debit">차변</option><option value="credit">대변</option></select></td>
          <td><input aria-label={`${a.code} 기초잔액`} type="number" value={a.opening} onChange={e => editRow("accounts", i, { opening: Number(e.target.value) || 0 })}/></td>
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
