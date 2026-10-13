// Same-origin "/api" is proxied to the backend by next.config.ts, so the login cookie is first-party.
// Inlined at build time: restart `npm run dev` (and clear .next) after changing NEXT_PUBLIC_API_BASE_URL.
export const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";

export type Account = { code: string; name: string; normal: "debit" | "credit"; opening: number; kind: "asset" | "liability" | "equity" | "revenue" | "expense" | "other" };
export type Vendor = { code: string; name: string; alias: string; aliases?: string[]; payment_aliases?: string[]; business: string; account: string; purpose: string; auto: boolean };
export type BankAccount = { code: string; name: string; number: string; account: string };
export type Card = { code: string; name: string; number: string; bank: string; payable: boolean };
export type CompanyProfile = { company: string; business_number: string; representative: string; opened: string; business_type: string; capital: number; shareholders: string[]; officers: string[]; summary: string; error: string };
export type Precedent = { text: string; account: string; count: number; sources?: PrecedentSource[] };
export type Workspace = {
  profile?: CompanyProfile | null; company: string; year: string; accounts: Account[]; vendors: Vendor[]; banks: BankAccount[]; cards: Card[];
  card_vat: "expense" | "deduct"; precedents: Precedent[]; precedent_source: string;
};
export type ScheduleRow = { kind: "card" | "invoice"; business_number: string; name: string; count: number; supply: number; tax: number };
export type VatReturn = { id: string; period_start: string; period_end: string; filed: string; output_tax: number; input_tax: number; nondeductible_account: string; note: string; schedule?: ScheduleRow[]; schedule_file?: string };
export type ReconcileRow = { business_number: string; name: string; kind: string; filed_count: number; filed_tax: number; book_count: number; book_tax: number; difference: number; status: string; zero_vat_receipts: string[] };
export type Reconcile = { rows: ReconcileRow[]; filed_total: number; book_total: number; return_input_tax: number; matched: number; fixed?: string[] };
export type SalesInvoice = { id: string; file_name: string; issued: string; customer: string; business_number: string; representative: string; item: string; supply: number; tax: number; revenue_account: string; note: string };
export type PrecedentSource = { file_name: string; sheet: string; row_number: number; date: string };
export type PolicyRule = { terms: string[]; account: string; exceptions: string[]; direction: "" | "출금" | "입금"; document_type: "" | "receipt" | "tax_invoice"; minimum: number; maximum: number | null };
export type PolicyInput = { title: string; series: string; version: string; kind: "accounting" | "tax"; source: string; locator: string; text: string; effective_from: string; effective_to: string | null; rules: PolicyRule[] };
export type AccountingPolicy = PolicyInput & { id: string; status: "draft" | "approved" | "retired"; created_at: string; approved_at: string };
export type Evidence = { source_type: "receipt" | "bank" | "precedent" | "policy"; source_id: string; description: string; account: string; account_name: string; date: string; amount: number | null; purpose: string; reason: string; path: string[]; sources: PrecedentSource[]; policy_version?: string; policy_kind?: string; source?: string; locator?: string; policy_text?: string; exceptions?: string[] };
export type AiSuggestion = { account: string; tax: number; vendor: string; purpose: string; reason: string; confidence: number; model: string; at: string; evidence?: Evidence[]; retrieval_version?: string; review_required?: boolean };

export type Extraction = {
  merchant: string; purchased_at: string; total: string; tax: string; payment_method: string;
  category_hint: string; confidence: number; warnings: string[]; approval_number: string | null;
  business_number: string | null; extraction_provider: string | null;
  document_type: "receipt" | "tax_invoice"; representative: string;
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
  confirm_year?: boolean; confirm_vendor?: boolean;
};

export type Receipt = Review & {
  id: string; file_name: string; content_type: string; uploaded_at: string;
  extraction: Extraction | null; extraction_error: string | null; original_tax: number;
  account_suggestion: string; account_suggestion_reason: string; autofilled: Record<string, string>;
  confirmed: boolean; confirmed_at: string | null; card: string; ai: AiSuggestion | null;
  lines: LedgerLine[]; missing: string[]; notices: string[]; balanced: boolean; duplicate_of: string;
  year_review_required: boolean; vendor_review_required: boolean;
};

