import { useState, type FormEvent } from "react";
import { checkSql } from "../api/endpoints";
import type { RunOutcome } from "../api/types";
import { useToast } from "../state/ToastContext";
import { Field } from "./Field";
import { ProfileSelect } from "./ProfileSelect";
import { RunResult } from "./RunResult";

const LIMITS = [10, 100, 1000, 5000];

/** Exploratory SQL (POST /check): one read-only SELECT, labeled by the join map. */
export function ExploreSqlPanel() {
  const toast = useToast();
  const [sql, setSql] = useState("");
  const [profile, setProfile] = useState("");
  const [limit, setLimit] = useState(100);
  const [busy, setBusy] = useState<"run" | "check" | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);

  async function submit(run: boolean) {
    setBusy(run ? "run" : "check");
    try {
      setOutcome(await checkSql({ sql, run, limit, ...(profile ? { profile } : {}) }));
    } catch (err) {
      toast.error(err, run ? "Run failed" : "Check failed");
    } finally {
      setBusy(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void submit(true);
  }

  return (
    <div className="run-panel">
      <form className="card run-form" onSubmit={onSubmit} aria-label="Exploratory SQL">
        <p className="muted small">
          New logic, not a trusted definition: the result is labeled new_logic_on_known_joins (every join is in the join
          map) or exploratory. If a definition answers the question, run it instead. Strict mode refuses this.
        </p>
        <Field label="Exploratory SQL" htmlFor="xsql" required hint="One read-only SELECT.">
          <textarea
            id="xsql"
            className="mono sql-input"
            rows={10}
            spellCheck={false}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
          />
        </Field>
        <div className="run-options">
          <Field label="Profile" htmlFor="xsql-profile">
            <ProfileSelect id="xsql-profile" value={profile} onChange={setProfile} />
          </Field>
          <Field label="Row limit" htmlFor="xsql-limit">
            <select id="xsql-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <div className="btn-row run-buttons">
            <button type="submit" className="btn btn-primary" disabled={busy !== null || !sql.trim()}>
              {busy === "run" ? "Running…" : "Run"}
            </button>
            <button type="button" className="btn" disabled={busy !== null || !sql.trim()} onClick={() => void submit(false)}>
              {busy === "check" ? "Checking…" : "Check only"}
            </button>
          </div>
        </div>
      </form>
      {outcome && <RunResult outcome={outcome} />}
    </div>
  );
}
