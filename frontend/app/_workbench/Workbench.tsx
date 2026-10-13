"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, ChevronRight, ClipboardCheck, FolderInput, Landmark, Layers3, Loader2, LogOut, UserRound } from "lucide-react";
import { api, ApiError, isDone, onUnauthorized, type BankTransaction, type Me, type Receipt, type Workspace } from "./api";
import AuthScreen from "./AuthScreen";
import CompanyStep from "./CompanyStep";
import SourcesStep from "./SourcesStep";
import ReviewStep from "./ReviewStep";
import LedgerStep from "./LedgerStep";
import EntriesStep from "./EntriesStep";
import OnboardingScreen from "./OnboardingScreen";
import HomeStep from "./HomeStep";
import s from "./workbench.module.css";
import { retryStartup } from "./startup";

const steps = [
  { title: "회사 기준", sub: "계정 · 거래처 · 기초잔액", icon: Building2, lead: "회사의 계정과 거래처를 기준으로 분개와 원장을 맞춥니다." },
  { title: "자료 연결", sub: "영수증과 거래 내역 연결", icon: FolderInput, lead: "영수증을 올리면 자동으로 읽고, 은행 거래 내역으로 지급을 확인합니다." },
  { title: "거래 분개", sub: "통장 거래 · AI 자동 처리", icon: Landmark, lead: "통장 거래마다 전표를 만들고, AI가 계정을 판단해 자동으로 장부에 반영합니다." },
  { title: "영수증 검토", sub: "추가 정보 입력 · 확정", icon: ClipboardCheck, lead: "사용 목적, 부가세, 지급 정보를 확인하고 분개를 확정합니다." },
  { title: "계정별원장", sub: "확정 전표 모아보기", icon: Layers3, lead: "확정한 전표를 회사 계정별로 확인하고 내보냅니다." },
];

const HOME = -1;

export type Notify = (message: string, kind?: "success" | "error") => void;

/** Shows the login screen until there is a session, then the workbench for that member's company. */
export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [bootError, setBootError] = useState("");
  const [loginError, setLoginError] = useState("");
  const [startupRun, setStartupRun] = useState(0);
  const [starting, setStarting] = useState(false);

  const check = useCallback(() => {
    setBootError("");
    setStarting(false);
    setStartupRun(run => run + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const hint = setTimeout(() => setStarting(true), 3_000);
    retryStartup(signal => api.me(signal), controller.signal).then(user => {
      if (!controller.signal.aborted) setMe(user);
    }).catch((error: Error) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) setMe(null);
      else setBootError(error.message);
    }).finally(() => clearTimeout(hint));
    return () => { controller.abort(); clearTimeout(hint); };
  }, [startupRun]);

  useEffect(() => {
    // Google sign-in failures come back as ?login_error=...
    const params = new URLSearchParams(window.location.search);
    const error = params.get("login_error");
    if (error) {
      setLoginError(error);
      window.history.replaceState(null, "", window.location.pathname);
    }
    onUnauthorized(() => setMe(null));
  }, []);

  if (bootError) {
    return <div className={s.boot}><div className={s.bootCard} role="alert"><strong>서버에 연결하지 못했습니다</strong><p>{bootError}</p><button className={s.primary} onClick={check}>다시 시도</button></div></div>;
  }
  if (me === undefined) return <div className={s.boot}><div className={s.bootCard} role="status"><Loader2 className={s.spin} size={22}/><strong>{starting ? "서버를 시작하고 있습니다" : "연결하고 있습니다"}</strong><p>{starting ? "오랜만에 접속하면 준비에 1~2분 정도 걸릴 수 있습니다. 연결되면 자동으로 다음 화면으로 이동합니다." : "잠시만 기다려 주세요."}</p></div></div>;
  if (me === null) return <AuthScreen initialError={loginError} onSignedIn={signedIn => { setLoginError(""); setMe(signedIn); }}/>;
  const logout = async () => { await api.logout().catch(() => undefined); setMe(null); };
  if (!me.onboarded) return <OnboardingScreen onDone={() => setMe({ ...me, onboarded: true })} onLogout={logout}/>;
  return <Workbench key={me.id} me={me} onLogout={logout}/>;
}

