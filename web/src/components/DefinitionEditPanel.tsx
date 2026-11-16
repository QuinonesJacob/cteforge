import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { reviseDefinition } from "../api/endpoints";
import type { DefinitionCard, ProposalOutcome } from "../api/types";
import { validateMetadata } from "../lib/importBuilder";
import { changedMetadata, metadataFromCard } from "../lib/revise";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { ImportResult } from "./ImportResult";
import { IssueList } from "./IssueList";
import { MetadataForm } from "./MetadataForm";

interface Props {
  def: DefinitionCard;
  /** Called after a revision was applied (the page reloads the card). */
  onApplied: () => void;
}

/**
 * Edit tab: propose the next version (POST /proposals/definitions/{id}/revise). Text-only
 * edits send just the changed metadata keys (nothing re-runs); SQL and variables are changed
 * in the import builder in revise mode. Mount with a key per version.
 */
export function DefinitionEditPanel({ def, onApplied }: Props) {
  const toast = useToast();
  const [initial] = useState(() => metadataFromCard(def));
  const [meta, setMeta] = useState(initial);
  const [showErrors, setShowErrors] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<ProposalOutcome | null>(null);

  const errors = validateMetadata(meta);
  const changed = changedMetadata(initial, meta);
  const nothing = Object.keys(changed).length === 0;

  async function submit() {
    setShowErrors(true);
    if (Object.keys(errors).length > 0 || nothing) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await reviseDefinition(def.id, {
        base_version: def.version,
        metadata: changed,
        summary: meta.summary.trim() || `Edit ${Object.keys(changed).join(", ")} of ${def.id}`,
      });
      setOutcome(result);
      notifyProposalsChanged();
      if (result.auto_applied) {
        toast.success(`Applied ${def.id} v${result.definition?.version ?? def.version + 1}`);
        onApplied();
      }
    } catch (err) {
      if (err instanceof ApiError) setError(err);
      else toast.error(err, "Could not propose the revision");
    } finally {
      setSubmitting(false);
    }
  }

  if (outcome) return <ImportResult outcome={outcome} revision />;

  return (
    <div className="stack">
      <section className="card" aria-label="Change SQL or variables">
        <h2>SQL and variables</h2>
        <p className="muted small">
          Change the trusted SQL, its dialect or which values are variables. Opens the import builder with this
          definition's SQL and variables filled in; every check runs again.
        </p>
        <Link className="btn" to={`/import?revise=${encodeURIComponent(def.id)}`}>
          Change SQL or variables…
        </Link>
      </section>
      <section className="card" aria-label="Edit description">
        <h2>Description</h2>
        <p className="muted small">
          Text-only edits (title, meaning, keywords, notes, grain, join keys, owner) propose v{def.version + 1} with the
          same SQL. {def.state === "verified" ? "This definition is verified: a person must approve the change." : ""}
        </p>
        <MetadataForm
          value={meta}
          onChange={setMeta}
          errors={errors}
          showErrors={showErrors}
          idTouched
          onIdTouched={() => undefined}
          idLocked
        />
        {error && (
          <div className="callout callout-danger" role="alert">
            <p className="callout-title">Could not propose ({error.type})</p>
            <p>{error.message}</p>
            <IssueList issues={error.issues} />
          </div>
        )}
        <div className="btn-row">
          <button type="button" className="btn btn-primary" disabled={submitting || nothing} onClick={() => void submit()}>
            {submitting ? "Proposing…" : `Propose v${def.version + 1}`}
          </button>
          {nothing && <span className="muted small">Change a field to propose a revision.</span>}
        </div>
      </section>
    </div>
  );
}
