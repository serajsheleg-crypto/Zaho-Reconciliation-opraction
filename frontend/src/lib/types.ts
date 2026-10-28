export type Role = "admin" | "analyst" | "viewer";

export interface User {
  id: number;
  name: string;
  email: string;
  role: Role;
  is_active: number | boolean;
  must_change_password: number | boolean;
  lang: "en" | "ar";
  created_at?: string;
  last_login_at?: string | null;
}

export interface ColumnProfile {
  name: string;
  filled: number;
  fill_rate: number;
  distinct: number;
  unique_ratio: number;
  examples: string[];
  amount_style: "dot" | "comma";
  date_formats: string[];
  date_serial: boolean;
  scores: Record<string, number>;
}

export interface Profile {
  rows: number;
  columns: ColumnProfile[];
  roles: Record<string, string | null>;
  confidence: Record<string, number>;
}

export interface Dataset {
  id: string;
  name: string;
  source_type: "file";
  file_type: string | null;
  size_bytes: number | null;
  status: "uploading" | "preparing" | "ready" | "failed";
  progress: number;
  stage: string | null;
  error: string | null;
  error_code: string | null;
  row_count: number | null;
  sheet?: string | null;
  sheets?: string[] | null;
  encoding?: string | null;
  delimiter?: string | null;
  warnings: string[];
  seconds?: number | null;
  profile?: Profile | null;
  preview?: { columns: string[]; rows: (string | null)[][] } | null;
  /** The line holding the column headers (1 = first line); null when a CSV has none. */
  header_row?: number | null;
  /** False when a person picked the header row. */
  header_auto?: boolean;
  /** The first lines of the file as they are, before any header is taken. */
  head?: (string | null)[][] | null;
  seen_in_run?: { id: number; name: string } | null;
}

/** Only rows whose column holds one of the values (ignoring case and surrounding spaces). */
export interface RowFilter {
  column: string;
  values: string[];
}

export type RoleKey = "key" | "amount" | "status" | "date" | "currency" | "fee";

export interface SideMap {
  key: string | null;
  amount: string | null;
  status: string | null;
  date: string | null;
  /** A separate time-of-day column, read together with the date. */
  time?: string | null;
  currency: string | null;
  fee: string | null;
  amount_style: "dot" | "comma";
  fee_style: "dot" | "comma";
  date_formats: string[];
  date_serial: boolean;
  /** From the side's template: which rows of the file are compared. */
  filters?: RowFilter[];
  /** From the side's template: a field read from the companion row of the same reference. */
  parts?: RowPart[];
  skip_no_key?: boolean;
}

export interface Extra {
  a: string;
  b: string;
  label?: string | null;
}

export interface Mapping {
  a: SideMap;
  b: SideMap;
  extras: Extra[];
}

export interface Rules {
  key_mode: "exact" | "case_insensitive" | "loose";
  amount_tolerance: number;
  amount_tolerance_pct: number;
  amount_absolute: boolean;
  date_tolerance_days: number | null;
  compare_amount?: boolean;
  compare_status?: boolean;
  compare_currency: boolean;
  compare_fee: boolean;
  /** The answer to "does fee / revenue reconciliation apply?"; null = not asked (older runs). */
  revenue_applies: boolean | null;
  service: { id: number; name?: string; revenue_applies?: boolean } | null;
  fallback_match: boolean;
  status_mode: StatusMode;
  approved_statuses?: ApprovedStatus[];
  status_map: { a: Record<string, string>; b: Record<string, string> };
  /** The company template each side's file was read with. */
  templates?: { a: TemplateRef | null; b: TemplateRef | null } | null;
  template_statuses?: { a: ApprovedStatus[]; b: ApprovedStatus[] } | null;
  /** The reconciliation template the run was started from, as it was then. */
  recon_template?: { id: number; name: string; version: number; label_a: string; label_b: string } | null;
}

export type StatusMode = "exact" | "auto";

export type FieldKey = RoleKey | "time";

export interface TemplateRef {
  id: number;
  name?: string | null;
  company?: string | null;
  company_id?: number | null;
}

export interface TemplateColumn {
  name: string;
  examples: string[];
  /** Rows with a value in this column. */
  filled?: number;
  /** Kept for columns with few distinct values, each with the meaning it would get as a status. */
  values?: { raw: string; count: number; meaning?: string | null }[];
}

export interface TemplateSpec {
  fields: Record<FieldKey, string | null>;
  amount_style: "dot" | "comma";
  fee_style: "dot" | "comma";
  date_formats: string[];
  date_serial: boolean;
  extras: { name: string; column: string }[];
  statuses: { label: string; meaning: string | null }[];
  columns: TemplateColumn[];
  sheet: string | null;
  filters: RowFilter[];
  /** Fields read from the companion row of the same reference (the fee on the profit_customer row). */
  parts?: RowPart[];
  skip_no_key: boolean;
  /** A header row a person picked (null = detected in every file). */
  header_row: number | null;
}

