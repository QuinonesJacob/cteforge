import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, buildUrl, onUnauthorized, parseErrorBody, request } from "./client";
import { createToken, createUser, listLibrary, revokeToken, toAuditPage, toCreatedUser } from "./endpoints";
import { clearToken, getToken, setToken } from "./token";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async (_url: string, _init?: RequestInit) =>
    new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

function headersOf(fn: ReturnType<typeof mockFetch>, call = 0): Record<string, string> {
  return (fn.mock.calls[call]?.[1]?.headers ?? {}) as Record<string, string>;
}

beforeEach(() => clearToken());
afterEach(() => {
  vi.unstubAllGlobals();
  onUnauthorized(null);
});

describe("auth header", () => {
  it("adds Authorization: Bearer <token> from sessionStorage", async () => {
    setToken("tok-123");
    const fetchFn = mockFetch(200, { name: "jq", role: "admin", kind: "human" });
    await request("GET", "/me");
    expect(headersOf(fetchFn).Authorization).toBe("Bearer tok-123");
    expect(fetchFn.mock.calls[0]?.[0]).toBe("/api/v1/me");
  });

  it("sends no Authorization without a token or when anonymous", async () => {
    const fetchFn = mockFetch(200, {});
    await request("GET", "/me");
    expect(headersOf(fetchFn).Authorization).toBeUndefined();
    setToken("tok");
    await request("GET", "/me", { anonymous: true });
    expect(headersOf(fetchFn, 1).Authorization).toBeUndefined();
  });

  it("uses an explicit token over the stored one", async () => {
    setToken("stored");
    const fetchFn = mockFetch(200, {});
    await request("GET", "/me", { token: "explicit" });
    expect(headersOf(fetchFn).Authorization).toBe("Bearer explicit");
  });

  it("keeps the token in sessionStorage only", () => {
    setToken("abc");
    expect(sessionStorage.getItem("cteforge.token")).toBe("abc");
    expect(localStorage.getItem("cteforge.token")).toBeNull();
    expect(getToken()).toBe("abc");
  });
});

describe("JSON bodies and query strings", () => {
  it("serializes the body and sets Content-Type", async () => {
    const fetchFn = mockFetch(200, { ok: true });
    await request("POST", "/parse", { body: { sql: "select 1", dialect: "duckdb" } });
    const init = fetchFn.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe('{"sql":"select 1","dialect":"duckdb"}');
    expect(headersOf(fetchFn)["Content-Type"]).toBe("application/json");
  });

  it("builds query strings and skips empty values", () => {
    expect(buildUrl("/audit", { actor: "jq", kind: "", limit: 50, definition_id: undefined })).toBe(
      "/api/v1/audit?actor=jq&limit=50",
    );
  });

  it("repeats array query values and drops empty ones", () => {
    expect(buildUrl("/tables", { name: ["deals", "", "accounts"], profile: "" })).toBe(
      "/api/v1/tables?name=deals&name=accounts",
    );
  });

  it("library repeats list filters and drops empty ones", async () => {
    const fetchFn = mockFetch(200, { rows: [], total: 0, pending_count: 0, proposals_visible: true });
    await listLibrary({ q: "revenue by month", trust: ["verified", "draft"], created_by: ["agent"], needs_my_review: true });
    expect(fetchFn.mock.calls[0]?.[0]).toBe(
      "/api/v1/library?q=revenue+by+month&trust=verified&trust=draft&created_by=agent&needs_my_review=true",
    );
    await listLibrary({});
    expect(fetchFn.mock.calls[1]?.[0]).toBe("/api/v1/library");
  });
});

