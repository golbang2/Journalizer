"use client";

import { useState } from "react";
import { Landmark, Loader2, Sparkles, X } from "lucide-react";
import { api, money, type Workspace, type WorkspaceProposal } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

export type PendingImport = { file: File; bank: string }[];

type Props = {
  draft: Workspace;
  onApply: (next: Workspace, pending: PendingImport) => void;
  notify: Notify;
};

/** Reads bank statements and lets the reviewer pick what to copy into 회사 기준. */
export default function StatementSetup({ draft, onApply, notify }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [proposal, setProposal] = useState<WorkspaceProposal | null>(null);
  const [loading, setLoading] = useState(false);
  const [take, setTake] = useState({ company: true, year: true, openings: true, importRows: true, cards: true, accounts: true });
  const [banks, setBanks] = useState<Set<number>>(new Set());
  const [vendors, setVendors] = useState<Record<string, { selected: boolean; account: string }>>({});

  const read = async (picked: File[]) => {
    setLoading(true);
    try {
      const p = await api.proposeWorkspace(picked);
      setFiles(picked);
      setProposal(p);
      setBanks(new Set(p.banks.map((_, i) => i)));
      setVendors(Object.fromEntries(p.vendors.map(v => [v.code, { selected: v.selected, account: v.account }])));
      setTake({ company: !!p.company, year: true, openings: true, importRows: true, cards: true, accounts: true });
    } catch (error) {
      notify((error as Error).message, "error");
    } finally {
      setLoading(false);
    }
  };

  const apply = () => {
    if (!proposal) return;
    const next: Workspace = { ...draft, accounts: [...draft.accounts], banks: [...draft.banks], vendors: [...draft.vendors], cards: [...draft.cards] };
    if (take.accounts) next.accounts.push(...proposal.accounts.filter(a => !next.accounts.some(x => x.code === a.code)));
    if (take.cards) for (const c of proposal.cards) if (!next.cards.some(x => x.code === c.code || x.number === c.number)) next.cards.push({ code: c.code, name: c.name, number: c.number, bank: c.bank, payable: c.payable });
    if (take.company && proposal.company) next.company = proposal.company;
    if (take.year) next.year = proposal.year;
    const chosen = proposal.banks.filter((_, i) => banks.has(i));
    for (const b of chosen) {
      const index = next.banks.findIndex(x => x.code === b.code);
      const row = { code: b.code, name: b.name, number: b.number, account: b.account };
      if (index >= 0) next.banks[index] = { ...next.banks[index], name: b.name, number: b.number || next.banks[index].number };
      else next.banks.push(row);
    }
    if (take.openings) {
      for (const [code, amount] of Object.entries(proposal.openings)) {
        next.accounts = next.accounts.map(a => a.code === code ? { ...a, opening: amount } : a);
      }
    }
    const used = new Set(next.vendors.map(v => v.code));
    let n = 1;
    for (const v of proposal.vendors) {
      const pick = vendors[v.code];
      if (!pick?.selected) continue;
      let code = v.code;
      while (used.has(code)) code = `A${String(n++).padStart(4, "0")}`;
      used.add(code);
      next.vendors.push({ code, name: v.name, alias: "", business: "", account: pick.account, purpose: "", auto: true });
    }
    const pending = take.importRows ? chosen.flatMap(b => files.filter(file => file.name === b.file_name).map(file => ({ file, bank: b.code }))) : [];
    onApply(next, pending);
    setProposal(null);
    notify(`회사 기준에 반영했습니다. 아래 내용을 확인하고 ‘회사 기준 저장’을 누르세요.${pending.length ? " 저장할 때 거래 내역도 함께 가져옵니다." : ""}`);
  };

  const expenseAccounts = draft.accounts.filter(a => a.kind === "expense");
  const selectedCount = Object.values(vendors).filter(v => v.selected).length;
  const accountName = (code: string) => draft.accounts.find(a => a.code === code)?.name ?? code;

  if (!proposal) {
    return <section className={`${s.card} ${s.setupCard}`}>
      <div className={s.sourceIcon}><Landmark size={22}/></div>
      <div className={s.setupText}><h2>은행 거래내역으로 자동 채우기</h2><p>은행에서 내려받은 거래내역조회 파일(.xlsx, .xls)을 올리면 회사명, 회계연도, 은행 계좌, 보통예금 기초잔액, 거래처를 읽어 채웁니다. 반영 전에 미리 보여 드립니다.</p></div>
      <label className={s.upload}>{loading ? <><Loader2 className={s.spin} size={13}/> 읽는 중…</> : "거래내역 파일 선택 (여러 개 가능)"}
        <input aria-label="회사 기준용 은행 거래내역 파일 선택" type="file" multiple disabled={loading} accept=".xlsx,.xls,.csv" onChange={e => { const picked = Array.from(e.target.files || []); e.target.value = ""; if (picked.length) read(picked); }}/></label>
    </section>;
  }

  return <section className={`${s.card} ${s.proposal}`}>
    <div className={s.cardHeading}><div><div className={s.eyebrow}><Sparkles size={11}/> 거래내역 {files.length}개에서 읽은 내용</div><h2>회사 기준에 반영할 내용을 고르세요</h2><p>체크한 항목만 아래 표에 채웁니다. 반영 후에도 ‘회사 기준 저장’을 눌러야 저장됩니다.</p></div>
      <button className={s.iconButton} aria-label="미리보기 닫기" onClick={() => setProposal(null)}><X size={16}/></button></div>

    <div className={s.proposalGrid}>
      <label className={s.pick}><input type="checkbox" checked={take.company} disabled={!proposal.company} onChange={e => setTake(t => ({ ...t, company: e.target.checked }))}/>
        <span><small>회사명 (예금주)</small><b>{proposal.company || "찾지 못함"}</b>{draft.company && draft.company !== proposal.company && <em>현재: {draft.company}</em>}</span></label>
      <label className={s.pick}><input type="checkbox" checked={take.year} onChange={e => setTake(t => ({ ...t, year: e.target.checked }))}/>
        <span><small>회계연도</small><b>{proposal.year}년</b><em>{proposal.year_note}</em></span></label>
      {Object.entries(proposal.openings).map(([code, amount]) => <label key={code} className={s.pick}><input type="checkbox" checked={take.openings} onChange={e => setTake(t => ({ ...t, openings: e.target.checked }))}/>
        <span><small>{code} {accountName(code)} 기초잔액</small><b>{money(amount)}원</b><em>계좌별 첫 거래 직전 잔액의 합</em></span></label>)}
    </div>

    {(proposal.cards.length > 0 || proposal.accounts.length > 0) && <div className={s.proposalGrid}>
      {proposal.cards.map(c => <label key={c.code + c.number} className={s.pick}><input type="checkbox" checked={take.cards} onChange={e => setTake(t => ({ ...t, cards: e.target.checked }))}/>
        <span><small>카드 ({c.existing ? "등록됨" : "새 카드"})</small><b>{c.name} {c.number} · {c.code}</b><em>‘{c.number} 가맹점’ 거래 {c.rows}건 · 미지급금 경유</em></span></label>)}
      {proposal.accounts.length > 0 && <label className={s.pick}><input type="checkbox" checked={take.accounts} onChange={e => setTake(t => ({ ...t, accounts: e.target.checked }))}/>
        <span><small>통장 거래·결산에 필요한 계정</small><b>{proposal.accounts.length}개 추가</b><em>{proposal.accounts.map(a => a.name).join(", ")}</em></span></label>}
    </div>}

    <h3 className={s.proposalTitle}>은행 계좌</h3>
    <div className={s.tableWrap}><table><thead><tr><th/><th>은행</th><th>계좌번호</th><th>상품</th><th>코드</th><th>기간</th><th>기초잔액</th></tr></thead><tbody>
      {proposal.banks.map((b, i) => <tr key={b.file_name}>
        <td><input type="checkbox" aria-label={`${b.name} 계좌 반영`} checked={banks.has(i)} onChange={() => setBanks(set => { const next = new Set(set); if (next.has(i)) next.delete(i); else next.add(i); return next; })}/></td>
        <td>{b.name}<small className={s.tableSub}>{b.existing ? "기존 계좌에 계좌번호 추가" : "새 계좌"}</small></td><td>{b.number || "—"}</td><td>{b.product || "—"}</td><td>{b.code}</td>
        <td>{b.first} ~ {b.last}<small className={s.tableSub}>{b.rows}건</small></td><td>{b.opening === null ? "—" : `${money(b.opening)}원`}<small className={s.tableSub}>{b.opening_note}</small></td></tr>)}
    </tbody></table></div>

    <h3 className={s.proposalTitle}>거래처 <small>선택 {selectedCount} / {proposal.vendors.length} · 거래 2회 이상이거나 5만 원 이상인 곳을 미리 골랐습니다</small>
      <span className={s.proposalActions}><button className={s.linkButton} onClick={() => setVendors(v => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, { ...x, selected: true }])))}>모두 선택</button><button className={s.linkButton} onClick={() => setVendors(v => Object.fromEntries(Object.entries(v).map(([k, x]) => [k, { ...x, selected: false }])))}>모두 해제</button></span></h3>
    <div className={`${s.tableWrap} ${s.vendorPick}`}><table><thead><tr><th/><th>거래 상대방</th><th>거래</th><th>출금 합계</th><th>기본 계정 (추정)</th><th>참고</th></tr></thead><tbody>
      {proposal.vendors.map(v => <tr key={v.code} className={vendors[v.code]?.selected ? undefined : s.dimRow}>
        <td><input type="checkbox" aria-label={`${v.name} 거래처 반영`} checked={!!vendors[v.code]?.selected} onChange={e => setVendors(x => ({ ...x, [v.code]: { ...x[v.code], selected: e.target.checked } }))}/></td>
        <td>{v.name}</td><td>{v.count}건</td><td>{v.withdrawal ? `${money(v.withdrawal)}원` : `입금 ${money(v.deposit)}원`}</td>
        <td><select aria-label={`${v.name} 기본 계정`} value={vendors[v.code]?.account ?? ""} onChange={e => setVendors(x => ({ ...x, [v.code]: { ...x[v.code], account: e.target.value } }))}><option value="">없음</option>{expenseAccounts.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select>
          {v.account_reason && vendors[v.code]?.account === v.account && <small className={s.tableSub}>{v.account_reason}</small>}</td>
        <td><small className={s.noteText}>{v.note}</small></td></tr>)}
    </tbody></table></div>

    <label className={s.checkbox}><input type="checkbox" checked={take.importRows} onChange={e => setTake(t => ({ ...t, importRows: e.target.checked }))}/> 저장할 때 이 파일의 입출금 거래 {proposal.banks.filter((_, i) => banks.has(i)).reduce((sum, b) => sum + b.rows, 0)}건도 함께 가져오기 (영수증 출금 연결에 사용)</label>
    <div className={s.bulkActions}><button className={s.secondary} onClick={() => setProposal(null)}>취소</button><button className={s.primary} onClick={apply}>회사 기준에 반영</button></div>
  </section>;
}
