"use client";

import { useState } from "react";
import { api, fileUrl, money, type AiSuggestion, type Evidence } from "./api";
import s from "./workbench.module.css";

type CurrentSource = { description: string; account: string; amount: number; confirmed: boolean };

function EvidenceItem({ evidence: e }: { evidence: Evidence }) {
  const [current, setCurrent] = useState<CurrentSource | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inspect = async () => {
    setBusy(true);
    setError("");
    try {
      if (e.source_type === "receipt") {
        const r = await api.receipt(e.source_id);
        setCurrent({ description: r.merchant, account: r.account, amount: r.total, confirmed: r.confirmed });
      } else {
        const row = (await api.entries()).find(x => x.tx_id === e.source_id);
        if (!row) throw new Error("이 전표가 삭제되었거나 더 이상 조회할 수 없습니다.");
        setCurrent({ description: row.transaction.description, account: row.account,
          amount: row.transaction.withdrawal || row.transaction.deposit, confirmed: row.confirmed && !row.skip && !row.covered_by });
      }
    } catch (err) {
      setCurrent(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <li>
    <strong>{e.description} → {e.account_name || e.account}</strong>
    <span>{e.date || "날짜 미기록"}{e.amount !== null ? ` · ${money(e.amount)}원` : ""} · {e.source_type === "precedent" ? "이전 원장" : e.source_id}</span>
    {e.purpose && <span>{e.purpose}</span>}
    <span>{e.reason}</span>
    <small>{e.path.join(" → ")}</small>
    {e.sources.map((source, i) => <small key={i}>{source.file_name} · {source.sheet} · {source.row_number}행 · {source.date}</small>)}
    {e.source_type === "precedent" && !e.sources.length && <small>기존에 가져온 원장은 행 위치가 없습니다. 다시 가져오면 출처가 기록됩니다.</small>}
    {e.source_type !== "precedent" && <button type="button" className={s.linkButton} disabled={busy} onClick={inspect}>{busy ? "불러오는 중…" : "현재 전표 확인"}</button>}
    {error && <span role="alert">{error}</span>}
    {current && <div className={s.evidenceCurrent}>
      <span>현재: {current.description} · {current.account} · {money(current.amount)}원 · {current.confirmed ? "확정" : "미확정·재검토"}</span>
      {(!current.confirmed || current.account !== e.account || current.amount !== e.amount) && <strong>추천 당시와 상태가 다릅니다. 근거를 다시 확인하세요.</strong>}
      {e.source_type === "receipt" && <a href={fileUrl(e.source_id)} target="_blank" rel="noreferrer">원본 영수증 열기</a>}
    </div>}
  </li>;
}

export default function RecommendationEvidence({ suggestion }: { suggestion: AiSuggestion }) {
  if (!suggestion.retrieval_version) return <p className={s.noteText}>이전 추천에는 사례 출처가 기록되지 않았습니다.</p>;
  const evidence = suggestion.evidence ?? [];
  if (!evidence.length) return <p className={s.noteText}>관련 확정 사례를 찾지 못했습니다. 회사 기준과 거래 내용을 확인하세요.</p>;
  return <details className={s.recommendationEvidence}>
    <summary>추천에 전달한 관련 사례 {evidence.length}건</summary>
    <p>추천 당시의 자료입니다. 현재 전표와 다를 수 있으며, 추천 계정과 다른 사례도 비교용으로 포함됩니다.</p>
    <ul>{evidence.map(e => <EvidenceItem key={`${e.source_type}:${e.source_id}`} evidence={e}/>)}</ul>
  </details>;
}
