// Types mirroring the HTTP API (src/cteforge/api/CLAUDE.md) and the response models in
// src/cteforge/services/cards.py and src/cteforge/domain/*. Keep field names identical.

export type Severity = "error" | "warning" | "info";

export interface Issue {
  code: string;
  severity: Severity;
  message: string;
  variable?: string | null;
  literal?: string | null;
}

export interface ApiErrorBody {
  error: { type: string; message: string; issues?: Issue[] };
}

// ── Users / auth (domain/users.py) ──────────────────────────────────────────────
export type UserRole = "viewer" | "contributor" | "approver" | "admin";
export type ActorKind = "human" | "agent";

export interface User {
  name: string;
  role: UserRole;
  kind: ActorKind;
  created_at?: string | null;
}

export interface CreatedUser {
  user: User;
  token: string;
}

export interface Profile {
  name: string;
  dialect: Dialect;
}

export type TeamMode = "strict" | "open";
export interface Settings {
  mode: TeamMode;
}

// ── Definitions (domain/models.py, services/cards.py) ──────────────────────────
export type Dialect = "duckdb" | "postgres" | "snowflake" | "bigquery" | "databricks";
export const DIALECTS: Dialect[] = ["duckdb", "postgres", "snowflake", "bigquery", "databricks"];

export type DefinitionState = "draft" | "llm_annotated" | "verified";
export type TrustLabel =
  | "verified"
  | "built_from_verified_parts"
  | "llm_annotated"
  | "new_logic_on_known_joins"
  | "exploratory";

export type VariableType =
  | "date_range"
  | "date"
  | "text_list"
  | "text"
  | "number"
  | "boolean"
  | "grain";
export type Role = "changeable" | "fixed";
export type RangeShape = "half_open" | "inclusive" | "exclusive" | "start_exclusive";

export interface VariableCard {
  name: string;
  type: VariableType;
  role: Role;
  input_format: string;
  description: string;
  allowed?: unknown[] | null;
  unit?: string | null;
  format?: string | null;
  column?: string | null;
  column_type?: string | null;
  shape?: RangeShape | null;
  original_value: unknown;
  /** How many places in the SQL this variable fills. */
  places?: number;
}

export interface DefinitionVersionInfo {
  id: string;
  version: number;
  state: DefinitionState;
  author: string;
  created_at: string;
  change_note: string;
  logic_changed: boolean;
  verified_by?: string | null;
  verified_at?: string | null;
}

export interface DefinitionCard {
  id: string;
  version: number;
  title: string;
  meaning: string;
  keywords: string[];
  use_when: string;
  dont_use_when: string;
  source_dialect: Dialect;
  profile?: string | null;
  state: DefinitionState;
  verified_by?: string | null;
  verified_at?: string | null;
  owner?: string | null;
  grain?: string | null;
  join_keys: string[];
  outputs: string[];
  tables: string[];
  variables: VariableCard[];
  template: string;
  /** Full history from GET /definitions/{id}; may be null elsewhere. */
  history?: DefinitionVersionInfo[] | null;
  /** Only with ?include_source=true (revise flow): the trusted SQL rebuilt from the template
   * with its original values, and the variables as annotations addressing that SQL. */
  source_sql?: string | null;
  annotations?: Annotation[] | null;
}

export interface SearchHit {
  id: string;
  version: number;
  title: string;
  meaning: string;
  state: DefinitionState;
  score: number;
  keywords: string[];
  variables: VariableCard[];
}

// ── Parsing (parsing/analyze.py) ───────────────────────────────────────────────
export type LiteralContext =
  | "negated"
  | "or_branch"
  | "case_condition"
  | "join_on"
  | "outer_join_on"
  | "having"
  | "subquery"
  | "value_wrapped";

export interface ColumnRef {
  table: string | null;
  column: string;
  type?: string | null;
  derived?: boolean;
}

