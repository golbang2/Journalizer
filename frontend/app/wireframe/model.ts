export type Account = { code: string; name: string; normal: "debit" | "credit"; opening: number };
export const accounts: Account[] = [
  ["10300", "보통예금", "debit"], ["13500", "부가세대급금", "debit"],
  ["25100", "외상매입금", "credit"], ["25300", "미지급금", "credit"],
  ["81100", "복리후생비", "debit"], ["81200", "여비교통비", "debit"],
  ["81300", "접대비", "debit"], ["81700", "세금과공과금", "debit"],
  ["81900", "지급임차료", "debit"], ["83000", "소모품비", "debit"],
  ["83100", "지급수수료", "debit"], ["85300", "외주개발비", "debit"],
].map(([code, name, normal]) => ({ code, name, normal: normal as Account["normal"], opening: 0 }));
export type Receipt = {
  id: string; merchant: string; date: string; total: number; originalTax: number; file: string;
  purpose: string; project: string; owner: string; vendorCode: string; postingDate: string;
  account: string; vat: "pending" | "deductible" | "included"; tax: number;
  payment: "pending" | "paid" | "unpaid"; paymentDate: string; bank: string;
  paymentLink: string; payableOffset: number; payableLink: string; evidence: string;
  checked: boolean; confirmed: boolean;
};
function receipt(values: Partial<Receipt> & Pick<Receipt, "id" | "merchant" | "date" | "total" | "file">): Receipt {
  return { originalTax: 0, purpose: "", project: "", owner: "", vendorCode: "", postingDate: values.date,
    account: "", vat: "pending", tax: 0, payment: "pending", paymentDate: values.date, bank: "99000",
    paymentLink: "", payableOffset: 0, payableLink: "", evidence: "", checked: false, confirmed: false, ...values };
}
export const receipts: Receipt[] = [
  receipt({ id: "R-031", merchant: "크몽", date: "2023-11-07", total: 4765000, file: "우리은행 / page_031.pdf", account: "85300", purpose: "외주 개발 용역", vat: "included", payment: "paid", payableOffset: 144790 }),
  receipt({ id: "R-044", merchant: "홍익경영파트너스", date: "2023-11-15", total: 54140, originalTax: 4922, tax: 4922, file: "우리은행 / page_044.pdf", account: "83100", purpose: "경영 자문 수수료", vendorCode: "00109", payment: "paid", paymentDate: "2023-11-19" }),
  receipt({ id: "R-001", merchant: "컴포즈커피", date: "2023-05-08", total: 3800, originalTax: 345, file: "광주은행 / 1.pdf", account: "81100", purpose: "직원 음료", project: "공통 운영", owner: "담당자 예시", vendorCode: "S001", vat: "included", payment: "paid", bank: "99001", paymentLink: "샘플: 광주은행 05/08 · 3,800원", evidence: "샘플: 카드 영수증 · 공제 검토 완료", checked: true, confirmed: true }),
  receipt({ id: "R-002", merchant: "크몽", date: "2023-09-23", total: 56920, file: "우리은행 / page_001.pdf" }),
];
export type Line = { receiptId: string; date: string; memo: string; vendor: string; vendorCode: string; account: string; debit: number; credit: number; voucher: string };
export function journal(r: Receipt): Line[] {
  const result: Line[] = [];
  const add = (account: string, debit: number, credit: number, date = r.postingDate, payment = false) => {
    if (debit || credit) result.push({ receiptId: r.id, date, memo: payment ? `${r.merchant} 대금 지급` : r.purpose || "사용 목적 미입력", vendor: account === "10300" ? (r.bank === "99000" ? "우리은행" : "광주은행") : r.merchant, vendorCode: account === "10300" ? r.bank : r.vendorCode, account, debit, credit, voucher: `${r.id}-${payment ? "02" : "01"}` });
  };
  const tax = r.vat === "deductible" ? r.tax : 0;
  const offset = r.payment === "paid" && r.paymentDate === r.postingDate ? r.payableOffset : 0;
  add(r.account || "미선택", r.total - tax - offset, 0);
  add("13500", tax, 0);
  add("25100", offset, 0);
  const later = r.payment === "paid" && r.paymentDate !== r.postingDate;
  add(r.payment === "paid" && !later ? "10300" : "25100", 0, r.total);
  if (later) { add("25100", r.total, 0, r.paymentDate, true); add("10300", 0, r.total, r.paymentDate, true); }
  return result;
}
export function missing(r: Receipt, year: string): string[] {
  const items: string[] = [];
  if (!r.purpose.trim()) items.push("사용 목적");
  if (!r.vendorCode.trim()) items.push("거래처 코드");
  if (!r.account) items.push("비용 계정");
  if (!r.postingDate || !r.postingDate.startsWith(year + "-")) items.push("회계연도에 맞는 전기일");
  if (r.vat === "pending") items.push("부가세 처리 판단");
  if (r.vat === "deductible" && (!Number.isFinite(r.tax) || r.tax < 0 || r.tax > r.total)) items.push("유효한 공제 부가세");
  if (!r.evidence.trim()) items.push("증빙·부가세 검토 근거");
  if (r.payment === "pending") items.push("지급 상태");
  if (r.payment === "paid" && (!r.paymentDate || r.paymentDate < r.postingDate || !r.paymentDate.startsWith(year + "-"))) items.push("지급일 (전기일 이후, 같은 회계연도)");
  if (r.payment === "paid" && !r.paymentLink.trim()) items.push("출금 거래 연결");
  if (!Number.isFinite(r.payableOffset) || r.payableOffset < 0 || r.payableOffset + (r.vat === "deductible" ? r.tax : 0) > r.total) items.push("유효한 기존 채무 정산액");
  if (r.payableOffset > 0 && (r.payment !== "paid" || r.paymentDate !== r.postingDate)) items.push("기존 채무 정산은 당일 지급 예시에서 입력");
  if (r.payableOffset > 0 && !r.payableLink.trim()) items.push("기존 채무 전표 연결");
  if (!r.checked) items.push("원본·거래 내역 확인");
  return items;
}
export const money = (v: number) => new Intl.NumberFormat("ko-KR").format(v);
