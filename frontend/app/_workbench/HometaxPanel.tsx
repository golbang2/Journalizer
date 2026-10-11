"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiBase, money, type HometaxKind, type HometaxReport } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

const labels = { card: "카드 매입내역", invoice: "매입 전자세금계산서" };
const original = (id: string) => `${apiBase}/hometax/imports/${encodeURIComponent(id)}/file`;

export default function HometaxPanel({ year, notify, onReview }: { year: string; notify: Notify; onReview: (id: string) => void }) {
  const [start, setStart] = useState(`${year}-01-01`);
  const [end, setEnd] = useState(`${year}-12-31`);
  const [report, setReport] = useState<HometaxReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [page, setPage] = useState(0);
  const validPeriod = !!start && !!end && start <= end;
  const refresh = useCallback(async () => {
    if (!start || !end || start > end) return;
    setReport(await api.hometaxReport(start, end));
    setError("");
  }, [start, end]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setReport(null);
    setError("");
    setPage(0);
    if (validPeriod) {
      api.hometaxReport(start, end).then(data => { if (active) setReport(data); })
        .catch(e => { if (active) setError((e as Error).message); })
        .finally(() => { if (active) setLoading(false); });
    } else setLoading(false);
    return () => { active = false; };
  }, [start, end, validPeriod]);

  const upload = async (files: File[], kind: HometaxKind) => {
    setBusy(true);
    let added = 0;
    const failures: string[] = [];
    for (const file of files) {
      try {
        const result = await api.importHometax(file, kind);
        if (result.added) added += result.rows;
      } catch (e) { failures.push(`${file.name}: ${(e as Error).message}`); }
    }
    try { await refresh(); } catch (e) { failures.push((e as Error).message); }
    setBusy(false);
    setError(failures.join("\n"));
    notify(`홈택스 자료 ${added}행을 추가했습니다. 같은 파일은 한 번만 저장됩니다.${failures.length ? " 가져오지 못한 파일은 아래 오류를 확인하세요." : ""}`, failures.length ? "error" : "success");
  };
  const remove = async (id: string) => {
    setBusy(true);
    try { await api.deleteHometax(id); await refresh(); notify("홈택스 자료를 삭제했습니다."); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const link = async (id: string, receiptId: string) => {
    setBusy(true);
    try { await api.linkHometax(id, receiptId); await refresh(); notify(receiptId ? "증빙 연결을 저장했습니다. 세액·공제 판단은 영수증 검토에서 확인하세요." : "수동 연결을 해제하고 자동 대조로 돌아갔습니다."); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const filtered = (report?.rows || []).filter(r => (!onlyIssues || r.issues.length > 0) &&
    `${r.merchant} ${r.business_number} ${r.approval_number} ${r.issues.join(" ")}`.toLowerCase().includes(search.toLowerCase()));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 30) - 1));
  const visible = filtered.slice(currentPage * 30, (currentPage + 1) * 30);

  return <section className={`${s.card} ${s.hometaxPanel}`}>
    <div className={s.cardHeading}><div><h2>홈택스 거래별 대조</h2>
      <p>카드 매입내역과 매입 전자세금계산서를 올려 영수증의 누락·중복·세액 차이를 확인하세요.</p>
      <p>홈택스 자료는 대조 증빙으로 보관합니다. 전표를 추가하거나 기존 세액·공제 판단을 자동으로 바꾸지 않습니다.</p>
    </div></div>
    <div className={s.sourceGrid}>{(["card", "invoice"] as const).map(kind => <div key={kind}>
      <label className={s.upload}>{labels[kind]} 올리기<input aria-label={`홈택스 ${labels[kind]} 파일 선택`} type="file" multiple accept=".xlsx,.xls,.csv" disabled={busy || loading || !validPeriod}
        onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length) upload(files, kind); }}/></label>
    </div>)}</div>
    <details><summary>내려받을 자료와 지원 형식</summary>
      <p>홈택스에서 조회기간을 맞춰 거래별 상세 내역을 엑셀로 내려받으세요. 카드 자료에는 가능하면 공제/불공제 구분도 포함하세요. 합계표는 계정별원장의 부가세 부속서류에 올립니다.</p>
      <p>필수 열: 카드 거래일자 또는 세금계산서 작성일자, 공급자 사업자등록번호, 상호, 공급가액, 세액, 합계금액. 승인번호가 있으면 더 정확하게 대조합니다.</p>
      <p>숫자 식별자는 앞자리 0이 보존되도록 텍스트로 저장하세요. 날짜는 YYYY-MM-DD 형식을 지원합니다. 지원하지 않는 서식이나 잘못된 행이 있으면 해당 파일 전체를 저장하지 않고 위치를 알려드립니다.</p>
      <p>매입 세금계산서는 공급자·공급받는자 열이 구분되어야 합니다. 회사 기준에 사업자번호가 있으면 공급받는자 번호와 대조합니다.</p>
    </details>
    <div className={s.bulkActions}>
      <label>시작일 <input aria-label="홈택스 대조 시작일" type="date" value={start} disabled={busy || loading} onChange={e => setStart(e.target.value)}/></label>
      <label>종료일 <input aria-label="홈택스 대조 종료일" type="date" value={end} disabled={busy || loading} onChange={e => setEnd(e.target.value)}/></label>
      <button disabled={busy || loading || !validPeriod} onClick={async () => { setLoading(true); try { await refresh(); } catch (e) { setError((e as Error).message); } finally { setLoading(false); } }}>다시 대조</button>
    </div>
    {!validPeriod && <p role="alert">올바른 시작일과 종료일을 선택하세요.</p>}
    {error && <p role="alert" className={s.failText} style={{ whiteSpace: "pre-wrap" }}>{error}</p>}
    {(busy || loading) && <p role="status">{busy ? "자료 처리 중…" : "대조 결과 불러오는 중…"}</p>}
    {report && <>
      <details><summary>보관한 원본 {report.imports.length}개 · 전체 기간</summary>
        {report.imports.map(item => <div className={s.fileName} key={item.id}>
          <a href={original(item.id)}>{item.file_name}</a><small>{labels[item.kind]} · {item.first} ~ {item.last} · {item.rows}행</small>
          <button disabled={busy} onClick={() => remove(item.id)}>자료 삭제</button>
        </div>)}
      </details>
      <p>선택 기간 {report.rows.length}건 · 일치 {report.rows.filter(r => !r.issues.length).length}건 · 검토 필요 {report.rows.filter(r => r.issues.length).length}건</p>
      <div className={s.bulkActions}>
        <input aria-label="홈택스 대조 검색" placeholder="상호·사업자번호·승인번호·검토 항목" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }}/>
        <label><input type="checkbox" checked={onlyIssues} onChange={e => { setOnlyIssues(e.target.checked); setPage(0); }}/> 검토 필요한 거래만</label>
      </div>
      {!filtered.length ? <p className={s.helper}>선택한 기간과 검색 조건에 해당하는 거래가 없습니다.</p> : <div className={s.tableWrap}><table>
        <thead><tr><th>홈택스 거래</th><th>금액·공제 표시</th><th>대조 결과</th><th>영수증 연결·검토</th></tr></thead>
        <tbody>{visible.map(row => <tr key={row.id}>
          <td>{row.date} · {labels[row.kind]}<br/><b>{row.merchant}</b><small className={s.tableSub}>{row.business_number}<br/>승인번호 {row.approval_number || "없음"}</small>
            <details><summary>원본 {row.sources.length}행</summary>{row.sources.map((source, i) => <p key={i}>
              <a href={original(source.import_id)}>{source.file_name}</a> · {source.sheet} {source.row_number}행<br/>합계 {money(source.total)} · 세액 {money(source.tax)} · {source.deduction_text || "공제 표시 없음"}
            </p>)}</details>
          </td>
          <td>공급가액 {money(row.supply)}<br/>세액 <b>{money(row.tax)}</b>{row.service !== 0 && <><br/>봉사료 {money(row.service)}</>}<br/>합계 {money(row.total)}<small className={s.tableSub}>{row.deduction_text || "공제 여부 미제공"}{row.status ? ` · ${row.status}` : ""}</small></td>
          <td><b>{row.status_label}</b>{row.issues.map(issue => <small className={s.tableSub} key={issue}>{issue}</small>)}</td>
          <td>{row.receipt_id && <p>{row.manual ? "수동 연결" : "승인번호 등으로 일치"}: <button className={s.linkButton} disabled={busy} onClick={() => onReview(row.receipt_id)}>{row.receipt_id} 검토</button></p>}
            {row.candidates.map(candidate => <div key={candidate.id}>
              <button className={s.linkButton} disabled={busy} onClick={() => onReview(candidate.id)}>{candidate.id} · {candidate.merchant}</button>
              <small className={s.tableSub}>{candidate.date} · {money(candidate.total)}원<br/>증빙 세액 {money(candidate.evidence_tax)} · {candidate.confirmed ? "확정" : "미확정"} 공제세액 {money(candidate.book_tax)}<br/>{candidate.same_kind ? "같은 증빙 종류" : "다른 증빙 종류 · 중복 확인"}</small>
              {row.receipt_id !== candidate.id && <button disabled={busy} onClick={() => link(row.id, candidate.id)}>이 영수증에 연결</button>}
            </div>)}
            {!row.candidates.length && <span>영수증을 추가하거나 사업자번호·날짜·금액을 확인하세요.</span>}
            {row.manual && <button disabled={busy} onClick={() => link(row.id, "")}>수동 연결 해제</button>}
          </td>
        </tr>)}</tbody>
      </table></div>}
      {filtered.length > 30 && <div className={s.bulkActions}><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>이전</button>
        <span>{currentPage + 1} / {Math.ceil(filtered.length / 30)}</span><button disabled={(currentPage + 1) * 30 >= filtered.length} onClick={() => setPage(currentPage + 1)}>다음</button></div>}
      {report.unmatched_receipts.length > 0 && <details><summary>대응 홈택스 자료가 없는 영수증 {report.unmatched_receipts.length}건</summary>
        <p>자료를 내려받은 기간과 카드 등록 범위를 먼저 확인하세요. 홈택스 자료에 없다는 이유만으로 불공제 처리하지 않습니다.</p>
        {report.unmatched_receipts.map(r => <p key={r.id}><button className={s.linkButton} disabled={busy} onClick={() => onReview(r.id)}>{r.id} · {r.date} · {r.merchant} · {money(r.total)}원</button></p>)}
      </details>}
      <p className={s.helper}>‘일치’는 자료 간 대조 결과입니다. 사업 관련성과 공제 요건은 영수증 검토에서 확정하세요. 취소·수정 거래는 원거래와 귀속기간을 함께 검토해야 합니다.</p>
    </>}
  </section>;
}
