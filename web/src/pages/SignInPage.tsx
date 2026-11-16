import { useState, type FormEvent } from "react";
import { errorMessage } from "../api/client";
import { Field } from "../components/Field";
import { ThemeToggle } from "../components/ThemeToggle";
import { useAuth } from "../state/AuthContext";

export function SignInPage() {
  const { signIn, recheck } = useAuth();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(token.trim());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="signin" id="main">
      <div className="signin-top">
        <ThemeToggle />
      </div>
      <form className="card signin-card" onSubmit={submit} aria-labelledby="signin-title">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden="true">
            c
          </span>
          cteforge
        </div>
        <h1 id="signin-title">Sign in</h1>
        <p className="muted">
          Paste the API token an admin created for you (<code>cteforge users create</code> or Settings). It stays in this
          browser tab only.
        </p>
        <Field label="API token" htmlFor="token" error={error}>
          <input
            id="token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoFocus
          />
        </Field>
        <div className="btn-row">
          <button type="submit" className="btn btn-primary" disabled={busy || !token.trim()}>
            {busy ? "Checking…" : "Sign in"}
          </button>
          <button type="button" className="btn" onClick={() => void recheck()}>
            Retry without token
          </button>
        </div>
        <p className="muted small mt">
          Running locally with <code>CTEFORGE_AUTH=off</code>? You'll be signed in automatically as the local admin.
        </p>
      </form>
    </main>
  );
}
