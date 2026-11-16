// One function per route (src/cteforge/api/CLAUDE.md). All under /api/v1.
import { request } from "./client";
import type {
  AuditEvent,
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
  RunOutcome,
  RunRequest,
  SearchHit,
  Settings,
  TeamMode,
  User,
  UserRole,
  ActorKind,
} from "./types";

type Opts = { signal?: AbortSignal };
const enc = encodeURIComponent;

// ── Identity ──
export const getMe = (opts: Opts & { anonymous?: boolean; token?: string } = {}) =>
  request<User>("GET", "/me", opts);
export const listProfiles = (opts: Opts = {}) => request<Profile[]>("GET", "/profiles", opts);

// ── Definitions ──
export const searchDefinitions = (q: string, includeDrafts: boolean, opts: Opts = {}) =>
  request<SearchHit[]>("GET", "/definitions", { ...opts, query: { q, include_drafts: includeDrafts, limit: 100 } });
export const getDefinition = (id: string, version?: number, opts: Opts = {}) =>
  request<DefinitionCard>("GET", `/definitions/${enc(id)}`, { ...opts, query: { version } });
export const getDefinitionHistory = (id: string, opts: Opts = {}) =>
  request<DefinitionVersionInfo[]>("GET", `/definitions/${enc(id)}/history`, opts);
export const verifyDefinition = (id: string, version: number) =>
  request<DefinitionCard>("POST", `/definitions/${enc(id)}/verify`, { body: { version } });

// ── Build ──
export const parseQuery = (body: { sql: string; dialect: Dialect; profile?: string }) =>
  request<ParseReport>("POST", "/parse", { body });
export const proposeDefinition = (body: ProposeDefinitionRequest) =>
  request<ProposalOutcome>("POST", "/proposals/definitions", { body });

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

// ── Admin ──
export const listUsers = (opts: Opts = {}) => request<User[]>("GET", "/users", opts);

/** Normalizes {user, token}, flat {name, role, kind, token} and bare "token" responses. */
export function toCreatedUser(body: unknown, name = ""): CreatedUser {
  if (typeof body === "string") return { token: body, user: { name, role: "viewer", kind: "human" } };
  const b = (body ?? {}) as Record<string, unknown>;
  const token = typeof b.token === "string" ? b.token : "";
  const user = (typeof b.user === "object" && b.user !== null ? b.user : b) as Partial<User>;
  return {
    token,
    user: { name: user.name ?? name, role: user.role ?? "viewer", kind: user.kind ?? "human", created_at: user.created_at },
  };
}

export const createUser = async (body: { name: string; role: UserRole; kind: ActorKind }) =>
  toCreatedUser(await request<unknown>("POST", "/users", { body }));
export const rotateToken = async (name: string) =>
  toCreatedUser(await request<unknown>("POST", `/users/${enc(name)}/token`), name);
export const setUserRole = (name: string, role: UserRole) =>
  request<User>("PUT", `/users/${enc(name)}/role`, { body: { role } });
export const getSettings = (opts: Opts = {}) => request<Settings>("GET", "/settings", opts);
export const putSettings = (mode: TeamMode) => request<Settings>("PUT", "/settings", { body: { mode } });