export interface Template {
  id: number;
  company_id: number;
  name: string;
  sample_name: string | null;
  sample_rows: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
  summary: {
    fields: Partial<Record<FieldKey, string>>;
    extras: string[];
    statuses: number;
    columns: number;
    sheet?: string | null;
    header_row?: number | null;
    filters?: RowFilter[];
    parts?: RowPart[];
    skip_no_key?: boolean;
  };
  spec?: TemplateSpec;
  /** The reconciliation templates built on this layout. */
  used_by?: { id: number; name: string }[];
  company?: string;
  is_ours?: boolean;
}

export interface Company {
  id: number;
  name: string;
  is_ours: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string | null;
  templates: Template[];
}

export interface AppliedTemplate extends TemplateRef {
  named: { name: string; column: string }[];
  missing: { field: string; column: string; instead?: string }[];
}

export interface ApprovedStatus {
  label: string;
  meaning: string;
}

export interface StatusValue {
  raw: string;
  count: number;
  /** The built-in dictionary's reading (automatic mode). */
  canonical: string;
  /** The approved name's meaning (exact mode), null when the value is not approved. */
  approved?: string | null;
}

export interface Service {
  id: number;
  name: string;
  revenue_applies: boolean;
  is_active: boolean;
  created_at?: string;
  updated_at?: string | null;
}

export interface StatusName {
  id: number;
  label: string;
  meaning: string;
  created_at?: string;
  updated_at?: string | null;
}

export interface CheckSide {
  rows: number;
  /** Rows the side's template filters leave out. */
  skipped: number;
  missing_reference: number;
  blank_amount: number | null;
  bad_amount: number | null;
  blank_fee: number | null;
  bad_fee: number | null;
  bad_date: number | null;
  blank_status: number | null;
  duplicate_rows: number;
  unknown_status: number;
  statuses: StatusValue[];
}

export interface CheckError {
  code: "missing_key" | "column_twice" | "fee_column_missing";
  side: "a" | "b";
  column?: string;
  roles?: string[];
}

export interface CheckResult {
  errors: CheckError[];
  not_compared: string[];
  sides: { a?: CheckSide; b?: CheckSide };
  seconds: number;
  rules: Pick<Rules, "status_mode" | "approved_statuses" | "service" | "revenue_applies" | "compare_fee" | "templates">;
}

export interface Suggestion {
  mapping: Mapping;
  rules: Rules;
  keys: { a: string; b: string; overlap: number; mode: string };
  statuses: { a: StatusValue[]; b: StatusValue[] };
  confidence: { a: Record<string, number>; b: Record<string, number> };
  canonical_statuses: string[];
  approved_statuses: ApprovedStatus[];
  templates?: { a: AppliedTemplate | null; b: AppliedTemplate | null };
  /** Other columns both files have under the same header, offered for comparison (off until picked). */
  candidates?: Extra[];
  /** Per side whose template filters its rows: a few values of each column, from the rows it keeps. */
  examples?: { a: Record<string, string[]> | null; b: Record<string, string[]> | null };
}

export interface CategoryStat {
  count: number;
  amount_a: number;
  amount_b: number;
  exposure: number;
}

export interface Quality {
  missing_reference: number;
  bad_amount: number;
  bad_date: number;
  unknown_status: number;
  duplicate_rows: number;
  bad_fee?: number;
  unknown_status_values: { raw: string; count: number }[];
  currencies: { currency: string; count: number; amount: number }[];
}

export interface Summary {
  rows_a: number;
  rows_b: number;
  results: number;
  matched: number;
  exceptions: number;
  match_rate: number;
  amount_a: number;
  amount_b: number;
  success_amount_a: number;
  success_amount_b: number;
  fee_a?: number;
  fee_b?: number;
  exposure: number;
  fallback_pairs: number;
  categories: Record<string, CategoryStat>;
  severities: Record<string, number>;
  status_a: { status: string; count: number; amount: number }[];
  status_b: { status: string; count: number; amount: number }[];
  status_matrix: { a: string; b: string; count: number }[];
  daily: { date: string; matched: number; exceptions: number; exposure: number }[];
  quality: { a: Quality; b: Quality };
  compared: {
    amount: boolean;
    status: boolean;
    date: boolean;
    currency: boolean;
    fee?: boolean;
    extras: string[];
    status_mode?: StatusMode;
    service?: { id: number; name: string; revenue_applies: boolean } | null;
    revenue_applies?: boolean | null;
    templates?: { a: TemplateRef | null; b: TemplateRef | null } | null;
  };
  seconds: number;
  stored_exceptions?: number;
  /** Rows a template's filters left out, per side. */
  skipped?: { a: number; b: number };
}