export interface LiteralInfo {
  id: string;
  scope: string;
  value: string;
  is_string: boolean;
  form: "bare" | "typed";
  cast_type?: string | null;
  operator: string;
  column_sql: string | null;
  column: ColumnRef | null;
  wrapped: boolean;
  in_list?: string | null;
  context: LiteralContext[];
  wrapper_sql?: string | null;
  suggested_format?: string | null;
  compared_type?: string | null;
}

export interface RangeCandidate {
  scope: string;
  column_sql: string;
  start: string;
  end: string;
  shape: RangeShape;
}

export interface JoinInfo {
  scope: string;
  left_table: string;
  right_table: string;
  join_type: string;
  keys: [string, string][];
  on_sql: string;
  /** Base tables / key columns resolved through CTEs that pass the keys through unchanged. */
  left_base_table?: string | null;
  right_base_table?: string | null;
  base_keys?: [string, string][];
}

export interface CteInfo {
  name: string;
  reads: string[];
  outputs: string[];
}

/**
 * A date truncation a `grain` variable could change (M1.5; parsing/analyze.py GrainCandidate).
 * Its `id` ("<scope>.G<n>") goes in a grain annotation's `literals`, never a literal ID.
 */
export interface GrainCandidate {
  id: string;
  scope: string;
  /** Normalized, lower case: "month", "week", "hour", "week(monday)", "isoweek", or
   * "column <name>" when the unit is read from a column. */
  unit: string;
  /** The truncated expression as written, e.g. "cd.closed_at". */
  column_sql: string;
  /** The whole truncation, e.g. "DATE_TRUNC('MONTH', cd.closed_at)". */
  sql: string;
  column?: ColumnRef | null;
  column_type?: string | null;
  /** Output column it computes, when it is a projection's value. */
  output?: string | null;
  context: LiteralContext[];
}

export interface ParseReport {
  dialect: Dialect;
  tables: string[];
  outputs: string[];
  ctes: CteInfo[];
  literals: LiteralInfo[];
  ranges: RangeCandidate[];
  joins: JoinInfo[];
  grains?: GrainCandidate[] | null;
  schema_found: boolean;
  issues: Issue[];
}

// ── Run (services/run.py) ──────────────────────────────────────────────────────
export interface RunOutcome {
  sql: string;
  dialect: Dialect;
  label: TrustLabel;
  reasons: string[];
  usable: boolean;
  issues: Issue[];
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  ran: boolean;
  compile_id: string | null;
  definitions: string[];
  /** One entry per definition version used: state and who verified it, when (DESIGN §8). */
  used: UsedDefinition[];
  duration_ms: number | null;
}

export interface UsedDefinition {
  id: string;
  version: number;
  state: DefinitionState;
  verified_by?: string | null;
  verified_at?: string | null;
}

export interface DefinitionRef {
  id: string;
  version?: number | null;
}

export interface RunRequest {
  uses?: Record<string, DefinitionRef>;
  recipe_id?: string;
  values: Record<string, Record<string, unknown>>;
  select?: string;
  dialect?: Dialect;
  profile?: string;
  run: boolean;
  limit: number;
  /** The user's question, recorded in the audit log. */
  question?: string;
}

export interface CheckRequest {
  sql: string;
  profile?: string;
  run: boolean;
  limit: number;
  question?: string;
}

// ── Catalog (domain/models.py Recipe / JoinEdge, services/cards.py TableCard) ───
export interface Recipe {
  id: string;
  version: number;
  title: string;
  description: string;
  uses: Record<string, DefinitionRef>;
  values: Record<string, Record<string, unknown>>;
  select?: string | null;
  dialect: Dialect;
  state: DefinitionState;
}

/** POST /proposals/recipes `recipe` (services/propose_other.py RecipeInput): cteforge sets
 * version, state and schema_version. */
export interface RecipeInput {
  id: string;
  title: string;
  description: string;
  uses: Record<string, DefinitionRef>;
  values: Record<string, Record<string, unknown>>;
  select?: string;
  dialect: Dialect;
}

