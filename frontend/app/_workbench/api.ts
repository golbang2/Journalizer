// Same-origin "/api" is proxied to the backend by next.config.ts, so the login cookie is first-party.
// Inlined at build time: restart `npm run dev` (and clear .next) after changing NEXT_PUBLIC_API_BASE_URL.
export const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

export type Account = { code: string; name: string; normal: "debit" | "credit"; opening: number; kind: "asset" | "liability" | "expense" | "other" };
export type Vendor = { code: string; name: string; alias: string; business: string; account: string; purpose: string; auto: boolean };
export type BankAccount = { code: string; name: string; number: string; account: string };
export type Workspace = { company: string; year: string; accounts: Account[]; vendors: Vendor[]; banks: BankAccount[] };

export type Extraction = {
  merchant: string; purchased_at: string; total: string; tax: string; payment_method: string;
  category_hint: string; confidence: number; warnings: string[]; approval_number: string | null;
  business_number: string | null; extraction_provider: string | null;
};

export type LedgerLine = {
  receipt_id: string; voucher: string; date: string; memo: string; vendor: string;
  vendor_code: string; account: string; debit: number; credit: number;
};

export type Review = {
  merchant: string; date: string; total: number; purpose: string; project: string; owner: string;
  vendor_code: string; posting_date: string; account: string; vat: "pending" | "deductible" | "included";
  tax: number; payment: "pending" | "paid" | "unpaid"; payment_date: string; bank: string;
  payment_transaction_id: string; payment_link: string; payable_offset: number; payable_link: string;
  evidence: string; checked: boolean;
};

export type Receipt = Review & {
  id: string; file_name: string; content_type: string; uploaded_at: string;
  extraction: Extraction | null; extraction_error: string | null; original_tax: number;
  account_suggestion: string; account_suggestion_reason: string; autofilled: Record<string, string>;
  confirmed: boolean; confirmed_at: string | null;
  lines: LedgerLine[]; missing: string[]; notices: string[]; balanced: boolean; duplicate_of: string;
};

export type BankTransaction = {
  id: string; bank: string; bank_name: string; account_number: string; posted_at: string;
  kind: string; description: string; withdrawal: number; deposit: number; balance: number | null; source_file: string;
};

export type PaymentCandidate = { transaction: BankTransaction; score: number; reasons: string[]; linked_receipt_id: string | null };
export type StatementImport = { autofilled: number; bank: string; bank_name: string; file_name: string; rows: number; added: number; duplicates: number; first: string; last: string };
export type ProposedBank = { file_name: string; code: string; existing: boolean; name: string; number: string; product: string; account: string; opening: number | null; opening_note: string; first: string; last: string; rows: number };
export type ProposedVendor = { code: string; name: string; count: number; withdrawal: number; deposit: number; account: string; account_reason: string; selected: boolean; note: string };
export type WorkspaceProposal = { company: string; year: string; year_note: string; banks: ProposedBank[]; openings: Record<string, number>; vendors: ProposedVendor[] };
export type LedgerView = { account: Account; rows: (LedgerLine & { balance: number })[]; debit_total: number; credit_total: number; closing: number };

export type Me = { id: number; email: string; name: string; has_password: boolean; google: boolean; company: string; receipts: number; transactions: number; imported: { receipts: number; files: number; transactions: number } | null };

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

const BACKEND_DOWN = "백엔드 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.";
let unauthorized: () => void = () => undefined;
/** Called when any request finds the session gone (expired, or logged out in another tab). */
export const onUnauthorized = (handler: () => void) => { unauthorized = handler; };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiBase + path, { credentials: "include", ...init });
  } catch {
    throw new ApiError(BACKEND_DOWN, 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    if (response.status === 401 && !path.startsWith("/auth/")) unauthorized();
    // The dev proxy answers 500 with plain text when the backend is not running.
    if (!body && response.status >= 500) throw new ApiError(BACKEND_DOWN, response.status);
    throw new ApiError(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join(", ") : `요청이 실패했습니다 (${response.status}).`, response.status);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body)
});

function upload(file: File, extra: Record<string, string> = {}): RequestInit {
  const form = new FormData();
  form.append("file", file);
  Object.entries(extra).forEach(([key, value]) => form.append(key, value));
  return { method: "POST", body: form };
}

export const api = {
  me: () => request<Me>("/auth/me"),
  authConfig: () => request<{ google: boolean }>("/auth/config"),
  signup: (email: string, password: string, name: string) => request<Me>("/auth/signup", json("POST", { email, password, name })),
  login: (email: string, password: string) => request<Me>("/auth/login", json("POST", { email, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  workspace: () => request<Workspace>("/workspace"),
  saveWorkspace: (workspace: Workspace) => request<Workspace>("/workspace", json("PUT", workspace)),
  proposeWorkspace: (files: File[]) => {
    const form = new FormData();
    files.forEach(file => form.append("files", file));
    return request<WorkspaceProposal>("/workspace/from-statements", { method: "POST", body: form });
  },
  receipts: () => request<Receipt[]>("/receipts"),
  uploadReceipt: (file: File) => request<Receipt>("/receipts", upload(file)),
  updateReceipt: (id: string, changes: Partial<Review>) => request<Receipt>(`/receipts/${id}`, json("PATCH", changes)),
  reextract: (id: string) => request<Receipt>(`/receipts/${id}/extract`, { method: "POST" }),
  bulkUpdate: (ids: string[], changes: Partial<Review>) => request<Receipt[]>("/receipts/bulk-update", json("POST", { ids, changes })),
  autofill: (ids: string[] = []) => request<Receipt[]>("/receipts/autofill", json("POST", { ids })),
  bulkConfirm: (ids: string[]) => request<{ confirmed: string[]; skipped: Record<string, string[]>; receipts: Receipt[] }>("/receipts/bulk-confirm", json("POST", { ids })),
  deleteDuplicates: () => request<{ deleted: string[] }>("/receipts/delete-duplicates", { method: "POST" }),
  confirm: (id: string) => request<Receipt>(`/receipts/${id}/confirm`, { method: "POST" }),
  reopen: (id: string) => request<Receipt>(`/receipts/${id}/reopen`, { method: "POST" }),
  deleteReceipt: (id: string) => request<void>(`/receipts/${id}`, { method: "DELETE" }),
  candidates: (id: string) => request<PaymentCandidate[]>(`/receipts/${id}/payment-candidates`),
  transactions: () => request<BankTransaction[]>("/bank-transactions"),
  importStatement: (file: File, bank: string) => request<StatementImport>("/bank-statements", upload(file, bank ? { bank } : {})),
  deleteTransactions: (bank: string) => request<{ deleted: number }>(`/bank-transactions?bank=${encodeURIComponent(bank)}`, { method: "DELETE" }),
  ledger: (code: string) => request<LedgerView>(`/ledger/${encodeURIComponent(code)}`),
};

export const googleLoginUrl = () => `${apiBase}/auth/google/login`;
export const fileUrl = (id: string) => `${apiBase}/receipts/${id}/file`;
export const ledgerCsvUrl = (code: string) => `${apiBase}/ledger/${encodeURIComponent(code)}/export.csv`;
export const workbookUrl = () => `${apiBase}/exports/workbook.xlsx`;

export const money = (v: number) => new Intl.NumberFormat("ko-KR").format(v);
export const isDone = (r: Receipt) => r.confirmed && r.missing.length === 0;
