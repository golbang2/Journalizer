"use client";

import { useEffect, useState } from "react";
import { api, type Account, type AccountingPolicy, type PolicyInput, type PolicyRule } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

const blankRule = (): PolicyRule => ({ terms: [], account: "", exceptions: [], direction: "", document_type: "", minimum: 0, maximum: null });
const blank = (): PolicyInput => ({ title: "", series: "", version: "1", kind: "accounting", source: "", locator: "", text: "", effective_from: "", effective_to: null, rules: [blankRule()] });
const labels = { draft: "검토 대기", approved: "승인", retired: "철회" };
const split = (text: string) => text.split(",");

export default function PolicyPanel({ accounts, notify }: { accounts: Account[]; notify: Notify }) {
  const [policies, setPolicies] = useState<AccountingPolicy[]>([]);
  const [draft, setDraft] = useState<PolicyInput>(blank);
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api.policies().then(setPolicies).catch(e => setError((e as Error).message));
  }, []);
  const set = (patch: Partial<PolicyInput>) => setDraft(d => ({ ...d, ...patch }));
  const rule = (index: number, patch: Partial<PolicyRule>) => setDraft(d => ({ ...d, rules: d.rules.map((r, i) => i === index ? { ...r, ...patch } : r) }));
  const run = async (operation: () => Promise<unknown>, message: string) => {
    setBusy(true); setError("");
    try {
      await operation();
      setPolicies(await api.policies());
      notify(message);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const save = () => run(async () => {
    if (editing) await api.updatePolicy(editing, draft);
    else await api.createPolicy(draft);
    setDraft(blank()); setEditing("");
  }, "정책 초안을 저장했습니다. 원문과 조건을 확인한 뒤 승인하세요.");
  return <section className={s.card}>
    <div className={s.cardHeading}><div><h2>회계처리 정책 · GraphRAG</h2><p>정책 원문과 검색 조건을 연결해 거래별 추천 근거로 사용합니다. 이 영역은 회사 기준 저장과 별도로 저장됩니다.</p></div></div>
    <p className={s.helper}>원문을 붙여 넣고 조건을 직접 검토하세요. 검색어 중 하나가 일치하고 금액·방향·증빙 조건을 모두 충족하면 조회합니다. 승인된 정책만 거래일 기준으로 적용하며, 변경 후 기존 추천은 재실행해야 합니다.</p>
    {error && <p role="alert">{error}</p>}
    <fieldset disabled={busy} className={s.policyForm}>
      <legend>{editing ? "정책 초안 수정" : "정책 버전 등록"}</legend>
      <div className={s.fields}>
        <label>정책명<input value={draft.title} maxLength={200} onChange={e => set({ title: e.target.value })}/></label>
        <label>정책 식별명<input value={draft.series} placeholder="예: 소프트웨어 구독 정책 (버전 간 동일하게 입력)" onChange={e => set({ series: e.target.value })}/></label>
        <label>버전<input value={draft.version} onChange={e => set({ version: e.target.value })}/></label>
        <label>정책 종류<select value={draft.kind} onChange={e => set({ kind: e.target.value as PolicyInput["kind"] })}><option value="accounting">회계정책</option><option value="tax">세무 참고 (담당자 검토 필요)</option></select></label>
        <label>적용 시작일<input type="date" value={draft.effective_from} onChange={e => set({ effective_from: e.target.value })}/></label>
        <label>적용 종료일 (선택)<input type="date" value={draft.effective_to ?? ""} onChange={e => set({ effective_to: e.target.value || null })}/></label>
        <label>출처 문서명·주소<input value={draft.source} placeholder="예: 회사 회계처리 지침" onChange={e => set({ source: e.target.value })}/></label>
        <label>원문 위치<input value={draft.locator} placeholder="예: 3페이지 제2조" onChange={e => set({ locator: e.target.value })}/></label>
      </div>
      <label className={s.policyText}>정책 원문<textarea rows={6} maxLength={20000} value={draft.text} onChange={e => set({ text: e.target.value })}/></label>
      {draft.rules.map((r, i) => <fieldset key={i} className={s.policyForm}>
        <legend>검색 조건 {i + 1}</legend><div className={s.fields}>
          <label>검색어 (쉼표로 구분)<input value={r.terms.join(",")} placeholder="예: 소프트웨어,구독,라이선스" onChange={e => rule(i, { terms: split(e.target.value) })}/></label>
          <label>연결 계정<select value={r.account} onChange={e => rule(i, { account: e.target.value })}><option value="">계정 선택</option>{accounts.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label>
          <label>예외 검색어 (쉼표로 구분)<input value={r.exceptions.join(",")} placeholder="예외가 있으면 항상 담당자 검토" onChange={e => rule(i, { exceptions: split(e.target.value) })}/></label>
          <label>입출금<select value={r.direction} onChange={e => rule(i, { direction: e.target.value as PolicyRule["direction"] })}><option value="">모두</option><option>출금</option><option>입금</option></select></label>
          <label>증빙 종류<select value={r.document_type} onChange={e => rule(i, { document_type: e.target.value as PolicyRule["document_type"] })}><option value="">제한 없음</option><option value="receipt">영수증</option><option value="tax_invoice">세금계산서</option></select></label>
          <label>최소 금액 (원)<input type="number" min={0} value={r.minimum} onChange={e => rule(i, { minimum: Number(e.target.value) })}/></label>
          <label>최대 금액 (원, 선택)<input type="number" min={0} value={r.maximum ?? ""} onChange={e => rule(i, { maximum: e.target.value === "" ? null : Number(e.target.value) })}/></label>
        </div>{draft.rules.length > 1 && <button className={s.linkButton} onClick={() => set({ rules: draft.rules.filter((_, j) => j !== i) })}>조건 삭제</button>}
      </fieldset>)}
      <div className={s.policyActions}>
        <button className={s.secondary} onClick={() => set({ rules: [...draft.rules, blankRule()] })}>조건 추가</button>
        <button className={s.primary} onClick={save}>{busy ? "저장 중…" : "초안 저장"}</button>
        {editing && <button className={s.secondary} onClick={() => { setEditing(""); setDraft(blank()); }}>수정 취소</button>}
      </div>
    </fieldset>
    {!policies.length && <p className={s.helper}>등록된 정책이 없습니다.</p>}
    {policies.map(p => <details key={p.id} className={s.policyRecord}>
      <summary>{p.title} · {p.version} · {labels[p.status]} · {p.effective_from} ~ {p.effective_to ?? "종료일 없음"}</summary>
      <p>{p.kind === "tax" ? "세무 참고" : "회계정책"} · {p.series} · {p.source} · {p.locator}</p>
      <p className={s.policyOriginal}>{p.text}</p>
      <ul>{p.rules.map((r, i) => <li key={i}>{r.terms.join(" / ")} → {r.account} · {r.direction || "입출금 모두"} · {r.document_type || "증빙 제한 없음"} · {r.minimum}~{r.maximum ?? "상한 없음"}원 · 예외: {r.exceptions.join(" / ") || "등록 없음"}</li>)}</ul>
      <small>등록 {p.created_at}{p.approved_at && ` · 승인 ${p.approved_at}`}</small>
      <div className={s.policyActions}>
        {p.status === "draft" && <>
          <button disabled={busy} className={s.secondary} onClick={() => { setEditing(p.id); setDraft(structuredClone(p)); }}>초안 수정</button>
          <button disabled={busy || editing === p.id} className={s.primary} onClick={() => run(() => api.policyAction(p.id, "approve"), "정책을 승인했습니다. 다음 추천부터 적용됩니다.")}>원문·조건 확인 후 승인</button>
        </>}
        {p.status !== "retired" && <button disabled={busy} className={s.secondary} onClick={() => run(() => api.policyAction(p.id, "retire"), "정책을 철회했습니다. 이전 추천 근거는 보존됩니다.")}>철회</button>}
        <button disabled={busy} className={s.secondary} onClick={() => { setEditing(""); setDraft({ ...structuredClone(p), version: "" }); }}>새 버전 작성</button>
      </div>
    </details>)}
  </section>;
}
