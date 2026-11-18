// One function per route (src/cteforge/api/CLAUDE.md). All under /api/v1.
import { request } from "./client";
import type {
  AuditEvent,
  JoinCard,
  LibraryFilter,
  LibraryPage,
  ProposeJoinRequest,
  ReviseDefinitionRequest,
  AuditPage,
  CreatedUser,
  DefinitionCard,
  DefinitionVersionInfo,
  Dialect,
  ParseReport,
  Profile,
  ProposalOutcome,
  ProposalStatus,
  ProposalView,
  ProposeDefinitionRequest,
  DefinitionReferences,
  RenameDefinitionRequest,
  RunOutcome,
  RunRequest,
  Settings,
  SettingsUpdate,
  User,
  UserRole,
  ActorKind,
  CreateTokenRequest,
  NewToken,
  RoleCard,
  RoleInput,
  TokenCard,
  TokenToolsRequest,
  ToolInfo,
  UserView,
} from "./types";

type Opts = { signal?: AbortSignal };
const enc = encodeURIComponent;

// ── Identity ──
export const getMe = (opts: Opts & { anonymous?: boolean; token?: string } = {}) =>
  request<User>("GET", "/me", opts);
export const listProfiles = (opts: Opts = {}) => request<Profile[]>("GET", "/profiles", opts);

// ── Library (ADR 0026): definitions + the proposals touching them, filtered server-side ──
export const listLibrary = (filter: LibraryFilter, opts: Opts = {}) =>
  request<LibraryPage>("GET", "/library", {
    ...opts,
    query: {
      q: filter.q,
      trust: filter.trust,
      approval: filter.approval,
      created_by: filter.created_by,
      verified_by: filter.verified_by,
      needs_my_review: filter.needs_my_review || undefined,
    },
  });

// ── Definitions ──
export const getDefinition = (id: string, version?: number, opts: Opts & { includeSource?: boolean } = {}) =>
  request<DefinitionCard>("GET", `/definitions/${enc(id)}`, {
    signal: opts.signal,
    query: { version, include_source: opts.includeSource || undefined },
  });
export const getDefinitionHistory = (id: string, opts: Opts = {}) =>
  request<DefinitionVersionInfo[]>("GET", `/definitions/${enc(id)}/history`, opts);
/** Everything inside cteforge that uses the ID (ADR 0024): shown before a rename. */
export const getDefinitionReferences = (id: string, opts: Opts = {}) =>
  request<DefinitionReferences>("GET", `/definitions/${enc(id)}/references`, opts);
export const verifyDefinition = (id: string, version: number) =>
  request<DefinitionCard>("POST", `/definitions/${enc(id)}/verify`, { body: { version } });

// ── Build ──
/** `dialect` omitted → the connection's: the profile's, else the default connection's. */
export const parseQuery = (body: { sql: string; dialect?: Dialect; profile?: string }) =>
  request<ParseReport>("POST", "/parse", { body });
export const proposeDefinition = (body: ProposeDefinitionRequest) =>
  request<ProposalOutcome>("POST", "/proposals/definitions", { body });
export const reviseDefinition = (id: string, body: ReviseDefinitionRequest) =>
  request<ProposalOutcome>("POST", `/proposals/definitions/${enc(id)}/revise`, { body });
export const renameDefinition = (id: string, body: RenameDefinitionRequest) =>
  request<ProposalOutcome>("POST", `/proposals/definitions/${enc(id)}/rename`, { body });
/** Join map changes (ADR 0026): annotate an existing edge, or add one no definition contains. */
export const proposeJoin = (body: ProposeJoinRequest) =>
  request<ProposalOutcome>("POST", "/proposals/joins", { body });

// ── Join map (DESIGN §3, ADR 0026) ──
export const listJoins = (filter: { table?: string; known_only?: boolean; definition?: string }, opts: Opts = {}) =>
  request<JoinCard[]>("GET", "/joins", {
    ...opts,
    query: { table: filter.table, known_only: filter.known_only || undefined, definition: filter.definition },
  });

// ── Run ──
export const runQuery = (body: RunRequest) => request<RunOutcome>("POST", "/run", { body });

// ── Proposals ──
export const listProposals = (status: ProposalStatus, opts: Opts = {}) =>
  request<ProposalView[]>("GET", "/proposals", { ...opts, query: { status } });
