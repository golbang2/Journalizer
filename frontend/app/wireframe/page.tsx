"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Building2, Check, CheckCircle2, ChevronRight, ClipboardCheck, Download, FileText, FolderInput, Landmark, Layers3, Plus, Search, Settings2 } from "lucide-react";
import { accounts as initialAccounts, receipts as initialReceipts, journal, missing, money, type Receipt } from "./model";
import s from "./wireframe.module.css";

const steps = [
  { title: "회사 기준", sub: "계정 · 거래처 · 기초잔액", icon: Building2 },
  { title: "자료 연결", sub: "영수증과 거래 내역 연결", icon: FolderInput },
  { title: "분개 검토", sub: "추가 정보 입력 · 확정", icon: ClipboardCheck },
  { title: "계정별원장", sub: "확정 전표 모아보기", icon: Layers3 },
];
const sources = ["영수증", "은행 거래 내역", "카드 이용·청구 내역", "세금계산서·정산서", "기초잔액·기존 채무", "매출·자본금·결산 자료"];
const expenseCodes = ["81100", "81200", "81300", "81700", "81900", "83000", "83100", "85300"];

export default function Wireframe() {
  const [step, setStep] = useState(2);
  const [company, setCompany] = useState("주식회사 더블락");
  const [year, setYear] = useState("2023");
  const [accounts, setAccounts] = useState(initialAccounts);
  const [vendors, setVendors] = useState([{ code: "00109", name: "홍익경영파트너스", alias: "기업김상철", business: "" }, { code: "S001", name: "컴포즈커피", alias: "컴포즈", business: "" }]);
  const [receipts, setReceipts] = useState(initialReceipts);
  const [selected, setSelected] = useState("R-031");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [uploads, setUploads] = useState<Record<string, string[]>>({});
  const [ledgerAccount, setLedgerAccount] = useState("81100");
  const [notice, setNotice] = useState("");
  const r = receipts.find(x => x.id === selected)!;
  const update = (patch: Partial<Receipt>) => { setReceipts(list => list.map(x => x.id === selected ? { ...x, ...patch, confirmed: false } : x)); setNotice(""); };
  const gaps = missing(r, year);
  const draft = journal(r);
  const confirmed = receipts.filter(x => x.confirmed && missing(x, year).length === 0);
  const ledger = confirmed.flatMap(journal).filter(l => l.account === ledgerAccount).sort((a, b) => a.date.localeCompare(b.date) || a.voucher.localeCompare(b.voucher));
  const account = accounts.find(a => a.code === ledgerAccount)!;
  let balance = account.opening;
  const ledgerRows = ledger.map(l => { balance += account.normal === "debit" ? l.debit - l.credit : l.credit - l.debit; return { ...l, balance }; });
  const debit = draft.reduce((sum, l) => sum + l.debit, 0);
  const credit = draft.reduce((sum, l) => sum + l.credit, 0);
  const name = (code: string) => accounts.find(a => a.code === code)?.name || code;
  const download = () => {
    const rows = [["샘플 원장", company, year, account.code, account.name], ["날짜", "적요", "거래처 코드", "거래처", "차변", "대변", "잔액"], ["기초", "입력한 기초잔액", "", "", "", "", account.opening], ...ledgerRows.map(l => [l.date, l.memo, l.vendorCode, l.vendor, l.debit, l.credit, l.balance])];
    const csv = "\uFEFF" + rows.map(row => row.map(v => '"' + String(v).replace(/^[=+@-]/, "'$&").replaceAll('"', '""') + '"').join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `샘플원장_${account.code}.csv`; a.click(); URL.revokeObjectURL(url);
  };
  return <div className={s.app}>
    <aside className={s.sidebar}>
      <Link href="/" className={s.brand}><span className={s.logo}>J</span> Journalizer<span className={s.beta}>LAB</span></Link>
      <div className={s.company}><Building2 size={18}/><div><strong>{company || "회사명 입력"}</strong><small>{year} 회계연도 · KRW</small></div></div>
      <div className={s.navLabel}>원장 만들기</div>
      <nav>{steps.map((item, i) => <button key={item.title} onClick={() => { setStep(i); setNotice(""); }} className={`${s.navItem} ${step === i ? s.active : ""}`} aria-current={step === i ? "step" : undefined}><item.icon size={19}/><span><strong>{item.title}</strong><small>{item.sub}</small></span><span className={s.stepNum}>0{i + 1}</span></button>)}</nav>
      <div className={s.sideNote}><Settings2 size={18}/><strong>영수증에서 원장까지</strong><p>회사 기준과 거래 맥락을 더해<br/>검토한 전표를 원장에 반영합니다.</p><Link href="/">실제 OCR 업로드 화면 <ArrowRight size={14}/></Link></div>
      <div className={s.sideBottom}>인터랙티브 와이어프레임 · v1</div>
    </aside>
    <main className={s.main}>
      <header className={s.topbar}><span>워크스페이스 <ChevronRight size={14}/> 원장 만들기 <ChevronRight size={14}/><b>{steps[step].title}</b></span><span className={s.sample}>샘플 데이터</span></header>
      <div className={s.content}>
        <div className={s.intro}><div><div className={s.eyebrow}>STEP 0{step + 1} / 04</div><h1>{step === 2 ? "영수증에 회계 정보를 더하세요" : steps[step].title}</h1><p>{["회사의 계정과 거래처를 기준으로 분개와 원장을 맞춥니다.", "영수증만으로 알 수 없는 지급 내역과 증빙을 함께 준비합니다.", "사용 목적, 부가세, 지급 정보를 확인하고 분개를 확정합니다.", "확정한 샘플 전표를 회사 계정별로 확인합니다."][step]}</p></div><div className={s.counter}><strong>{confirmed.length}<span> / {receipts.length}</span></strong><small>검토 확정</small></div></div>
        <div className={s.demoNotice}>화면 설계용 예시입니다. 입력은 새로고침하면 초기화되며, 실제 OCR·파일 분석·서버 저장에는 연결되지 않습니다.</div>
        {notice && <div role="status" className={s.success}>{notice}</div>}

        {step === 0 && <>
          <section className={s.card}><div className={s.cardHeading}><h2>회사 · 회계 기간</h2><span>기본 정보</span></div><div className={s.fields}><label>회사명<input value={company} onChange={e => setCompany(e.target.value)}/></label><label>회계연도<select value={year} onChange={e => setYear(e.target.value)}><option>2023</option><option>2024</option><option>2025</option><option>2026</option></select></label><label>기장 통화<input value="KRW · 대한민국 원" readOnly/></label></div><p className={s.helper}>기간: {year}.01.01 ~ {year}.12.31 · 영수증 거래일과 전기일은 별도로 관리합니다.</p></section>
          <section className={s.card}><div className={s.cardHeading}><div><h2>회사 계정과 기초잔액</h2><p>2023년 원장의 계정 코드를 참고했습니다. 아래 기초잔액 0원은 예시이며 원장 전체 잔액이 아닙니다.</p></div><span>계정 코드 기준</span></div><div className={s.tableWrap}><table><thead><tr><th>코드</th><th>계정명</th><th>잔액 기준</th><th>기초잔액 (원)</th></tr></thead><tbody>{accounts.map((a, i) => <tr key={a.code}><td className={s.mono}>{a.code}</td><td><input aria-label={`${a.code} 계정명`} value={a.name} onChange={e => setAccounts(list => list.map((x, j) => i === j ? { ...x, name: e.target.value } : x))}/></td><td><select aria-label={`${a.code} 잔액 기준`} value={a.normal} onChange={e => setAccounts(list => list.map((x, j) => i === j ? { ...x, normal: e.target.value as "debit" | "credit" } : x))}><option value="debit">차변</option><option value="credit">대변</option></select></td><td><input aria-label={`${a.code} 기초잔액`} type="number" value={a.opening} onChange={e => setAccounts(list => list.map((x, j) => i === j ? { ...x, opening: Number(e.target.value) } : x))}/></td></tr>)}</tbody></table></div></section>
          <section className={s.card}><div className={s.cardHeading}><div><h2>거래처 기준 정보</h2><p>상호와 입출금 표시명이 달라도 같은 거래처로 연결할 수 있습니다. S로 시작하는 코드는 샘플입니다.</p></div><button className={s.secondary} onClick={() => setVendors(v => [...v, { code: "", name: "", alias: "", business: "" }])}><Plus size={14}/> 거래처 추가</button></div><div className={s.tableWrap}><table><thead><tr><th>거래처 코드</th><th>정식 상호</th><th>상호·수취인 별칭</th><th>사업자등록번호</th></tr></thead><tbody>{vendors.map((v, i) => <tr key={i}>{(["code", "name", "alias", "business"] as const).map(key => <td key={key}><input aria-label={`거래처 ${i + 1} ${key}`} value={v[key]} placeholder="입력" onChange={e => setVendors(list => list.map((x, j) => i === j ? { ...x, [key]: e.target.value } : x))}/></td>)}</tr>)}</tbody></table></div></section>
          <div className={s.next}><button className={s.primary} onClick={() => setStep(1)}>자료 연결로 이동 <ArrowRight size={16}/></button></div>
        </>}

        {step === 1 && <>
          <div className={s.sourceGrid}>{sources.map((source, i) => <section className={s.card} key={source}><div className={s.sourceIcon}>{i === 1 ? <Landmark size={22}/> : <FileText size={22}/>}</div><h2>{source}</h2><p>{["PDF·이미지 · 한 파일에 영수증 하나", "출금일, 금액, 수취인, 계좌 정보", "승인번호, 취소 여부, 청구·결제일", "공급가액, 세액, 플랫폼 수수료", "전기말 잔액과 미결제 전표", "영수증 외 거래와 기말 조정"][i]}</p><label className={s.upload}>파일 선택<input aria-label={`${source} 파일 선택`} type="file" multiple accept={i === 0 ? "image/*,.pdf" : ".xlsx,.xls,.csv,.pdf"} onChange={e => setUploads(v => ({ ...v, [source]: Array.from(e.target.files || []).map(f => f.name) }))}/></label>{uploads[source]?.map((file, n) => <div className={s.fileName} key={`${file}-${n}`}>{file} <small>선택됨 · 연결 전</small></div>)}</section>)}</div>
          <section className={s.card}><div className={s.cardHeading}><h2>연결에 필요한 정보</h2><span>필드 설계</span></div><div className={s.connection}><span>영수증<br/><b>일자 · 상호 · 금액 · 승인번호</b></span><ArrowRight size={20}/><span>거래 내역<br/><b>출금일 · 수취인 · 금액 · 거래 ID</b></span><ArrowRight size={20}/><span>확인 후 연결<br/><b>중복 · 취소 · 분할 결제 확인</b></span></div><p className={s.helper}>파일 선택은 화면 동작 예시입니다. 실제 가져오기·열 매핑·자동 대사는 후속 구현 대상이며, 아래 분개 검토에서는 연결할 거래를 직접 입력합니다.</p></section>
          <div className={s.next}><button className={s.primary} onClick={() => setStep(2)}>샘플 영수증 검토 <ArrowRight size={16}/></button></div>
        </>}

        {step === 2 && <div className={s.reviewLayout}>
          <section className={s.queue}><div className={s.queueTitle}><h2>검토할 영수증</h2><span>{receipts.length}</span></div><label className={s.search}><Search size={16}/><input aria-label="영수증 검색" placeholder="거래처 검색" value={query} onChange={e => setQuery(e.target.value)}/></label><div className={s.tabs}>{[["all", "전체"], ["pending", "검토 필요"], ["confirmed", "확정"]].map(([v, label]) => <button key={v} className={filter === v ? s.tabActive : ""} onClick={() => setFilter(v)}>{label}</button>)}</div><div className={s.queueItems}>{receipts.filter(x => x.merchant.includes(query) && (filter === "all" || (filter === "confirmed" ? x.confirmed && !missing(x, year).length : !x.confirmed || !!missing(x, year).length))).map(x => <button key={x.id} className={`${s.receiptItem} ${selected === x.id ? s.selected : ""}`} onClick={() => { setSelected(x.id); setNotice(""); }}><div><strong>{x.merchant}</strong><span className={x.confirmed && !missing(x, year).length ? s.doneTag : s.pendingTag}>{x.confirmed && !missing(x, year).length ? "확정" : "검토 필요"}</span></div><small>{x.date} · {x.id}</small><b>{money(x.total)}<small> 원</small></b><p>{x.id === "R-031" ? "비용 + 기존 채무 정산" : x.id === "R-044" ? "발생일과 지급일 분리" : x.id === "R-002" ? "원장 대응 거래 미확인" : "복리후생비 · 총액 처리"}</p></button>)}</div><div className={s.queueFoot}>대조 보고서의 사례 4건을<br/>입력 흐름 확인용으로 구성했습니다.</div></section>
          <div className={s.reviewMain}>
            <section className={s.card}><div className={s.cardHeading}><div><div className={s.eyebrow}>RECEIPT / {r.id}</div><h2>{r.merchant}</h2></div><span className={s.sample}>원본 추출값 예시</span></div><div className={s.receiptFacts}><div><small>영수증 거래일</small><strong>{r.date}</strong></div><div><small>결제 금액</small><strong>{money(r.total)} 원</strong></div><div><small>추출 부가세</small><strong>{money(r.originalTax)} 원</strong></div></div><div className={s.sourceFile}><FileText size={15}/>{r.file}<span>원본 이미지 미리보기 연결 예정</span></div></section>
            <section className={s.card}><div className={s.cardHeading}><div><h2>회계 정보 입력</h2><p>필수 항목은 * 표시 · 금액 단위: 원</p></div>{r.confirmed && <button className={s.secondary} onClick={() => update({ confirmed: false })}>검토로 되돌리기</button>}</div>
              <fieldset disabled={r.confirmed} className={s.formBody}>
                <h3><span>01</span> 사용 목적과 귀속</h3><div className={s.fields}><label className={s.wide}>사용 목적 · 적요 *<input placeholder="예: 앱 개발 외주 용역" value={r.purpose} onChange={e => update({ purpose: e.target.value })}/></label><label>프로젝트 · 부서<input placeholder="예: 서비스 개발 / 공통 운영" value={r.project} onChange={e => update({ project: e.target.value })}/></label><label>사용자 · 담당자<input placeholder="담당자 이름" value={r.owner} onChange={e => update({ owner: e.target.value })}/></label><label>전기일 *<input type="date" value={r.postingDate} onChange={e => update({ postingDate: e.target.value })}/></label><label>거래처 코드 *<input list="vendorCodes" placeholder="선택 또는 직접 입력" value={r.vendorCode} onChange={e => update({ vendorCode: e.target.value })}/><datalist id="vendorCodes">{vendors.filter(v => v.name === r.merchant || v.alias === r.merchant).map((v, i) => <option key={i} value={v.code}>{v.name}</option>)}</datalist></label></div>
                <h3><span>02</span> 계정과 부가세</h3><div className={s.fields}><label>비용 계정 *<select value={r.account} onChange={e => update({ account: e.target.value })}><option value="">계정 선택</option>{accounts.filter(a => expenseCodes.includes(a.code)).map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label><label>부가세 처리 *<select value={r.vat} onChange={e => update({ vat: e.target.value as Receipt["vat"] })}><option value="pending">검토 필요</option><option value="deductible">부가세대급금으로 분리</option><option value="included">부가세를 비용에 포함</option></select></label>{r.vat === "deductible" && <label>확인한 공제 부가세 *<input type="number" min="0" max={r.total} value={r.tax} onChange={e => update({ tax: Number(e.target.value) })}/></label>}<label className={s.wide}>증빙·부가세 검토 근거 *<input placeholder="예: 세금계산서 번호, 불공제 사유, 확인한 정산서" value={r.evidence} onChange={e => update({ evidence: e.target.value })}/></label></div>
                <h3><span>03</span> 지급 정보와 기존 채무</h3><div className={s.fields}><label>지급 상태 *<select value={r.payment} onChange={e => update({ payment: e.target.value as Receipt["payment"] })}><option value="pending">확인 필요</option><option value="paid">지급 완료 · 은행 출금</option><option value="unpaid">미지급 · 외상매입금</option></select></label>{r.payment === "paid" && <><label>지급일 *<input type="date" value={r.paymentDate} onChange={e => update({ paymentDate: e.target.value })}/></label><label>출금 계좌 *<select value={r.bank} onChange={e => update({ bank: e.target.value })}><option value="99000">우리은행 · 10300 보통예금</option><option value="99001">광주은행 · 10300 보통예금</option></select></label><label>출금 거래 연결 *<input placeholder="거래 ID 또는 출금일·금액·수취인" value={r.paymentLink} onChange={e => update({ paymentLink: e.target.value })}/></label></>}<label>기존 외상매입금 정산액<input type="number" min="0" max={r.total} value={r.payableOffset} onChange={e => update({ payableOffset: Number(e.target.value) })}/></label>{r.payableOffset > 0 && <label>기존 채무 전표 연결 *<input placeholder="기존 전표 번호 또는 원장 행" value={r.payableLink} onChange={e => update({ payableLink: e.target.value })}/></label>}</div>
                {r.payableOffset > 0 && <div className={s.inlineNote}>결제 총액 중 {money(r.payableOffset)}원은 기존 채무 정산으로 분리합니다. 신규 비용은 {money(r.total - r.payableOffset - (r.vat === "deductible" ? r.tax : 0))}원입니다.</div>}
                {r.payment === "paid" && r.paymentDate !== r.postingDate && <div className={s.inlineNote}>전기일에는 비용과 외상매입금을, 지급일에는 외상매입금 정산과 은행 출금을 기록합니다.</div>}
                <p className={s.helper}>이 예시는 은행 지급·외상매입금 흐름을 다룹니다. 카드 결제, 선급금, 환불·취소 등은 별도 검토 흐름이 필요합니다.</p>
                <label className={s.checkbox}><input type="checkbox" checked={r.checked} onChange={e => update({ checked: e.target.checked })}/> 원본 증빙과 연결 거래를 확인했습니다. *</label>
              </fieldset>
            </section>
          </div>
          <aside className={s.preview}><div className={s.previewHeading}><span className={s.eyebrow}>LIVE PREVIEW</span><h2>분개 미리보기</h2><p>입력한 정보가 바로 반영됩니다.</p></div>{Array.from(new Set(draft.map(l => l.voucher))).map(v => <div className={s.voucher} key={v}><div className={s.voucherTitle}>{draft.find(l => l.voucher === v)?.date}<small>{v}</small></div>{draft.filter(l => l.voucher === v).map((l, i) => <div className={s.journalLine} key={i}><span className={l.debit ? s.debit : s.credit}>{l.debit ? "차" : "대"}</span><div>{name(l.account)}<small>{l.account}</small></div><strong>{money(l.debit || l.credit)}</strong></div>)}</div>)}<div className={s.balance}><span>차변 합계 <b>{money(debit)}</b></span><span>대변 합계 <b>{money(credit)}</b></span><strong><Check size={14}/>{debit === credit ? "차변·대변 일치" : "금액 확인 필요"}</strong></div><div className={s.checklist}><h3>{r.confirmed ? "검토 확정됨" : gaps.length ? `${gaps.length}개 항목을 확인하세요` : "확정할 준비가 되었습니다"}</h3>{gaps.map(g => <div key={g}><span className={s.dot}/>{g}</div>)}{!gaps.length && <p><CheckCircle2 size={17}/> 필요한 정보를 모두 입력했습니다.</p>}</div><button className={s.primary} disabled={r.confirmed || gaps.length > 0 || debit !== credit} onClick={() => { setReceipts(list => list.map(x => x.id === selected ? { ...x, confirmed: true } : x)); setNotice(`${r.merchant} 샘플 분개를 확정했습니다. 계정별원장에서 확인할 수 있습니다.`); }}> <Check size={16}/>{r.confirmed ? "확정 완료" : "분개 확정"}</button><button className={s.textButton} onClick={() => { setLedgerAccount(r.account || "81100"); setStep(3); }}>계정별원장 보기 <ArrowRight size={14}/></button><small className={s.previewFoot}>확정은 이 화면의 샘플 상태에만 반영됩니다.</small></aside>
        </div>}

        {step === 3 && <>
          <section className={s.card}><div className={s.cardHeading}><div><h2>{company} · {year} 계정별원장</h2><p>확정 전표 {confirmed.length}건의 예시 원장입니다. 회사 전체 거래와 결산 내역은 포함하지 않습니다.</p></div><button className={s.secondary} onClick={download}><Download size={16}/> 샘플 CSV 다운로드</button></div><div className={s.ledgerControls}><label>조회 계정<select value={ledgerAccount} onChange={e => setLedgerAccount(e.target.value)}>{accounts.map(a => <option key={a.code} value={a.code}>{a.code} · {a.name}</option>)}</select></label><div><small>기초잔액</small><strong>{money(account.opening)} 원</strong></div><div><small>조회 기말잔액</small><strong>{money(balance)} 원</strong></div><div><small>잔액 기준</small><strong>{account.normal === "debit" ? "차변" : "대변"}</strong></div></div><div className={s.tableWrap}><table className={s.ledgerTable}><thead><tr><th>날짜</th><th>적요</th><th>거래처 코드</th><th>거래처</th><th>차변</th><th>대변</th><th>잔액</th></tr></thead><tbody><tr><td>{year}-01-01</td><td>기초잔액 (입력값)</td><td>—</td><td>—</td><td>—</td><td>—</td><td>{money(account.opening)}</td></tr>{ledgerRows.map((l, i) => <tr key={i}><td>{l.date}</td><td>{l.memo}<small className={s.tableSub}>{l.voucher}</small></td><td>{l.vendorCode}</td><td>{l.vendor}</td><td>{l.debit ? money(l.debit) : "—"}</td><td>{l.credit ? money(l.credit) : "—"}</td><td>{money(l.balance)}</td></tr>)}{!ledgerRows.length && <tr><td colSpan={7} className={s.empty}>이 계정에 확정된 샘플 분개가 없습니다. 분개 검토에서 내용을 확정하세요.</td></tr>}</tbody><tfoot><tr><td colSpan={4}>조회 전표 합계</td><td>{money(ledger.reduce((sum, l) => sum + l.debit, 0))}</td><td>{money(ledger.reduce((sum, l) => sum + l.credit, 0))}</td><td>{money(balance)}</td></tr></tfoot></table></div><p className={s.helper}>원장 양식의 ‘코드’는 거래처 코드입니다. 계정 코드는 위 조회 계정에 표시합니다. 기초잔액은 해당 계정의 잔액 기준으로 반영합니다.</p></section>
          <section className={s.card}><h2>완성된 원장을 위해 더 필요한 자료</h2><div className={s.completionGrid}>{["매출·매출채권", "자본금·지원금·이자", "미지급·미수·선급 내역", "기초잔액·기말 조정"].map(t => <div key={t}><FileText size={19}/><b>{t}</b><small>자료 연결 및 별도 전표 검토 필요</small></div>)}</div><button className={s.textButton} onClick={() => setStep(1)}>추가 자료 입력으로 이동 <ArrowRight size={14}/></button></section>
        </>}
        <footer className={s.footer}><span>JOURNALIZER · RECEIPTS TO LEDGER</span><span>원본 → 추가 정보 → 검토 → 원장</span></footer>
      </div>
    </main>
  </div>;
}
