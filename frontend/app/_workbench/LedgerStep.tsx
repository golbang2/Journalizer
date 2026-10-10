"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Download, FileSpreadsheet, FileText, Loader2 } from "lucide-react";
import { api, ledgerCsvUrl, money, workbookUrl, type LedgerView, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

type Props = { workspace: Workspace; confirmed: number; account: string; onAccount: (code: string) => void; notify: Notify; onSources: () => void };

export default function LedgerStep({ workspace, confirmed, account, onAccount, notify, onSources }: Props) {
  const [view, setView] = useState<LedgerView | null>(null);

  useEffect(() => {
    if (!account) return;
    let live = true;
    setView(null);
    api.ledger(account).then(v => live && setView(v)).catch(error => live && notify((error as Error).message, "error"));
    return () => { live = false; };
  }, [account, notify]);

  return <>
    <section className={s.card}>
      <div className={s.cardHeading}><div><h2>{workspace.company || "회사"} · {workspace.year} 계정별원장</h2><p>확정 전표 {confirmed}건으로 만든 원장입니다. 영수증 외 거래(매출, 자본금, 결산 조정)는 아직 포함하지 않습니다.</p></div>
        <div className={s.headingActions}>
          <a className={s.secondary} href={account ? ledgerCsvUrl(account) : undefined}><Download size={16}/> 이 계정 CSV</a>
          <a className={s.primary} href={workbookUrl()}><FileSpreadsheet size={16}/> 전체 엑셀 (분개장 + 원장)</a>
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
    <section className={s.card}><h2>완성된 원장을 위해 더 필요한 자료</h2><div className={s.completionGrid}>{["매출·매출채권", "자본금·지원금·이자", "미지급·미수·선급 내역", "기초잔액·기말 조정"].map(t => <div key={t}><FileText size={19}/><b>{t}</b><small>자료 연결 및 별도 전표 검토 필요</small></div>)}</div>
      <button className={s.textButton} onClick={onSources}>자료 연결로 이동 <ArrowRight size={14}/></button></section>
  </>;
}
