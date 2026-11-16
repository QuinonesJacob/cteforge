import { useState, type FormEvent } from "react";
import { listRecipes, runQuery } from "../api/endpoints";
import type { Recipe, RunOutcome } from "../api/types";
import { buildRecipeRunRequest } from "../lib/variableForm";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { Field } from "./Field";
import { Loading } from "./Loading";
import { ProfileSelect } from "./ProfileSelect";
import { RecipeSummary } from "./RecipeSummary";
import { RunResult } from "./RunResult";

/** Saved recipes (GET /recipes): run one with its values, or open it in the composer. */
export function RecipesPanel({ onOpen }: { onOpen: (recipe: Recipe) => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ id: string; outcome: RunOutcome } | null>(null);
  const recipes = useAsync((signal) => listRecipes(q, false, { signal }), [q]);

  function onSearch(e: FormEvent) {
    e.preventDefault();
    setQ(draft.trim());
  }

  async function run(recipe: Recipe, execute: boolean) {
    setBusy(`${recipe.id}:${execute ? "run" : "compile"}`);
    try {
      const outcome = await runQuery(buildRecipeRunRequest(recipe.id, { run: execute, limit: 100, profile: profile || undefined }));
      setResult({ id: recipe.id, outcome });
    } catch (err) {
      toast.error(err, execute ? "Run failed" : "Compile failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack">
      <form className="search-bar" role="search" onSubmit={onSearch}>
        <label htmlFor="rcp-q" className="visually-hidden">
          Search recipes
        </label>
        <input id="rcp-q" type="search" placeholder="Search recipes" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" className="btn">
          Search
        </button>
      </form>
      <Field label="Profile" htmlFor="rcp-profile">
        <ProfileSelect id="rcp-profile" value={profile} onChange={setProfile} />
      </Field>
      {recipes.loading && !recipes.data ? (
        <Loading label="Loading recipes…" />
      ) : recipes.error ? (
        <ErrorState error={recipes.error} title="Could not load recipes" onRetry={recipes.reload} />
      ) : (recipes.data ?? []).length === 0 ? (
        <EmptyState title={q ? `No recipes match “${q}”` : "No recipes yet"}>
          Combine definitions in Compose and save the result as a recipe, or have an agent propose one.
        </EmptyState>
      ) : (
        (recipes.data ?? []).map((r) => (
          <section key={r.id} className="card" aria-label={`Recipe ${r.id}`}>
            <h2>{r.title}</h2>
            <RecipeSummary recipe={r} />
            <div className="btn-row mt">
              <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void run(r, true)}>
                {busy === `${r.id}:run` ? "Running…" : "Run"}
              </button>
              <button type="button" className="btn" disabled={busy !== null} onClick={() => void run(r, false)}>
                {busy === `${r.id}:compile` ? "Compiling…" : "Compile only"}
              </button>
              <button type="button" className="btn" onClick={() => onOpen(r)}>
                Change values…
              </button>
            </div>
            {result?.id === r.id && <RunResult outcome={result.outcome} />}
          </section>
        ))
      )}
    </div>
  );
}
