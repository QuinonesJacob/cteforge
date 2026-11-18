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

// ── Users / auth (domain/users.py, domain/access.py; ADR 0027) ─────────────────
/** Built-in role names; custom roles have other names (ADR 0027). */
export type BuiltinRole = "viewer" | "contributor" | "approver" | "admin";
export type UserRole = BuiltinRole | (string & {});
export type ActorKind = "human" | "agent";

/** The tool catalog (domain/access.py `Tool`): the permission unit. */
export type ToolName =
  | "search"
  | "get_definition"
  | "compile_and_run"
  | "check_and_run"
  | "describe_tables"
  | "list_joins"
  | "list_recipes"
  | "parse_query"
  | "propose_definition"
  | "revise_definition"
  | "propose_join"
  | "propose_recipe"
  | "list_proposals"
  | "review_proposal"
  | "verify"
  | "view_audit"
  | "list_users"
  | "manage_user"
  | "manage_role"
  | "manage_settings";
export type ToolGroup = "use" | "build" | "review" | "admin";
export type ToolMode = "follow_role" | "only";

/** GET /tools (services/cards.py ToolInfo). */
export interface ToolInfo {
  name: ToolName;
  group: ToolGroup;
  admin_only: boolean;
}

/**
 * The signed-in user: GET /me (MeCard). `tools` are the effective tools of the token in use;
 * `role_allowed` / `role_defaults` bound what a new token may get. They are optional so a
 * plain `{name, role, kind}` (tests, other users) falls back to the built-in role's tools.
 */
export interface User {
  name: string;
  role: UserRole;
  kind: ActorKind;
  active?: boolean;
  created_at?: string | null;
  tools?: ToolName[];
  review_all?: boolean;
  role_allowed?: ToolName[];
  role_defaults?: ToolName[];
}

export type TokenState = "active" | "revoked" | "expired";

/** A token's metadata (services/cards.py TokenCard). Never the token itself. */
export interface TokenCard {
  id: string;
  user_name: string;
  name: string;
  /** First 12 characters ("cfg_pat_…"); "" for a token migrated from before ADR 0027. */
  prefix: string;
  created_at: string;
  created_by: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  tool_mode: ToolMode;
  include: ToolName[];
  exclude: ToolName[];
  /** Effective tools now (empty unless active). */
  tools: ToolName[];
  state: TokenState;
}

/** POST /me/tokens, POST /users/{name}/tokens: `token` is shown once. */
export interface NewToken {
  token: string;
  info: TokenCard;
}

export interface CreateTokenRequest {
  name: string;
  expires_at?: string | null;
  tool_mode: ToolMode;
  include: ToolName[];
  exclude: ToolName[];
}

export interface TokenToolsRequest {
  tool_mode: ToolMode;
  include: ToolName[];
  exclude: ToolName[];
}

/** GET /users (services/cards.py UserView). */
export interface UserView {
  name: string;
  role: UserRole;
  kind: ActorKind;
  active: boolean;
  created_at?: string | null;
  tokens: TokenCard[];
}

/** GET /roles (services/cards.py RoleCard). */
export interface RoleCard {
  name: string;
  description: string;
  allowed: ToolName[];
  defaults: ToolName[];
  review_all: boolean;
  builtin: boolean;
  /** How many users hold it. */
  users: number;
}

export interface RoleInput {
  description?: string;
  allowed?: ToolName[];
  defaults?: ToolName[];
  review_all?: boolean;
}

/** POST /users: `token` (the "default" token) is shown once, and null for an agent caller. */
export interface CreatedUser {
  user: User;
  token: string | null;
}

export interface Profile {
  name: string;
  dialect: Dialect;
  /** The effective default connection: the profile used when none is named (ADR 0021). */
  default?: boolean;
}

export type TeamMode = "strict" | "open";
export interface Settings {
  mode: TeamMode;
  /** The admin's default connection (a profile name) as stored; null = not set. */
  default_connection?: string | null;
  /** The profile used when none is named: the setting → CTEFORGE_DEFAULT_PROFILE → the only one. */
  effective_default_connection?: string | null;
  /** e.g. `default_connection_missing`: the stored profile is no longer configured. */
  issues?: Issue[];
}

/** PUT /settings changes only the fields given. */
export interface SettingsUpdate {
  mode?: TeamMode;
  default_connection?: string | null;
}

// ── Definitions (domain/models.py, services/cards.py) ──────────────────────────
export type Dialect =
  | "duckdb"
  | "postgres"
  | "redshift"
  | "snowflake"
  | "bigquery"
  | "databricks"
  | "clickhouse";
export const DIALECTS: Dialect[] = [
  "duckdb",
  "postgres",
  "redshift",
  "snowflake",
  "bigquery",
  "databricks",
  "clickhouse",
];

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
  /** The verifier's kind (ADR 0027: an admin agent may verify). */
  verified_by_kind?: ActorKind | null;
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
  /** IDs it had before a rename (ADR 0024), oldest first. */
  former_ids?: string[];
}

/** How a search hit matched (ADR 0023): its words, the meaning of the query, or both. */
export type SearchMatch = "keyword" | "meaning" | "both";

export interface SearchHit {
  id: string;
  version: number;
  title: string;
  meaning: string;
  state: DefinitionState;
  score: number;
  /** null for an empty query (everything listed). */
  match?: SearchMatch | null;
  /** Set when meaning search could not run: results match by keyword only. */
  note?: string | null;
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
export type ProposalKind = "new_definition" | "revise_definition" | "new_recipe" | "new_join" | "rename_definition";
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

/** POST /proposals/definitions/{id}/rename (ADR 0024). */
export interface RenameDefinitionRequest {
  new_id: string;
  base_version?: number;
  summary: string;
}

// ── References (GET /definitions/{id}/references; ADR 0024) ───────────────────
export interface RecipeReference {
  recipe_id: string;
  recipe_version: number;
  title: string;
  state: DefinitionState;
  alias: string;
  /** null = floats on the latest usable version. */
  pinned_version?: number | null;
}

export interface ProposalReference {
  proposal_id: string;
  kind: ProposalKind;
  target_id: string;
  summary: string;
  author: string;
  created_at: string;
  relation: "target" | "uses";
}

export interface FormerId {
  former_id: string;
  definition_id: string;
  renamed_to: string;
  renamed_by: string;
  renamed_at: string;
  proposal_id?: string | null;
}

export interface DefinitionReferences {
  id: string;
  version: number;
  state: DefinitionState;
  recipes: RecipeReference[];
  proposals: ProposalReference[];
  former_ids: FormerId[];
  outside_warning: string;
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
  decided_by_kind?: ActorKind | null;
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
