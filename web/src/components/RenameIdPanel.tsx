import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { getDefinitionReferences, renameDefinition } from "../api/endpoints";
import type { DefinitionCard, ProposalOutcome } from "../api/types";
import { checkRename, referenceCount } from "../lib/rename";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { useAsync } from "../state/useAsync";
import { ErrorState } from "./ErrorState";
import { Field } from "./Field";
import { IssueList } from "./IssueList";
import { Loading } from "./Loading";
import { ReferencesList } from "./ReferencesList";

interface Props {
  def: DefinitionCard;
  /** Called when the rename was applied at once (the page moves to the new ID). */
  onRenamed: (newId: string) => void;
}

/**
 * Edit tab → Rename ID (ADR 0024): everything inside cteforge that uses the ID, a warning
 * about references outside it, the new ID typed twice. Submitting proposes the rename
 * (POST /proposals/definitions/{id}/rename): applied at once in open mode on a definition
 * that is not verified, otherwise it waits for approval.
 */
export function RenameIdPanel({ def, onRenamed }: Props) {
  const toast = useToast();
  const refs = useAsync((signal) => getDefinitionReferences(def.id, { signal }), [def.id]);
  const [newId, setNewId] = useState("");
  const [confirm, setConfirm] = useState("");
  const [summary, setSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<ProposalOutcome | null>(null);
  const check = checkRename(def.id, newId, confirm);
  const target = newId.trim();

  async function submit() {
    if (!check.ready) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await renameDefinition(def.id, {
        new_id: target,
        base_version: def.version,
        summary: summary.trim(),
      });
      notifyProposalsChanged();
      if (result.auto_applied) {
        toast.success(`Renamed ${def.id} to ${target}`);
        onRenamed(target);
        return;
      }
      setOutcome(result);
    } catch (err) {
      if (err instanceof ApiError) setError(err);
      else toast.error(err, "Could not propose the rename");
    } finally {
      setSubmitting(false);
    }
  }

  if (outcome) {
    return (
      <section className="card" aria-live="polite" aria-label="Rename ID">
        <h2>Rename proposal submitted</h2>
        <p>
          <span className="rename-arrow">
            {def.id} → {target}
          </span>{" "}
          waits for an approver{def.state === "verified" ? " (a person: this definition is verified)" : ""}. Until it is
          approved, the ID stays <code>{def.id}</code>.
        </p>
        <div className="btn-row">
          <Link className="btn btn-primary" to={`/review/${encodeURIComponent(outcome.proposal.id)}`}>
            View proposal
          </Link>
        </div>
      </section>
    );
  }

  const count = referenceCount(refs.data);
  return (
    <section className="card" aria-label="Rename ID">
      <h2>Rename ID</h2>
      <p className="muted small">
        Agents and recipes call this definition by <code>{def.id}</code>. If agents don't find it under that name, a
        clearer ID helps. Renaming keeps every version, its state and its history.
      </p>
      <h3 className="small">Used inside cteforge{refs.data ? ` (${count})` : ""}</h3>
      {refs.loading && !refs.data ? (
        <Loading label="Loading references…" />
      ) : refs.error ? (
        <ErrorState error={refs.error} title="Could not load references" onRetry={refs.reload} />
      ) : refs.data ? (
        <>
          <ReferencesList refs={refs.data} />
          {count > 0 && <p className="small">These are updated to the new ID automatically.</p>}
        </>
      ) : null}
      <div className="callout callout-warn" role="note">
        <p className="callout-title">References outside cteforge will break</p>
        <p className="small">
          {refs.data?.outside_warning ||
            "Agent instructions, prompts and scripts that use the old ID stop working. A lookup of the old ID fails with an error naming the new ID; there is no alias."}{" "}
          The old ID stays searchable and can't be given to another definition.
        </p>
        {def.state === "verified" && <p className="small">This definition is verified: a person must approve the rename.</p>}
      </div>
      <div className="grid-2">
        <Field label="New ID" htmlFor="rn-id" required error={check.idError} hint="lower_snake_case, e.g. realization_rate_monthly">
          <input
            id="rn-id"
            type="text"
            className="mono"
            autoComplete="off"
            spellCheck={false}
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
          />
        </Field>
        <Field label="Type the new ID to confirm" htmlFor="rn-confirm" required hint="Must match the new ID exactly.">
          <input
            id="rn-confirm"
            type="text"
            className="mono"
            autoComplete="off"
            spellCheck={false}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </Field>
      </div>
      <Field label="Why (for reviewers)" htmlFor="rn-summary">
        <input id="rn-summary" type="text" value={summary} onChange={(e) => setSummary(e.target.value)} />
      </Field>
      {error && (
        <div className="callout callout-danger" role="alert">
          <p className="callout-title">Could not propose the rename ({error.type})</p>
          <p>{error.message}</p>
          <IssueList issues={error.issues} />
        </div>
      )}
      <div className="btn-row">
        <button type="button" className="btn btn-danger" disabled={!check.ready || submitting} onClick={() => void submit()}>
          {submitting ? "Proposing…" : target && check.idError === null ? `Rename to ${target}` : "Rename ID"}
        </button>
        {!check.ready && <span className="muted small">Type the new ID twice to enable renaming.</span>}
      </div>
    </section>
  );
}