/** Cheap count for the nav badge: no diffs built, no audit event (GET /proposals does both). */
export const countProposals = (status: ProposalStatus, opts: Opts = {}) =>
  request<{ count: number }>("GET", "/proposals/count", { ...opts, query: { status } });
export const getProposal = (id: string, opts: Opts = {}) =>
  request<ProposalView>("GET", `/proposals/${enc(id)}`, opts);
export const approveProposal = (id: string, body: { verify: boolean; comment: string }) =>
  request<ProposalOutcome>("POST", `/proposals/${enc(id)}/approve`, { body });
export const rejectProposal = (id: string, comment: string) =>
  request<ProposalView>("POST", `/proposals/${enc(id)}/reject`, { body: { comment } });
export const withdrawProposal = (id: string) =>
  request<ProposalView>("POST", `/proposals/${enc(id)}/withdraw`);

// ── Audit ──
export interface AuditFilter {
  actor?: string;
  kind?: string;
  definition_id?: string;
  limit?: number;
}
/** Accepts the AuditPage wrapper (services/cards.py) or a bare list. */
export function toAuditPage(body: unknown, limit: number): AuditPage {
  if (Array.isArray(body)) return { events: body as AuditEvent[], limit, has_more: body.length >= limit && limit > 0 };
  const b = (body ?? {}) as Partial<AuditPage>;
  return { events: b.events ?? [], limit: b.limit ?? limit, has_more: Boolean(b.has_more) };
}
export const listAudit = async (filter: AuditFilter, opts: Opts = {}) =>
  toAuditPage(await request<unknown>("GET", "/audit", { ...opts, query: { ...filter } }), filter.limit ?? 100);

// ── Admin: users, roles, tokens (ADR 0027) ──
export const listTools = (opts: Opts = {}) => request<ToolInfo[]>("GET", "/tools", opts);
export const listUsers = (opts: Opts = {}) => request<UserView[]>("GET", "/users", opts);

/** Normalizes {user, token} (token null for an agent caller), flat {name, role, kind, token}
 * and bare "token" responses. */
export function toCreatedUser(body: unknown, name = ""): CreatedUser {
  if (typeof body === "string") return { token: body, user: { name, role: "viewer", kind: "human" } };
  const b = (body ?? {}) as Record<string, unknown>;
  const token = typeof b.token === "string" && b.token ? b.token : null;
  const user = (typeof b.user === "object" && b.user !== null ? b.user : b) as Partial<User>;
  return {
    token,
    user: { name: user.name ?? name, role: user.role ?? "viewer", kind: user.kind ?? "human", created_at: user.created_at },
  };
}

export const createUser = async (body: { name: string; role: UserRole; kind: ActorKind }) =>
  toCreatedUser(await request<unknown>("POST", "/users", { body }));
/** Change a user's role and/or active flag (only the fields given). */
export const updateUser = (name: string, body: { role?: UserRole; active?: boolean }) =>
  request<UserView>("PATCH", `/users/${enc(name)}`, { body });

export const listRoles = (opts: Opts = {}) => request<RoleCard[]>("GET", "/roles", opts);
export const createRole = (body: RoleInput & { name: string }) => request<RoleCard>("POST", "/roles", { body });
export const updateRole = (name: string, body: RoleInput) => request<RoleCard>("PUT", `/roles/${enc(name)}`, { body });
export const deleteRole = (name: string) => request<null>("DELETE", `/roles/${enc(name)}`);

/** Tokens: `user` undefined = the signed-in user's (/me/tokens), else /users/{user}/tokens. */
const tokensPath = (user?: string) => (user ? `/users/${enc(user)}/tokens` : "/me/tokens");
export const listTokens = (user?: string, opts: Opts = {}) => request<TokenCard[]>("GET", tokensPath(user), opts);
/** The new token is in this response only. */
export const createToken = (body: CreateTokenRequest, user?: string) =>
  request<NewToken>("POST", tokensPath(user), { body });
export const setTokenTools = (id: string, body: TokenToolsRequest, user?: string) =>
  request<TokenCard>("PUT", `${tokensPath(user)}/${enc(id)}`, { body });
export const revokeToken = (id: string, user?: string) =>
  request<TokenCard>("DELETE", `${tokensPath(user)}/${enc(id)}`);
export const getSettings = (opts: Opts = {}) => request<Settings>("GET", "/settings", opts);
export const putSettings = (body: SettingsUpdate) => request<Settings>("PUT", "/settings", { body });
