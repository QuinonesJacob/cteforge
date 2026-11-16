// Route-based fetch mock for DOM tests.
import { vi } from "vitest";

type Handler = (body: unknown, url: URL) => { status?: number; body: unknown };

export function mockApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  const fn = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, "http://localhost");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const path = url.pathname.replace(/^\/api\/v1/, "");
    calls.push({ method, path, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response(JSON.stringify({ error: { type: "NotFound", message: `no mock for ${method} ${path}` } }), { status: 404 });
    const res = handler(body, url);
    return new Response(JSON.stringify(res.body), { status: res.status ?? 200 });
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls };
}
