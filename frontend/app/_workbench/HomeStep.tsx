"use client";

import { FormEvent, useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, KeyRound, Loader2, Sparkles, Trash2, TriangleAlert } from "lucide-react";
import { api, autoMessage, money, type AiSource, type AiStatus, type Workspace } from "./api";
import type { Notify } from "./Workbench";
import s from "./workbench.module.css";

type Step = { title: string; sub: string };
type Props = {
  workspace: Workspace; steps: Step[]; receipts: number; confirmed: number; transactions: number;
  notify: Notify; onOpen: (step: number) => void; onProcessed: () => Promise<void>;
};

/** Main page: AI connection (Claude, GPT or Gemini) and where each step stands. */
export default function HomeStep({ workspace, steps, receipts, confirmed, transactions, notify, onOpen, onProcessed }: Props) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.aiSettings().then(setStatus).catch(e => notify((e as Error).message, "error")); }, [notify]);

  const processAll = async () => {
    setBusy(true);
    try {
      const result = await api.autoProcess();
      await onProcessed();
      notify(autoMessage(result), result.error && !result.judged && !result.auto?.entries ? "error" : "success");
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const profile = workspace.profile;

  const progress = [
    workspace.company ? `${workspace.company} · ${workspace.year}년` : "회사명 미입력",
    `영수증 ${receipts}건 · 은행 거래 ${transactions}건`,
    transactions ? `통장 거래 ${transactions}건` : "거래내역을 먼저 올리세요",
    `영수증 ${confirmed} / ${receipts}건 확정`,
    "확정 전표로 원장 만들기",
  ];

  return <>
    <AiConnection status={status} setStatus={setStatus} notify={notify}/>

    {profile && !profile.error && <section className={s.card}><div className={s.cardHeading}><div><h2>회사 정보 (서류에서 읽음)</h2><p>AI가 계정을 판단할 때 이 정보를 참고합니다.</p></div></div>
      <div className={s.proposalGrid}>{[["업태·종목", profile.business_type], ["대표자", profile.representative], ["개업일", profile.opened],
        ["자본금", profile.capital ? `${money(profile.capital)}원` : ""], ["주주", profile.shareholders.join(", ")], ["사업자등록번호", profile.business_number]]
        .filter(([, v]) => v).map(([k, v]) => <div key={k} className={s.pick}><span><small>{k}</small><b>{v}</b></span></div>)}</div></section>}

    <section className={s.card}><div className={s.cardHeading}><div><h2>원장 만들기</h2><p>통장 거래내역과 영수증을 올린 뒤 ‘AI로 전부 처리’를 누르면 분개부터 원장까지 자동으로 반영합니다.</p></div>
      <button className={s.primary} disabled={busy || !transactions} onClick={processAll}>{busy ? <Loader2 className={s.spin} size={14}/> : <Sparkles size={14}/>} AI로 전부 처리</button></div>
      <div className={s.homeSteps}>{steps.map((step, i) => <button key={step.title} className={s.homeStep} onClick={() => onOpen(i)}>
        <span className={s.stepNum}>0{i + 1}</span><b>{step.title}</b><small>{step.sub}</small><em>{progress[i]}</em><ArrowRight size={14}/>
      </button>)}</div>
    </section>
  </>;
}

const SOURCE_TEXT: Record<AiSource, string> = { company: "키 등록됨", server: "서버 키 사용", none: "미등록" };

/** Pick the AI service, register its key (checked against the provider), and pick a model from the provider's own list. */
function AiConnection({ status, setStatus, notify }: { status: AiStatus | null; setStatus: (s: AiStatus) => void; notify: Notify }) {
  const [chosen, setChosen] = useState("");
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState("");
  const provider = chosen || status?.provider || "anthropic";
  const info = status?.providers.find(p => p.id === provider);
  const active = status?.provider === provider;
  const source = info?.source ?? "none";

  useEffect(() => {
    if (!status) return;
    setModel(status.provider === provider ? status.model : "");
    setModels([]);
    if (source === "none") return;
    let live = true;
    api.aiModels(provider).then(r => live && setModels(r.models)).catch(() => live && setModels([]));
    return () => { live = false; };
  }, [provider, status, source]);

  const run = async (label: string, task: () => Promise<void>) => {
    setBusy(label);
    try {
      await task();
    } catch (e) {
      notify((e as Error).message, "error");
    } finally {
      setBusy("");
    }
  };
  // With a new key typed in, its model list can be previewed before anything is saved.
  const preview = () => run("preview", async () => {
    const found = await api.previewAiModels(provider, key.trim());
    setModels(found.models);
    setModel(m => found.models.includes(m) ? m : found.default);
    notify(`키를 확인했습니다. 쓸 수 있는 모델 ${found.models.length}개 중에서 고른 뒤 ‘등록’을 누르세요.`);
  });
  const save = (event?: FormEvent) => {
    event?.preventDefault();
    run("save", async () => {
      const typed = key.trim();
      const next = await api.saveAi(provider, typed, model);
      setStatus(next);
      setKey("");
      notify(typed ? `${info?.label} 키를 등록했습니다. 이제 ${next.label} 모델로 판단합니다.` : `AI 판단에 ${next.label} 모델을 사용합니다.`);
    });
  };
  const remove = () => {
    if (!window.confirm(`${info?.label} API 키를 삭제할까요?`)) return;
    run("delete", async () => {
      setStatus(await api.deleteAiKey(provider));
      notify("API 키를 삭제했습니다.");
    });
  };

  return <section className={`${s.card} ${s.aiConnect}`}>
    <div className={s.cardHeading}><div><h2><KeyRound size={16}/> AI 연결</h2>
      <p>계정·부가세 판단과 회사 서류·세금계산서·부가세 부속서류 읽기에 쓸 AI 서비스를 고르세요. 키는 등록할 때 해당 서비스에 확인하고, 저장한 뒤에는 일부만 표시합니다.</p></div>
      {status && (status.configured
        ? <span className={s.doneTag}><CheckCircle2 size={11}/> 사용 중 · {status.label}</span>
        : <span className={s.pendingTag}><TriangleAlert size={11}/> 미연결</span>)}</div>
    {!status ? <p className={s.helper}><Loader2 className={s.spin} size={12}/> 확인 중</p> : <>
      <div className={s.providerTabs} role="tablist">{status.providers.map(p => <button key={p.id} role="tab" aria-selected={p.id === provider}
        className={`${s.providerTab} ${p.id === provider ? s.providerActive : ""}`} onClick={() => { setChosen(p.id); setKey(""); }}>
        <b>{p.label}</b><small>{status.provider === p.id ? "사용 중 · " : ""}{SOURCE_TEXT[p.source]}</small></button>)}</div>

      {info && <div className={s.providerBody}>
        {source === "company" && <div className={s.keyRow}><span>등록된 키</span><code>{info.masked}</code>
          <button className={s.iconButton} aria-label="API 키 삭제" title="API 키 삭제" onClick={remove}><Trash2 size={14}/></button></div>}
        {source === "server" && <p className={s.helper}>서버 설정(backend/.env)의 키를 쓰고 있습니다. 회사 키를 등록하면 그 키를 먼저 씁니다.</p>}
        <form className={s.keyForm} onSubmit={save}>
          <label>{source === "company" ? "새 API 키로 바꾸기" : `${info.label} API 키`} <small className={s.optional}>{info.console}에서 발급</small>
            <input type="password" autoComplete="off" spellCheck={false} placeholder={info.key_hint} value={key} onChange={e => setKey(e.target.value)}/></label>
          <button type="button" className={s.secondary} disabled={!!busy || key.trim().length < 20} onClick={preview}>
            {busy === "preview" ? <Loader2 className={s.spin} size={14}/> : <KeyRound size={14}/>} 키 확인 · 모델 불러오기</button>
        </form>
        <div className={s.keyForm}>
          <label>모델 <small className={s.optional}>{models.length ? `이 키로 쓸 수 있는 모델 ${models.length}개` : source === "none" && !key ? "키를 확인하면 목록이 나옵니다" : "목록을 불러오는 중…"}</small>
            <select value={model} disabled={!models.length} onChange={e => setModel(e.target.value)}>
              <option value="">{models.length ? "모델 선택" : "—"}</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
              {model && !models.includes(model) && <option value={model}>{model}</option>}
            </select></label>
          <button className={s.primary} disabled={!!busy || !model || (!key.trim() && source === "none") || (active && !key.trim() && model === status.model)} onClick={() => save()}>
            {busy === "save" ? <Loader2 className={s.spin} size={14}/> : <CheckCircle2 size={14}/>} {key.trim() ? "키와 모델 등록" : active ? "이 모델로 변경" : "이 서비스로 전환"}</button>
        </div>
      </div>}
    </>}
  </section>;
}
