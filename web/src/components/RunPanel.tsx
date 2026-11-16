import { useState, type FormEvent } from "react";
import { runQuery } from "../api/endpoints";
import type { DefinitionCard, RunOutcome } from "../api/types";
import { buildRunRequest, buildRunValues, initialFormValue, type FormValue } from "../lib/variableForm";
import { useToast } from "../state/ToastContext";
import { Field } from "./Field";
import { ProfileSelect } from "./ProfileSelect";
import { RunResult } from "./RunResult";
import { RunVariableField } from "./RunVariableField";

const LIMITS = [10, 100, 1000, 5000];

/** Run form generated from the definition's variables → POST /run. Mount with a key per version. */
export function RunPanel({ def }: { def: DefinitionCard }) {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, FormValue>>(() =>
    Object.fromEntries(def.variables.map((v) => [v.name, initialFormValue(v)])),
  );
  const [overrides, setOverrides] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [profile, setProfile] = useState("");
  const [limit, setLimit] = useState(100);
  const [busy, setBusy] = useState<"run" | "compile" | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);

  const changeable = def.variables.filter((v) => v.role === "changeable");
  const fixed = def.variables.filter((v) => v.role !== "changeable");

  async function submit(run: boolean) {
    const built = buildRunValues(def.variables, values, overrides);
    setErrors(built.errors);
    if (Object.keys(built.errors).length) {
      toast.push({ kind: "warning", title: "Check the highlighted values" });
      return;
    }
    setBusy(run ? "run" : "compile");
    try {
      setOutcome(await runQuery(buildRunRequest(def, built.values, { run, limit, profile: profile || undefined })));
    } catch (err) {
      toast.error(err, run ? "Run failed" : "Compile failed");
    } finally {
      setBusy(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void submit(true);
  }

  function setOverride(name: string, on: boolean) {
    setOverrides((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  const field = (v: DefinitionCard["variables"][number]) => (
    <RunVariableField
      key={v.name}
      card={v}
      value={values[v.name] ?? initialFormValue(v)}
      onChange={(fv) => setValues((s) => ({ ...s, [v.name]: fv }))}
      overridden={overrides.has(v.name)}
      onOverride={(on) => setOverride(v.name, on)}
      error={errors[v.name]}
    />
  );

  return (
    <div className="run-panel">
      <form className="card run-form" onSubmit={onSubmit} aria-label="Run this definition">
        {changeable.length > 0 && (
          <fieldset>
            <legend>Changeable variables</legend>
            <div className="run-vars">{changeable.map(field)}</div>
          </fieldset>
        )}
        {fixed.length > 0 && (
          <fieldset>
            <legend>Fixed variables</legend>
            <p className="muted small">Part of the trusted logic. Override only when you mean to change it.</p>
            <div className="run-vars">{fixed.map(field)}</div>
          </fieldset>
        )}
        {def.variables.length === 0 && <p className="muted">No variables: this runs exactly as written.</p>}
        <div className="run-options">
          <Field label="Profile" htmlFor="run-profile">
            <ProfileSelect
              id="run-profile"
              value={profile}
              onChange={setProfile}
              defaultLabel={def.profile ? `Definition default (${def.profile})` : "Default profile"}
            />
          </Field>
          <Field label="Row limit" htmlFor="run-limit">
            <select id="run-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <div className="btn-row run-buttons">
            <button type="submit" className="btn btn-primary" disabled={busy !== null}>
              {busy === "run" ? "Running…" : "Run"}
            </button>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => void submit(false)}>
              {busy === "compile" ? "Compiling…" : "Compile only"}
            </button>
          </div>
        </div>
      </form>
      {outcome && <RunResult outcome={outcome} />}
    </div>
  );
}
