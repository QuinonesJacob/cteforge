import { useEffect, useRef, useState, type FormEvent } from "react";
import { getDefinition, runQuery, searchDefinitions } from "../api/endpoints";
import type { DefinitionRef, RunOutcome, VariableCard } from "../api/types";
import {
  buildComposeRequest,
  initialForm,
  initialFormValue,
  nextAlias,
  savedOverrides,
  type ComposePart,
  type FormValue,
} from "../lib/variableForm";
import { canProposeRecipe } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";
import { Field } from "./Field";
import { ProfileSelect } from "./ProfileSelect";
import { RunResult } from "./RunResult";
import { RunVariableField } from "./RunVariableField";
import { SaveRecipeForm } from "./SaveRecipeForm";

/** What to open the composer with (a recipe, or nothing). */
export interface ComposeSeed {
  parts: { alias: string; ref: DefinitionRef; saved?: Record<string, unknown> }[];
  select: string;
  title?: string;
  description?: string;
  recipeId?: string;
}

const LIMITS = [10, 100, 1000, 5000];

interface Part extends ComposePart {
  title: string;
  saved: Record<string, unknown>;
  loadError?: string;
}

/**
 * Combine definitions by alias (+ an optional final SELECT over the aliases) and run them
 * through POST /run (DESIGN §17). Values per alias come from the same run-form fields as a
 * single definition. Mount with a key per seed.
 */