export interface Run {
  id: number;
  name: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  stage: string | null;
  label_a: string;
  label_b: string;
  dataset_a: string;
  dataset_b: string;
  source_a_name: string | null;
  source_b_name: string | null;
  rows_a: number | null;
  rows_b: number | null;
  matched: number | null;
  exceptions: number | null;
  match_rate: number | null;
  amount_a: number | null;
  amount_b: number | null;
  exposure: number | null;
  stored_exceptions?: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  created_by: number | null;
  created_by_name?: string | null;
  open_exceptions?: number;
  recon_template_id?: number | null;
  /** The reconciliation template the run was started from, as the run recorded it. */
  template_name?: string | null;
  summary?: Summary | null;
  config?: { mapping: Mapping; rules: Rules };
  review?: Record<string, number>;
  exports?: ExportJob[];
}

export interface ExportJob {
  id: string;
  format: "xlsx" | "csv" | "json" | "pdf";
  status: "queued" | "running" | "ready" | "failed";
  progress: number;
  filename: string | null;
  size_bytes: number | null;
  error: string | null;
  created_at: string;
}

export interface ResultRow {
  result_id: number;
  category: string;
  severity: string | null;
  mismatch: string | null;
  method: string | null;
  row_a: number | null;
  row_b: number | null;
  key_a: string | null;
  key_b: string | null;
  amount_a: number | null;
  amount_b: number | null;
  amount_diff: number | null;
  exposure: number | null;
  status_a: string | null;
  status_b: string | null;
  status_a_raw: string | null;
  status_b_raw: string | null;
  date_a: string | null;
  date_b: string | null;
  date_diff_days: number | null;
  currency_a: string | null;
  currency_b: string | null;
  fee_a?: number | null;
  fee_b?: number | null;
  fee_diff?: number | null;
  exception_id?: number | null;
  review_status?: string | null;
  assignee_name?: string | null;
  [extra: string]: unknown;
}

export interface ExceptionRow {
  id: number;
  run_id: number;
  result_id: number;
  category: string;
  severity: string;
  mismatch: string | null;
  method: string | null;
  ref_a: string | null;
  ref_b: string | null;
  amount_a: number | null;
  amount_b: number | null;
  amount_diff: number | null;
  exposure: number | null;
  status_a: string | null;
  status_b: string | null;
  date_a: string | null;
  date_b: string | null;
  review_status: string;
  assignee_id: number | null;
  assignee_name: string | null;
  resolution: string | null;
  run_name: string;
  template_name?: string | null;
  label_a: string;
  label_b: string;
  age_days: number;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

/** A field a layout reads from the companion row of the same reference ("fee" = Amount where Type is profit_customer). */
export interface RowPart {
  field: "amount" | "fee";
  column: string;
  by: string;
  values: string[];
}

/** Where a layout reads a field: a column, or a column of the companion row. */
export interface FieldSource {
  column: string;
  by?: string;
  values?: string[];
}

/** A field two layouts can be reconciled on: "key", "amount"… or "extra:Direction". */
export interface FieldPair {
  id: string;
  label: string;
  a: FieldSource | null;
  b: FieldSource | null;
}

export interface ReconRules {
  key_mode: "exact" | "case_insensitive" | "loose";
  amount_tolerance: number;
  amount_tolerance_pct: number;
  amount_absolute: boolean;
  date_tolerance_days: number;
  status_mode: StatusMode;
  fallback_match: boolean;
}

export interface LayoutRef {
  id: number;
  name: string;
  company_id: number;
  company: string;
  summary?: Template["summary"];
}

/** A recurring reconciliation: our layout, the partner's layout, and what is compared between them. */
export interface ReconTemplate {
  id: number;
  name: string;
  description: string | null;
  layout_a: number;
  layout_b: number;
  label_a: string;
  label_b: string;
  config: { compare: string[]; rules: ReconRules };
  version: number;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
  layouts: { a: LayoutRef | null; b: LayoutRef | null };
  pairs?: FieldPair[];
  runs: number;
  last_run: string | null;
}

/** One problem found in the two files: `side` is "a" or "b" when it belongs to one file. */
export interface FileProblem {
  code: string;
  side?: "a" | "b";
  field?: string;
  column?: string;
  detail?: string;
}

/** What the run screen gets for a template and two uploaded files. */
export interface TemplatePlan {
  errors: FileProblem[];
  warnings: FileProblem[];
  reread: { a: { sheet?: string; header_row?: number } | null; b: { sheet?: string; header_row?: number } | null };
  unexpected?: { a: string[]; b: string[] };
  mapping?: Mapping;
  check?: CheckResult | null;
  config?: ReconTemplate["config"];
  pairs?: FieldPair[];
}
