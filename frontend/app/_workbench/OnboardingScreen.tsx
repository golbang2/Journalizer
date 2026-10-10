"use client";

import { useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, FileText, Loader2, LogOut } from "lucide-react";
import { api, documentUrl, type CompanyDocument } from "./api";
import s from "./workbench.module.css";

type Props = { onDone: () => void; onLogout: () => Promise<void> };

/** First step for a new company: all three registration documents are required before the workbench opens. */
export default function OnboardingScreen({ onDone, onLogout }: Props) {
  const [documents, setDocuments] = useState<CompanyDocument[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { api.documents().then(setDocuments).catch(e => setError((e as Error).message)); }, []);

  const upload = async (kind: string, file: File) => {
    setBusy(kind);
    setError("");
    try {
      setDocuments(await api.uploadDocument(kind, file));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const done = documents?.filter(d => d.file_name).length ?? 0;
  const complete = !!documents && done === documents.length;

  return <div className={s.authPage}>
    <div className={`${s.authCard} ${s.onboardCard}`}>
      <div className={s.brand}><span className={s.logo}>J</span> Journalizer</div>
      <h1>회사 서류 등록</h1>
      <p>장부를 시작하기 전에 회사 기본 서류 세 가지를 올려 주세요. 세 가지를 모두 올려야 다음 단계로 넘어갈 수 있습니다. PDF 또는 이미지(15MB 이하).</p>
      {!documents ? <p><Loader2 className={s.spin} size={13}/> 불러오는 중</p> : <div className={s.docList}>
        {documents.map(d => <div key={d.kind} className={`${s.docItem} ${d.file_name ? s.docDone : ""}`}>
          {d.file_name ? <CheckCircle2 size={18} color="#478267"/> : <FileText size={18} color="#8a93a1"/>}
          <div><b>{d.label} <span className={s.required}>필수</span></b>
            {d.file_name ? <a className={s.tableSub} href={documentUrl(d.kind)} target="_blank" rel="noreferrer">{d.file_name}</a> : <small className={s.tableSub}>아직 올리지 않았습니다</small>}</div>
          <label className={s.secondary}>{busy === d.kind ? <Loader2 className={s.spin} size={13}/> : d.file_name ? "다시 올리기" : "파일 선택"}
            <input className={s.hiddenInput} aria-label={`${d.label} 파일 선택`} type="file" accept="image/*,.pdf" disabled={!!busy}
              onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) upload(d.kind, file); }}/></label>
        </div>)}
      </div>}
      {error && <div role="alert" className={s.authError}>{error}</div>}
      <button className={s.primary} disabled={!complete} onClick={onDone}>{complete ? "다음" : `다음 (${done} / ${documents?.length ?? 3} 등록)`} <ArrowRight size={15}/></button>
      <button className={s.textButton} onClick={onLogout}><LogOut size={13}/> 로그아웃</button>
    </div>
  </div>;
}