export type BankTransaction = {
  id: string; bank: string; bank_name: string; account_number: string; posted_at: string;
  kind: string; description: string; withdrawal: number; deposit: number; balance: number | null; source_file: string;
};

export type EntryReview = { account: string; tax: number; fee: number; memo: string; vendor_code: string; vendor: string; card: string; transfer_to: string; skip: boolean };
export type Entry = EntryReview & {
  tx_id: string; autofilled: Record<string, string>; ai: AiSuggestion | null; confirmed: boolean; confirmed_at: string | null;
  transaction: BankTransaction; lines: LedgerLine[]; missing: string[]; balanced: boolean; covered_by: string; checks: string[]; requires_person: boolean;
};
export type AutoResult = { receipts: number; entries: number; receipts_left: Record<string, string>; entries_left: Record<string, string> };
export type JudgeResult = { requested: number; judged: number; model: string; error: string; auto?: AutoResult };

export type PaymentCandidate = { transaction: BankTransaction; score: number; reasons: string[]; linked_receipt_id: string | null };
export type StatementImport = { ai: JudgeResult | null; autofilled: number; bank: string; bank_name: string; file_name: string; rows: number; added: number; duplicates: number; first: string; last: string };
export type ProposedBank = { file_name: string; code: string; existing: boolean; name: string; number: string; product: string; account: string; opening: number | null; opening_note: string; first: string; last: string; rows: number };
export type ProposedVendor = { code: string; name: string; count: number; withdrawal: number; deposit: number; account: string; account_reason: string; selected: boolean; note: string };
export type ProposedCard = Card & { existing: boolean; rows: number; file_name: string };
export type WorkspaceProposal = { company: string; year: string; year_note: string; banks: ProposedBank[]; openings: Record<string, number>; vendors: ProposedVendor[]; cards: ProposedCard[]; accounts: Account[] };
export type StatementItem = { label: string; amount: number; level: number; code: string };
export type Statement = { title: string; items: StatementItem[]; checks: string[] };
export type Statements = { balance_sheet: Statement; income_statement: Statement; deficit: Statement };
export type LedgerView = { account: Account; rows: (LedgerLine & { balance: number })[]; debit_total: number; credit_total: number; closing: number };

export type Me = { id: number; email: string; name: string; has_password: boolean; google: boolean; company: string; receipts: number; transactions: number; imported: { receipts: number; files: number; transactions: number } | null; onboarded: boolean };
export type AiSource = "company" | "server" | "none";
export type AiProvider = { id: string; label: string; console: string; key_hint: string; source: AiSource; masked: string };
export type AiStatus = { provider: string; model: string; label: string; configured: boolean; source: AiSource; masked: string; providers: AiProvider[]; models: string[] };
export type CompanyDocument = { kind: string; label: string; file_name: string; uploaded_at: string };

export type HometaxKind = "card" | "invoice";
export type HometaxCandidate = { id: string; merchant: string; date: string; total: number; evidence_tax: number; book_tax: number; vat: string; confirmed: boolean; same_kind: boolean };
export type HometaxRow = {
  id: string; kind: HometaxKind; date: string; business_number: string; merchant: string; approval_number: string;
  supply: number; tax: number; total: number; service: number; deduction_text: string; status: string; adjustment: boolean;
  receipt_id: string; manual: boolean; issues: string[]; candidates: HometaxCandidate[]; status_label: string;
  sources: { import_id: string; file_name: string; sheet: string; row_number: number; tax: number; total: number; deduction_text: string }[];
};
export type HometaxReport = {
  rows: HometaxRow[];
  imports: { id: string; kind: HometaxKind; file_name: string; uploaded_at: string; rows: number; first: string; last: string }[];
  unmatched_receipts: { id: string; merchant: string; date: string; total: number }[];
};

export class ApiError extends Error {
  /** Seconds the server asked us to wait (429 answers). */
  constructor(message: string, readonly status: number, readonly retryAfter = 0) { super(message); }
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
    throw new ApiError(typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join(", ") : `요청이 실패했습니다 (${response.status}).`, response.status, Number(response.headers.get("Retry-After")) || 0);
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body)
});

