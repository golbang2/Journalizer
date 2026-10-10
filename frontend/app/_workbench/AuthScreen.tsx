"use client";

import { FormEvent, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api, googleLoginUrl, type Me } from "./api";
import s from "./workbench.module.css";

type Props = { onSignedIn: (me: Me) => void; initialError?: string };

export default function AuthScreen({ onSignedIn, initialError = "" }: Props) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const [google, setGoogle] = useState(false);

  useEffect(() => { api.authConfig().then(c => setGoogle(c.google)).catch(() => setGoogle(false)); }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      onSignedIn(mode === "login" ? await api.login(email, password) : await api.signup(email, password, name));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return <div className={s.authPage}>
    <form className={s.authCard} onSubmit={submit}>
      <div className={s.brand}><span className={s.logo}>J</span> Journalizer</div>
      <h1>{mode === "login" ? "로그인" : "회원가입"}</h1>
      <p>{mode === "login" ? "영수증에서 원장까지, 회사 장부를 이어서 작업하세요." : "가입하면 회사 장부가 하나 만들어집니다. 다른 회원은 내 데이터를 볼 수 없습니다."}</p>
      <div className={s.authTabs} role="tablist">
        <button type="button" role="tab" aria-selected={mode === "login"} className={mode === "login" ? s.tabActive : ""} onClick={() => { setMode("login"); setError(""); }}>로그인</button>
        <button type="button" role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? s.tabActive : ""} onClick={() => { setMode("signup"); setError(""); }}>회원가입</button>
      </div>
      {mode === "signup" && <label>이름<input autoComplete="name" value={name} onChange={e => setName(e.target.value)} placeholder="선택 입력"/></label>}
      <label>이메일<input type="email" required autoComplete="email" value={email} onChange={e => setEmail(e.target.value)}/></label>
      <label>비밀번호<input type="password" required minLength={mode === "signup" ? 8 : undefined} autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={e => setPassword(e.target.value)} placeholder={mode === "signup" ? "8자 이상" : ""}/></label>
      {error && <div role="alert" className={s.authError}>{error}</div>}
      <button className={s.primary} disabled={busy} type="submit">{busy && <Loader2 className={s.spin} size={15}/>}{mode === "login" ? "로그인" : "가입하고 시작하기"}</button>
      {google && <>
        <div className={s.authDivider}><span>또는</span></div>
        <a className={`${s.secondary} ${s.googleButton}`} href={googleLoginUrl()}>
          <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.2C12.5 13.6 17.8 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.8c4.3-4 6.9-9.9 6.9-17.2z"/><path fill="#FBBC05" d="M10.6 28.5c-.5-1.4-.8-2.9-.8-4.5s.3-3.1.8-4.5l-7.9-6.2C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.2z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.8c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.8l-7.9 6.2C6.6 42.6 14.6 48 24 48z"/></svg>
          구글 계정으로 계속하기</a>
      </>}
    </form>
  </div>;
}
