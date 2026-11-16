import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { proposeRecipe } from "../api/endpoints";
import type { Dialect, ProposalOutcome, RunRequest } from "../api/types";
import { isIdent } from "../lib/importBuilder";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { Field } from "./Field";
import { IssueList } from "./IssueList";

interface Props {
  /** The composition as last built (uses, values, select). */
  request: RunRequest;
  /** The dialect the composition compiled in (the final SELECT is written in it). */
  dialect: Dialect;
  initial: { id: string; title: string; description: string };
}

/** Save a composition as a recipe: POST /proposals/recipes (DESIGN §3, §18). */
export function SaveRecipeForm({ request, dialect, initial }: Props) {
  const toast = useToast();
  const [id, setId] = useState(initial.id);
  const [title, setTitle] = useState(initial.title);
  const [description, setDescription] = useState(initial.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<ProposalOutcome | null>(null);
  const idOk = isIdent(id);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!idOk || !title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await proposeRecipe(
        {
          id,
          title: title.trim(),
          description: description.trim(),
          uses: request.uses ?? {},
          values: request.values,
          ...(request.select ? { select: request.select } : {}),
          dialect,
        },
        `Recipe ${id}`,
      );
      setOutcome(result);
      notifyProposalsChanged();
    } catch (err) {
      if (err instanceof ApiError) setError(err);
      else toast.error(err, "Could not propose the recipe");
    } finally {
      setBusy(false);
    }
  }

  if (outcome)
    return (
      <section className="card" aria-live="polite">
        <h2>{outcome.auto_applied ? "Recipe saved" : `Recipe proposal ${outcome.proposal.status}`}</h2>
        <IssueList issues={outcome.proposal.checks} emptyText="All checks passed." />
        <Link className="btn" to={`/review/${encodeURIComponent(outcome.proposal.id)}`}>
          View proposal
        </Link>
      </section>
    );

  return (
    <form className="card" onSubmit={(e) => void onSubmit(e)} aria-label="Save as recipe">
      <h2>Save as recipe</h2>
      <p className="muted small">
        A recipe keeps the definitions by reference with these values and SELECT, so fixing a definition fixes the recipe.
      </p>
      <div className="grid-2">
        <Field label="Recipe ID" htmlFor="rcp-id" required error={id && !idOk ? "Use lower_snake_case." : null}>
          <input id="rcp-id" type="text" className="mono" value={id} onChange={(e) => setId(e.target.value)} />
        </Field>
        <Field label="Title" htmlFor="rcp-title" required>
          <input id="rcp-title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </div>
      <Field label="Description" htmlFor="rcp-desc">
        <textarea id="rcp-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      {error && (
        <div className="callout callout-danger" role="alert">
          <p className="callout-title">Could not propose ({error.type})</p>
          <p>{error.message}</p>
          <IssueList issues={error.issues} />
        </div>
      )}
      <div className="btn-row">
        <button type="submit" className="btn btn-primary" disabled={busy || !idOk || !title.trim()}>
          {busy ? "Proposing…" : "Propose recipe"}
        </button>
      </div>
    </form>
  );
}