export function ComposePanel({ seed }: { seed: ComposeSeed | null }) {
  const { user } = useAuth();
  const toast = useToast();
  const [parts, setParts] = useState<Part[]>(() =>
    (seed?.parts ?? []).map((p) => ({
      alias: p.alias,
      ref: p.ref,
      cards: null,
      form: {},
      overrides: new Set<string>(),
      title: p.ref.id,
      saved: p.saved ?? {},
    })),
  );
  const [select, setSelect] = useState(seed?.select ?? "");
  const [pick, setPick] = useState("");
  const [profile, setProfile] = useState("");
  const [limit, setLimit] = useState(100);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"run" | "compile" | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);
  const requested = useRef(new Set<string>());
  const library = useAsync((signal) => searchDefinitions("", false, { signal }), []);

  // Load each definition's card (its variables) once per alias.
  useEffect(() => {
    for (const part of parts) {
      if (part.cards !== null || requested.current.has(part.alias)) continue;
      requested.current.add(part.alias);
      getDefinition(part.ref.id, part.ref.version ?? undefined).then(
        (card) =>
          update(part.alias, (p) => ({
            ...p,
            title: card.title,
            cards: card.variables,
            form: initialForm(card.variables, p.saved),
            overrides: savedOverrides(card.variables, p.saved),
          })),
        (err: unknown) =>
          update(part.alias, (p) => ({ ...p, cards: [], loadError: err instanceof Error ? err.message : String(err) })),
      );
    }
  }, [parts]);

  function update(alias: string, fn: (p: Part) => Part) {
    setParts((ps) => ps.map((p) => (p.alias === alias ? fn(p) : p)));
  }

  function add() {
    if (!pick) return;
    const alias = nextAlias(pick, parts.map((p) => p.alias));
    setParts((ps) => [...ps, { alias, ref: { id: pick }, cards: null, form: {}, overrides: new Set(), title: pick, saved: {} }]);
    setPick("");
  }

  function remove(alias: string) {
    requested.current.delete(alias);
    setParts((ps) => ps.filter((p) => p.alias !== alias));
  }

  function setOverride(alias: string, name: string, on: boolean) {
    update(alias, (p) => {
      const next = new Set(p.overrides);
      if (on) next.add(name);
      else next.delete(name);
      return { ...p, overrides: next };
    });
  }

  function build(run: boolean) {
    return buildComposeRequest(parts, select, { run, limit, profile: profile || undefined });
  }

  async function submit(run: boolean) {
    const { request, errors: errs } = build(run);
    setErrors(errs);
    if (Object.keys(errs).length) {
      toast.push({ kind: "warning", title: "Check the highlighted values" });
      return;
    }
    setBusy(run ? "run" : "compile");
    try {
      setOutcome(await runQuery(request));
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

  const loading = parts.some((p) => p.cards === null);
  const field = (part: Part, v: VariableCard) => (
    <RunVariableField
      key={v.name}
      idPrefix={`cmp-${part.alias}`}
      card={v}
      value={(part.form[v.name] as FormValue | undefined) ?? initialFormValue(v)}
      onChange={(fv) => update(part.alias, (p) => ({ ...p, form: { ...p.form, [v.name]: fv } }))}
      overridden={part.overrides.has(v.name)}
      onOverride={(on) => setOverride(part.alias, v.name, on)}
      error={errors[`${part.alias}.${v.name}`]}
    />
  );

  return (
    <div className="run-panel">
      <form className="card run-form" onSubmit={onSubmit} aria-label="Compose definitions">
        {seed?.recipeId && (
          <p className="callout callout-info small" role="status">
            Opened from recipe <code>{seed.recipeId}</code>. Changing values here does not change the recipe; save a new
            version below to keep them.
          </p>
        )}
        <div className="grid-2">
          <Field label="Add a definition" htmlFor="cmp-pick" hint="Each gets an alias the final SELECT can read from.">
            <select id="cmp-pick" value={pick} onChange={(e) => setPick(e.target.value)} disabled={library.loading}>
              <option value="">{library.loading ? "Loading library…" : "Choose a definition"}</option>
              {(library.data ?? []).map((h) => (
                <option key={h.id} value={h.id}>
                  {h.title} ({h.id})
                </option>
              ))}
            </select>
          </Field>
          <div className="btn-row run-buttons">
            <button type="button" className="btn" disabled={!pick} onClick={add}>
              Add
            </button>
          </div>
        </div>
        {parts.length === 0 && <p className="muted">Add one or more definitions to combine.</p>}
        {parts.map((part) => {
          const changeable = (part.cards ?? []).filter((v) => v.role === "changeable");
          const fixed = (part.cards ?? []).filter((v) => v.role !== "changeable");
          return (
            <fieldset key={part.alias} className="compose-part" aria-label={`Alias ${part.alias}`}>
              <legend>
                <code>{part.alias}</code> · {part.title}{" "}
                <span className="muted small mono">
                  {part.ref.id}
                  {part.ref.version != null ? `@${part.ref.version}` : ""}
                </span>
              </legend>
              <div className="btn-row">
                <button type="button" className="btn btn-sm btn-danger-ghost" onClick={() => remove(part.alias)}>
                  Remove {part.alias}
                </button>
              </div>
              {part.cards === null && <p className="muted small">Loading variables…</p>}
              {part.loadError && (
                <p className="field-error" role="alert">
                  Could not load {part.ref.id}: {part.loadError}
                </p>
              )}
              {changeable.length > 0 && <div className="run-vars">{changeable.map((v) => field(part, v))}</div>}
              {fixed.length > 0 && (
                <details className="details">
                  <summary>Fixed variables ({fixed.length})</summary>
                  <div className="run-vars">{fixed.map((v) => field(part, v))}</div>
                </details>
              )}
              {part.cards !== null && part.cards.length === 0 && !part.loadError && (
                <p className="muted small">No variables.</p>
              )}
            </fieldset>
          );
        })}
        <Field
          label="Final SELECT (optional)"
          htmlFor="cmp-select"
          hint="One read-only SELECT over the aliases. Join them only on their declared join keys: unknown joins label the result exploratory."
        >
          <textarea
            id="cmp-select"
            className="mono sql-input"
            rows={6}
            spellCheck={false}
            value={select}
            onChange={(e) => setSelect(e.target.value)}
            placeholder={parts.length > 1 ? `SELECT … FROM ${parts[0]?.alias} JOIN ${parts[1]?.alias} USING (…)` : ""}
          />
        </Field>
        <div className="run-options">
          <Field label="Profile" htmlFor="cmp-profile">
            <ProfileSelect id="cmp-profile" value={profile} onChange={setProfile} />
          </Field>
          <Field label="Row limit" htmlFor="cmp-limit">
            <select id="cmp-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {LIMITS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <div className="btn-row run-buttons">
            <button type="submit" className="btn btn-primary" disabled={busy !== null || parts.length === 0 || loading}>
              {busy === "run" ? "Running…" : "Run"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy !== null || parts.length === 0 || loading}
              onClick={() => void submit(false)}
            >
              {busy === "compile" ? "Compiling…" : "Compile only"}
            </button>
          </div>
        </div>
      </form>
      {outcome && <RunResult outcome={outcome} />}
      {outcome?.usable && canProposeRecipe(user) && (
        <SaveRecipeForm
          key={outcome.compile_id ?? "saved"}
          request={build(false).request}
          dialect={outcome.dialect}
          initial={{ id: seed?.recipeId ?? "", title: seed?.title ?? "", description: seed?.description ?? "" }}
        />
      )}
    </div>
  );
}
