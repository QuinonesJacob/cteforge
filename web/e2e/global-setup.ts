// Builds the GUI, creates a fresh `cteforge demo` in a temp dir, starts `cteforge serve` on a
// free port with auth on, and creates a second human approver for four-eyes tests. Shares the
// server URL and tokens with the specs through E2E_* environment variables (inherited by the
// workers). Returns the teardown, which stops the server and removes the temp dir.
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, openSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = resolve(WEB, "..");

function freePort(): Promise<number> {
  return new Promise((ok, fail) => {
    const srv = createServer();
    srv.once("error", fail);
    srv.listen(0, "127.0.0.1", () => {
      const address = srv.address();
      const port = typeof address === "object" && address ? address.port : 0;
      srv.close(() => ok(port));
    });
  });
}

/** `export NAME=value` lines of the demo's env.sh (no secrets in it). */
function readEnvSh(path: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = /^export (\w+)=(.*)$/.exec(line.trim());
    if (!m || !m[1] || m[2] === undefined) continue;
    const value = m[2];
    env[m[1]] = value.startsWith("'") && value.endsWith("'") ? value.slice(1, -1) : value;
  }
  return env;
}

function token(stdout: string, who: "admin" | "agent"): string {
  const m = new RegExp(`^${who} token \\([^)]*\\):\\s+(\\S+)$`, "m").exec(stdout);
  if (!m?.[1]) throw new Error(`no ${who} token in cteforge demo output:\n${stdout}`);
  return m[1];
}

async function waitForServer(base: string, proc: ChildProcess, logPath: string): Promise<void> {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) throw new Error(`cteforge serve exited early:\n${readFileSync(logPath, "utf8")}`);
    try {
      // Up when the API answers; no token → 401 (auth is on).
      const res = await fetch(`${base}/api/v1/me`);
      if (res.status === 401) return;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`cteforge serve not healthy:\n${readFileSync(logPath, "utf8")}`);
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  execFileSync("npm", ["run", "build"], { cwd: WEB, stdio: "inherit" });

  const tmp = mkdtempSync(join(tmpdir(), "cteforge-e2e-"));
  const demoDir = join(tmp, "demo");
  const baseEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith("CTEFORGE_")));
  const stdout = execFileSync("uv", ["run", "--directory", ROOT, "cteforge", "demo", demoDir], {
    cwd: tmp,
    env: baseEnv,
    encoding: "utf8",
    timeout: 300_000,
  });
  const adminToken = token(stdout, "admin");
  const agentToken = token(stdout, "agent");
  const env = { ...baseEnv, ...readEnvSh(join(demoDir, "env.sh")), CTEFORGE_AUTH: "on" };

  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const logPath = join(tmp, "serve.log");
  const log = openSync(logPath, "w");
  const proc = spawn("uv", ["run", "--directory", ROOT, "cteforge", "serve", "--port", String(port)], {
    cwd: demoDir,
    env,
    stdio: ["ignore", log, log],
  });

  async function teardown(): Promise<void> {
    if (proc.exitCode === null) {
      const exited = new Promise((r) => proc.once("exit", r));
      proc.kill("SIGINT"); // uvicorn's graceful shutdown
      const timer = setTimeout(() => proc.kill("SIGKILL"), 20_000);
      await exited;
      clearTimeout(timer);
    }
    if (process.env.E2E_KEEP_TMP) console.log(`e2e: kept ${tmp}`);
    else rmSync(tmp, { recursive: true, force: true });
  }

  try {
    await waitForServer(base, proc, logPath);
    // A second human approver (not admin), for the four-eyes review.
    const created = await fetch(`${base}/api/v1/users`, {
      method: "POST",
      headers: { Authorization: `Bearer ${adminToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name: "ben", role: "approver", kind: "human" }),
    });
    if (created.status !== 201) throw new Error(`POST /users: ${created.status} ${await created.text()}`);
    const body = (await created.json()) as { token: string };

    process.env.E2E_BASE_URL = base;
    process.env.E2E_ADMIN_TOKEN = adminToken;
    process.env.E2E_AGENT_TOKEN = agentToken;
    process.env.E2E_APPROVER_TOKEN = body.token;
    process.env.E2E_SERVE_LOG = logPath;
  } catch (err) {
    await teardown();
    throw err;
  }
  return teardown;
}