function Workbench({ me, onLogout }: { me: Me; onLogout: () => Promise<void> }) {
  const [step, setStep] = useState(HOME);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [transactions, setTransactions] = useState<BankTransaction[]>([]);
  const [selected, setSelected] = useState("");
  const [ledgerAccount, setLedgerAccount] = useState("");
  const [notice, setNotice] = useState<{ message: string; kind: "success" | "error" } | null>(
    me.imported ? { kind: "success", message: `가입 전에 쓰던 데이터를 이 계정으로 옮겼습니다: 영수증 ${me.imported.receipts}건, 원본 파일 ${me.imported.files}개, 은행 거래 ${me.imported.transactions}건.` } : null);
  const [loadError, setLoadError] = useState("");

  const notify: Notify = useCallback((message, kind = "success") => setNotice(message ? { message, kind } : null), []);

  const load = useCallback(async () => {
    try {
      const [ws, list, txs] = await Promise.all([api.workspace(), api.receipts(), api.transactions()]);
      setWorkspace(ws);
      setReceipts(list);
      setTransactions(txs);
      setLoadError("");
    } catch (error) {
      setLoadError((error as Error).message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Uploads and confirmations add or teach vendors on the server, so re-read the workspace on each step.
  const go = (index: number) => { setStep(index); setNotice(null); api.workspace().then(setWorkspace).catch(() => undefined); };
  const upsertReceipt = useCallback((receipt: Receipt) => {
    setReceipts(list => list.some(x => x.id === receipt.id) ? list.map(x => x.id === receipt.id ? receipt : x) : [...list, receipt]);
  }, []);
  const refreshReceipts = useCallback(async () => setReceipts(await api.receipts()), []);
  const refreshTransactions = useCallback(async () => setTransactions(await api.transactions()), []);
  const confirmed = receipts.filter(isDone).length;

  // Receipts from another year all fail the posting-date check; offer the obvious fix.
  const years = receipts.map(r => r.date.slice(0, 4)).filter(Boolean);
  const commonYear = years.sort((a, b) => years.filter(y => y === b).length - years.filter(y => y === a).length)[0];
  const yearMismatch = workspace && commonYear && commonYear !== workspace.year && years.filter(y => y === commonYear).length > receipts.length / 2 ? commonYear : "";
  const switchYear = async () => {
    if (!workspace) return;
    try {
      setWorkspace(await api.saveWorkspace({ ...workspace, year: yearMismatch }));
      await refreshReceipts();
      notify(`회계연도를 ${yearMismatch}년으로 바꿨습니다.`);
    } catch (error) {
      notify((error as Error).message, "error");
    }
  };

  if (!workspace) {
    return <div className={s.boot}>
      {loadError ? <div className={s.bootCard}><strong>서버에 연결하지 못했습니다</strong><p>{loadError}</p><button className={s.primary} onClick={load}>다시 시도</button></div>
        : <><Loader2 className={s.spin} size={22}/> 불러오는 중</>}
    </div>;
  }

  return <div className={s.app}>
    <aside className={s.sidebar}>
      <button className={`${s.brand} ${s.brandButton}`} onClick={() => go(HOME)} aria-label="메인 페이지로" title="메인 페이지"><span className={s.logo}>J</span> Journalizer</button>
      <button className={`${s.company} ${s.companyButton}`} onClick={() => go(0)} title="회사 기준 · 회계연도 설정"><Building2 size={18}/><div><strong>{workspace.company || "회사명 입력"}</strong><small>{workspace.year} 회계연도 · KRW</small></div></button>
      <div className={s.navLabel}>원장 만들기</div>
      <nav>{steps.map((item, i) => <button key={item.title} onClick={() => go(i)} className={`${s.navItem} ${step === i ? s.active : ""}`} aria-current={step === i ? "step" : undefined}><item.icon size={19}/><span><strong>{item.title}</strong><small>{item.sub}</small></span><span className={s.stepNum}>0{i + 1}</span></button>)}</nav>
      <div className={s.account}><UserRound size={16}/><div><strong title={me.email}>{me.name || me.email}</strong><small>{me.email}</small></div>
        <button className={s.iconButton} aria-label="로그아웃" title="로그아웃" onClick={onLogout}><LogOut size={15}/></button></div>
      <div className={s.sideBottom}>JOURNALIZER v0.3</div>
    </aside>
    <main className={s.main}>
      <header className={s.topbar}><span>워크스페이스 <ChevronRight size={14}/> 원장 만들기 <ChevronRight size={14}/><b>{step === HOME ? "홈" : steps[step].title}</b></span><span className={s.sample}>영수증 {receipts.length} · 거래 {transactions.length}</span></header>
      <div className={s.content}>
        <div className={s.intro}>{step === HOME
          ? <div><div className={s.eyebrow}>HOME</div><h1>{workspace.company || "회사"} 장부</h1><p>AI 연결을 확인하고, 원장 만들기 단계를 순서대로 진행하세요.</p></div>
          : <div><div className={s.eyebrow}>STEP 0{step + 1} / 0{steps.length}</div><h1>{step === 3 ? "영수증에 회계 정보를 더하세요" : steps[step].title}</h1><p>{steps[step].lead}</p></div>}<div className={s.counter}><strong>{confirmed}<span> / {receipts.length}</span></strong><small>영수증 확정</small></div></div>
        {yearMismatch && <div className={s.yearBanner}><span>영수증 대부분이 <b>{yearMismatch}년</b>인데 회계연도가 {workspace.year}년으로 되어 있어 모든 영수증에 ‘회계연도에 맞는 전기일’이 누락으로 표시됩니다.</span><button className={s.primary} onClick={switchYear}>회계연도를 {yearMismatch}년으로 변경</button></div>}
        {notice && <div role={notice.kind === "error" ? "alert" : "status"} className={notice.kind === "error" ? s.error : s.success}>{notice.message}<button aria-label="알림 닫기" onClick={() => setNotice(null)}>×</button></div>}

        {step === HOME && <HomeStep workspace={workspace} steps={steps} receipts={receipts.length} confirmed={confirmed} transactions={transactions.length} notify={notify} onOpen={go} onProcessed={async () => { await refreshReceipts(); }}/>}
        {step === 0 && <CompanyStep workspace={workspace} onSaved={async ws => { setWorkspace(ws); await refreshReceipts(); }} onTransactionsChanged={async () => { await refreshTransactions(); await refreshReceipts(); }} notify={notify} onNext={() => go(1)}/>}
        {step === 1 && <SourcesStep onReview={id => { setSelected(id); go(3); }} workspace={workspace} transactions={transactions} onReceipt={upsertReceipt} onTransactionsChanged={async () => { await refreshTransactions(); await refreshReceipts(); }} notify={notify} onNext={() => go(2)}/>}
        {step === 2 && <EntriesStep workspace={workspace} notify={notify} onReceipts={() => { refreshReceipts(); go(3); }} onLedger={() => go(4)}/>}
        {step === 3 && <ReviewStep workspace={workspace} receipts={receipts} transactions={transactions} selected={selected} onSelect={id => { setSelected(id); setNotice(null); }} onReceipt={upsertReceipt} onReceipts={setReceipts} onWorkspaceChanged={async () => setWorkspace(await api.workspace())} onDeleted={ids => setReceipts(list => list.filter(x => !ids.includes(x.id)))} notify={notify} onUpload={() => go(1)} onLedger={code => { setLedgerAccount(code); go(4); }}/>}
        {step === 4 && <LedgerStep workspace={workspace} confirmed={confirmed} account={ledgerAccount || workspace.accounts.find(a => a.kind === "expense")?.code || workspace.accounts[0]?.code || ""} onAccount={setLedgerAccount} notify={notify} onSources={() => go(1)}/>}
        <footer className={s.footer}><span>JOURNALIZER · RECEIPTS TO LEDGER</span><span>통장·영수증 → AI 판단 → 자동 반영 → 원장</span></footer>
      </div>
    </main>
  </div>;
}