export interface JoinKey {
  left: string;
  right: string;
}

export interface JoinEdge {
  id: string;
  left_table: string;
  right_table: string;
  keys: JoinKey[];
  join_type: string;
  relationship?: string | null;
  usage_count: number;
  traps: string[];
  state: DefinitionState;
}

export interface ColumnCard {
  name: string;
  type: string;
}

export interface TableCard {
  name: string;
  found: boolean;
  columns: ColumnCard[];
  used_by: string[];
  joins: string[];
  units: Record<string, string>;
}

// ── Proposals (domain/proposals.py, services/review.py) ────────────────────────
export type ProposalKind = "new_definition" | "revise_definition" | "new_recipe" | "new_join";
export type ProposalStatus = "pending" | "approved" | "rejected" | "withdrawn";

export interface Annotation {
  name: string;
  type: VariableType;
  role: Role;
  literals: string[];
  column?: string | null;
  description: string;
  allowed?: unknown[] | null;
  format?: string | null;
  unit?: string | null;
}

export interface DefinitionMetadata {
  id: string;
  title: string;
  meaning: string;
  keywords: string[];
  use_when: string;
  dont_use_when: string;
  grain: string | null;
  join_keys: string[];
  owner: string | null;
}

export interface ProposeDefinitionRequest {
  sql: string;
  dialect: Dialect;
  metadata: DefinitionMetadata;
  annotations: Annotation[];
  profile?: string;
  summary: string;
}

/** POST /proposals/definitions/{id}/revise: only what changes (DESIGN §18). */
export interface ReviseDefinitionRequest {
  base_version: number;
  sql?: string;
  dialect?: Dialect;
  metadata?: Partial<DefinitionMetadata> & { profile?: string | null };
  annotations?: Annotation[];
  summary: string;
}

/** Impact preview: recipes using the target. Accepts a list or an object form. */
export type ProposalImpact =
  | string[]
  | { recipes?: string[]; count?: number; [key: string]: unknown }
  | null;

export interface ProposalView {
  id: string;
  kind: ProposalKind;
  target_id: string;
  base_version?: number | null;
  payload: Record<string, unknown>;
  checks: Issue[];
  summary: string;
  author: string;
  author_kind: ActorKind;
  created_at: string;
  status: ProposalStatus;
  auto_applied: boolean;
  decided_by?: string | null;
  decided_at?: string | null;
  comment: string;
  resulting_version?: number | null;
  /** Unified diff of the stored YAML, base → proposed ("" or /dev/null base when new). */
  diff?: string | null;
  /** Ids of recipes using the target definition. */
  impact?: ProposalImpact;
}

export interface ProposalOutcome {
  proposal: ProposalView;
  definition?: DefinitionCard | null;
  issues: Issue[];
  auto_applied: boolean;
}

// ── Audit (domain/audit.py) ────────────────────────────────────────────────────
export type AuditKind =
  | "search"
  | "get_definition"
  | "parse_query"
  | "compile_and_run"
  | "check_and_run"
  | "propose"
  | "review"
  | "revalidate"
  | "admin";
export const AUDIT_KINDS: AuditKind[] = [
  "search",
  "get_definition",
  "parse_query",
  "compile_and_run",
  "check_and_run",
  "propose",
  "review",
  "revalidate",
  "admin",
];

export interface AuditEvent {
  id: string;
  at: string;
  actor: string;
  actor_kind: ActorKind;
  kind: AuditKind;
  question?: string | null;
  definitions: string[];
  values: Record<string, unknown>;
  trust_label?: TrustLabel | null;
  compile_id?: string | null;
  sql_sha256?: string | null;
  row_count?: number | null;
  duration_ms?: number | null;
  outcome: "ok" | "error" | "rejected";
  error?: string | null;
  extra?: Record<string, unknown>;
}

/** GET /audit: one page of events, newest first. */
export interface AuditPage {
  events: AuditEvent[];
  limit: number;
  has_more: boolean;
}