// Same caps as the backend (app/limits.py). Checked here too because an oversized body is cut off
// mid-upload, which the browser only sees as a broken connection.
const MAX_FILE_MB = 15;
const MAX_FILES = 10;

function checkSize(file: File) {
  if (file.size > MAX_FILE_MB * 1024 * 1024) throw new ApiError(`${file.name}: 파일은 ${MAX_FILE_MB}MB 이하만 올릴 수 있습니다.`, 413);
}

function upload(file: File, extra: Record<string, string> = {}): RequestInit {
  checkSize(file);
  const form = new FormData();
  form.append("file", file);
  Object.entries(extra).forEach(([key, value]) => form.append(key, value));
  return { method: "POST", body: form };
}

export const api = {
  policies: () => request<AccountingPolicy[]>("/policies"),
  createPolicy: (body: PolicyInput) => request<AccountingPolicy>("/policies", json("POST", body)),
  updatePolicy: (id: string, body: PolicyInput) => request<AccountingPolicy>(`/policies/${encodeURIComponent(id)}`, json("PUT", body)),
  policyAction: (id: string, action: "approve" | "retire") => request<AccountingPolicy>(`/policies/${encodeURIComponent(id)}/${action}`, { method: "POST" }),
  importHometax: (file: File, kind: HometaxKind) => request<{ id: string; added: boolean; rows: number }>("/hometax/imports", upload(file, { kind })),
  hometaxReport: (start: string, end: string) => request<HometaxReport>(`/hometax/reconcile?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  deleteHometax: (id: string) => request<void>(`/hometax/imports/${encodeURIComponent(id)}`, { method: "DELETE" }),
  linkHometax: (id: string, receiptId: string) => request<void>(`/hometax/rows/${encodeURIComponent(id)}/link`, json("PUT", { receipt_id: receiptId })),
  me: (signal?: AbortSignal) => request<Me>("/auth/me", { signal }),
  authConfig: () => request<{ google: boolean }>("/auth/config"),
  signup: (email: string, password: string, name: string) => request<Me>("/auth/signup", json("POST", { email, password, name })),
  login: (email: string, password: string) => request<Me>("/auth/login", json("POST", { email, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  workspace: () => request<Workspace>("/workspace"),
  saveWorkspace: (workspace: Workspace) => request<Workspace>("/workspace", json("PUT", workspace)),
  proposeWorkspace: (files: File[]) => {
    if (files.length > MAX_FILES) throw new ApiError(`한 번에 ${MAX_FILES}개 파일까지 올릴 수 있습니다.`, 413);
    files.forEach(checkSize);
    const form = new FormData();
    files.forEach(file => form.append("files", file));
    return request<WorkspaceProposal>("/workspace/from-statements", { method: "POST", body: form });
  },
  receipts: () => request<Receipt[]>("/receipts"),
  receipt: (id: string) => request<Receipt>(`/receipts/${encodeURIComponent(id)}`),
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
  aiSettings: () => request<AiStatus>("/settings/ai"),
  saveAi: (provider: string, apiKey = "", model = "") => request<AiStatus>("/settings/ai", json("PUT", { provider, api_key: apiKey, model })),
  aiModels: (provider: string) => request<{ models: string[] }>(`/settings/ai/models?provider=${encodeURIComponent(provider)}`),
  previewAiModels: (provider: string, apiKey: string) => request<{ models: string[]; default: string }>("/settings/ai/models", json("POST", { provider, api_key: apiKey })),
  deleteAiKey: (provider: string) => request<AiStatus>(`/settings/ai?provider=${encodeURIComponent(provider)}`, { method: "DELETE" }),
  uploadPrecedents: (file: File) => request<{ count: number; rows: number; source: string }>("/workspace/precedents", upload(file)),
  deletePrecedents: () => request<{ count: number }>("/workspace/precedents", { method: "DELETE" }),
  salesInvoices: () => request<SalesInvoice[]>("/sales-invoices"),
  uploadSalesInvoice: (file: File) => request<SalesInvoice>("/sales-invoices", upload(file)),
  addSalesInvoice: (item: Partial<SalesInvoice>) => request<SalesInvoice>("/sales-invoices/manual", json("POST", { id: "new", ...item })),
  deleteSalesInvoice: (id: string) => request<void>(`/sales-invoices/${encodeURIComponent(id)}`, { method: "DELETE" }),
  documents: () => request<CompanyDocument[]>("/company/documents"),
  uploadDocument: (kind: string, file: File) => request<CompanyDocument[]>(`/company/documents/${kind}`, upload(file)),
  statements: () => request<Statements>("/reports/statements"),
  vatReturns: () => request<VatReturn[]>("/vat-returns"),
  bookedVat: (start: string, end: string) => request<{ output_tax: number; input_tax: number }>(`/vat-returns/booked?start=${start}&end=${end}`),
  addVatReturn: (item: Omit<VatReturn, "id">) => request<VatReturn>("/vat-returns", json("POST", { id: "new", ...item })),
  deleteVatReturn: (id: string) => request<void>(`/vat-returns/${encodeURIComponent(id)}`, { method: "DELETE" }),
  uploadVatSchedule: (id: string, file: File) => request<Reconcile>(`/vat-returns/${encodeURIComponent(id)}/schedule`, upload(file)),
  reconcileVat: (id: string) => request<Reconcile>(`/vat-returns/${encodeURIComponent(id)}/reconcile`),
  recheckVat: () => request<{ checked: number; fixed: number; error: string }>("/receipts/vat-check", { method: "POST" }),
  entries: () => request<Entry[]>("/entries"),
  updateEntry: (id: string, changes: Partial<EntryReview>) => request<Entry>(`/entries/${encodeURIComponent(id)}`, json("PATCH", changes)),
  confirmEntry: (id: string) => request<Entry>(`/entries/${encodeURIComponent(id)}/confirm`, { method: "POST" }),
  reopenEntry: (id: string) => request<Entry>(`/entries/${encodeURIComponent(id)}/reopen`, { method: "POST" }),
  bulkConfirmEntries: (ids: string[]) => request<{ confirmed: string[]; skipped: Record<string, string[]> }>("/entries/bulk-confirm", json("POST", { ids })),
  judge: (ids: string[] = []) => request<JudgeResult>("/ai/judge", json("POST", { ids })),
  autoProcess: () => request<JudgeResult>("/auto/process", { method: "POST" }),
};

export const googleLoginUrl = () => `${apiBase}/auth/google/login`;
export const fileUrl = (id: string) => `${apiBase}/receipts/${id}/file`;
export const ledgerCsvUrl = (code: string) => `${apiBase}/ledger/${encodeURIComponent(code)}/export.csv`;
export const workbookUrl = () => `${apiBase}/exports/workbook.xlsx`;
export const documentUrl = (kind: string) => `${apiBase}/company/documents/${kind}/file`;
export const ledgerBookUrl = () => `${apiBase}/exports/ledger-book.xlsx`;
export const closingReportUrl = () => `${apiBase}/exports/closing-report.xlsx`;
export const incomeStatementUrl = () => `${apiBase}/exports/income-statement.xlsx`;
/** Fields the model filled stay highlighted until a person confirms or edits them. */
export const autoMessage = (r: JudgeResult) => {
  const left = r.auto ? Object.keys(r.auto.entries_left).length + Object.keys(r.auto.receipts_left).length : 0;
  return [r.requested ? `${r.model}가 ${r.judged}건을 판단했습니다.` : "",
    r.auto ? `통장 거래 ${r.auto.entries}건, 영수증 ${r.auto.receipts}건을 장부에 반영했습니다.` : "",
    left ? `${left}건은 확인이 필요합니다 (영수증 없는 이체는 ‘이체 확인’ 탭에서 직접 확정).` : "",
    r.error ? `AI: ${r.error}` : ""].filter(Boolean).join(" ") || "새로 처리할 거래가 없습니다.";
};
export const isAi = (reason: string | undefined) => !!reason && reason.startsWith("AI 추천");

export const money = (v: number) => new Intl.NumberFormat("ko-KR").format(v);
export const isDone = (r: Receipt) => r.confirmed && r.missing.length === 0;
