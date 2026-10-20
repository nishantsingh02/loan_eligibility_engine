export type Product = {
  product_id: string;
  lender: string;
  product_name: string;
  product_url: string;
  advertised_rate?: string;
  observed_at?: string;
  extraction_status?: string;
};
export type Run = {
  run_id: string;
  upload_id?: string;
  filename: string;
  status: string;
  row_count: number;
  created_at: string;
  error_code?: string;
  product_count?: number;
  processed_decisions?: number;
  processed_applicants?: number;
  applicant_summary?: Record<string, number>;
  products?: Product[];
};
export type Dashboard = {
  total_rows: number;
  total_runs: number;
  products: Product[];
  storage_mode: string;
  max_upload_bytes: number;
};
export type Rule = {
  kind: string;
  min?: number;
  max?: number;
  status?: string;
  currency?: string;
  location_dependent?: boolean;
};
export type Policy = {
  snapshot_id: string;
  lender: string;
  product_name: string;
  source_url: string;
  source_excerpt: string;
  observed_at: string;
  advertised_rate?: string;
  rate_source_quote?: string;
  reviewer_note?: string;
  rules: Record<string, Rule>;
};
export type Applicant = {
  user_id: string;
  status: string;
  processed_decisions: number;
  matched_products: Product[];
};
export type Reason = {
  criterion: string;
  status: string;
  message?: string;
  observed_value?: string | number;
  required_value?: string;
  minimum?: number;
  maximum?: number;
  preferred_minimum?: number;
  currency?: string;
};
export type Result = {
  result_id: string;
  lender: string;
  product_name: string;
  decision: string;
  reasons: Reason[];
  source_url: string;
  snapshot_id: string;
  rule_version: string;
  observed_at: string;
  advertised_rate?: string;
};
export type Job = {
  filename: string;
  started: number;
  upload_id?: string;
  run_id?: string;
  phase: "upload" | "import" | "match" | "ready" | "failed";
  percent: number;
  confirming?: boolean;
  error?: string;
  run?: Run;
  reconnecting?: boolean;
};
export type Request = <T = unknown>(
  path: string,
  init?: RequestInit,
) => Promise<T>;
export type Workspace = {
  busy: boolean;
  loaded: boolean;
  token: string;
  setToken: (v: string) => void;
  authError: string;
  dashboard: Dashboard | null;
  runs: Run[];
  totalRuns: number;
  pending: Policy[];
  error: string;
  request: Request;
  refresh: () => Promise<void>;
  job: Job | null;
  upload: (file: File) => Promise<void>;
  clearJob: () => void;
  retry: (runId: string) => Promise<void>;
  moreRuns: () => Promise<void>;
};
