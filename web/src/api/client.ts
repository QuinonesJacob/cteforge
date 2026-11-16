// Fetch wrapper: base path, bearer token, JSON, and error parsing that mirrors
// {"error": {type, message, issues}} (src/cteforge/api/CLAUDE.md).
import { getToken } from "./token";
import type { Issue } from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly issues: Issue[];

  constructor(status: number, type: string, message: string, issues: Issue[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.type = type;
    this.issues = issues;
  }
}

type QueryValue = string | number | boolean | null | undefined;
/** An array repeats the key (`?name=a&name=b`); empty values are dropped. */
type Query = Record<string, QueryValue | QueryValue[]>;

export interface RequestOptions {
  query?: Query;
  body?: unknown;
  signal?: AbortSignal;
  /** Send without a token even if one is stored (used to detect auth off). */
  anonymous?: boolean;
  /** Use this token instead of the stored one (sign-in check). */
  token?: string;
}

let unauthorizedHandler: (() => void) | null = null;

/** Called on any 401 so the app can drop the session and show sign-in. */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, raw] of Object.entries(query ?? {})) {
    for (const value of Array.isArray(raw) ? raw : [raw]) {
      if (value === undefined || value === null || value === "") continue;
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

function isIssueList(value: unknown): value is Issue[] {
  return Array.isArray(value) && value.every((i) => typeof i === "object" && i !== null && "code" in i);
}

/** Turn any error response body into an ApiError. Handles FastAPI's default {detail}. */
export function parseErrorBody(status: number, body: unknown, statusText = ""): ApiError {
  if (typeof body === "object" && body !== null) {
    const err = (body as { error?: unknown }).error;
    if (typeof err === "object" && err !== null) {
      const e = err as { type?: unknown; message?: unknown; issues?: unknown };
      return new ApiError(
        status,
        typeof e.type === "string" ? e.type : "Error",
        typeof e.message === "string" ? e.message : statusText || `HTTP ${status}`,
        isIssueList(e.issues) ? e.issues : [],
      );
    }
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return new ApiError(status, "HTTPError", detail);
    if (Array.isArray(detail)) {
      // Request validation (422) from FastAPI: [{loc, msg, type}]
      const msg = detail
        .map((d: { loc?: unknown[]; msg?: string }) =>
          [Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "", d.msg ?? ""].filter(Boolean).join(": "),
        )
        .join("; ");
      return new ApiError(status, "RequestValidationError", msg || "Invalid request");
    }
  }
  if (typeof body === "string" && body.trim()) return new ApiError(status, "HTTPError", body.trim().slice(0, 300));
  return new ApiError(status, "HTTPError", statusText || `HTTP ${status}`);
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = opts.anonymous ? null : (opts.token ?? getToken());
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: string | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), { method, headers, body, signal: opts.signal });
  } catch (exc) {
    if (exc instanceof DOMException && exc.name === "AbortError") throw exc;
    throw new ApiError(0, "NetworkError", "Could not reach the cteforge server. Is it running?");
  }

  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    const error = parseErrorBody(res.status, parsed, res.statusText);
    if (res.status === 401 && !opts.anonymous && !opts.token) unauthorizedHandler?.();
    throw error;
  }
  return parsed as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}