describe("error parsing", () => {
  it("mirrors {error: {type, message, issues}}", async () => {
    mockFetch(422, {
      error: {
        type: "VariableValueError",
        message: "Invalid value for 'period'",
        issues: [{ code: "value_not_allowed", severity: "warning", message: "m", variable: "period" }],
      },
    });
    const err = await request("POST", "/run", { body: {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const api = err as ApiError;
    expect(api.status).toBe(422);
    expect(api.type).toBe("VariableValueError");
    expect(api.message).toBe("Invalid value for 'period'");
    expect(api.issues).toHaveLength(1);
    expect(api.issues[0]?.variable).toBe("period");
  });

  it("handles FastAPI {detail} bodies", () => {
    expect(parseErrorBody(404, { detail: "Not Found" }).message).toBe("Not Found");
    const v = parseErrorBody(422, { detail: [{ loc: ["body", "sql"], msg: "Field required" }] });
    expect(v.type).toBe("RequestValidationError");
    expect(v.message).toBe("sql: Field required");
  });

  it("handles non-JSON and empty bodies", () => {
    expect(parseErrorBody(502, "Bad gateway").message).toBe("Bad gateway");
    expect(parseErrorBody(500, undefined, "Internal Server Error").message).toBe("Internal Server Error");
    expect(parseErrorBody(500, { error: { type: "X" } }).issues).toEqual([]);
  });

  it("calls the unauthorized handler on 401", async () => {
    setToken("expired");
    const handler = vi.fn();
    onUnauthorized(handler);
    mockFetch(401, { error: { type: "Unauthorized", message: "invalid token", issues: [] } });
    await expect(request("GET", "/definitions")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledOnce();
  });

  it("does not call the unauthorized handler for anonymous probes", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    mockFetch(401, { error: { type: "Unauthorized", message: "token required" } });
    await expect(request("GET", "/me", { anonymous: true })).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });

  it("wraps network failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(request("GET", "/me")).rejects.toMatchObject({ status: 0, type: "NetworkError" });
  });
});

describe("user creation responses", () => {
  it("accepts {user, token} and flat shapes", async () => {
    expect(toCreatedUser({ user: { name: "a", role: "viewer", kind: "agent" }, token: "t1" })).toEqual({
      token: "t1",
      user: { name: "a", role: "viewer", kind: "agent", created_at: undefined },
    });
    expect(toCreatedUser({ name: "b", role: "admin", kind: "human", token: "t2" }).user.name).toBe("b");
    const fetchFn = mockFetch(200, { user: { name: "bot", role: "contributor", kind: "agent" }, token: "secret" });
    const created = await createUser({ name: "bot", role: "contributor", kind: "agent" });
    expect(created.token).toBe("secret");
    expect(JSON.parse(String(fetchFn.mock.calls[0]?.[1]?.body))).toEqual({ name: "bot", role: "contributor", kind: "agent" });
  });
});

describe("response normalizers", () => {
  it("create user keeps a null token (agent caller) as null", () => {
    expect(toCreatedUser({ user: { name: "x", role: "viewer", kind: "human" }, token: null }).token).toBeNull();
  });

  it("tokens go to /me/tokens or /users/{name}/tokens", async () => {
    const fetchFn = mockFetch(201, { token: "cfg_pat_x", info: { id: "t1" } });
    const body = { name: "ci", tool_mode: "only" as const, include: ["search" as const], exclude: [] };
    expect((await createToken(body)).token).toBe("cfg_pat_x");
    expect(String(fetchFn.mock.calls[0]?.[0])).toBe("/api/v1/me/tokens");
    await createToken(body, "bob smith");
    expect(String(fetchFn.mock.calls[1]?.[0])).toBe("/api/v1/users/bob%20smith/tokens");
    expect(JSON.parse(String(fetchFn.mock.calls[1]?.[1]?.body))).toEqual(body);
    mockFetch(200, { id: "t1", state: "revoked" });
    expect((await revokeToken("t1")).state).toBe("revoked");
  });

  it("audit accepts the AuditPage wrapper or a list", () => {
    expect(toAuditPage({ events: [], limit: 50, has_more: true }, 100)).toEqual({ events: [], limit: 50, has_more: true });
    expect(toAuditPage([{ id: "1" }, { id: "2" }], 2).has_more).toBe(true);
    expect(toAuditPage(null, 10)).toEqual({ events: [], limit: 10, has_more: false });
  });
});
