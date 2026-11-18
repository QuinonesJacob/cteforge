import { useState, type FormEvent } from "react";
import { proposeJoin } from "../api/endpoints";
import type { JoinCard, ProposalOutcome } from "../api/types";
import { annotateDraft, annotateRequest } from "../lib/joins";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { Field } from "./Field";
import { RelationshipSelect } from "./RelationshipSelect";

/** Propose relationship / traps / notes for an existing edge (POST /proposals/joins annotate). */
export function JoinAnnotateForm({
  edge,
  onDone,
  onCancel,
}: {
  edge: JoinCard;
  onDone: (outcome: ProposalOutcome) => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState(() => annotateDraft(edge));
  const [busy, setBusy] = useState(false);
  const request = annotateRequest(edge, draft);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!request) return;
    setBusy(true);
    try {
      const outcome = await proposeJoin(request);
      notifyProposalsChanged();
      toast.success(outcome.auto_applied ? "Join annotated" : "Annotation proposed: it waits for review");
      onDone(outcome);
    } catch (err) {
      toast.error(err, "Could not propose the annotation");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack join-form" onSubmit={(e) => void submit(e)} aria-label={`Annotate ${edge.id}`}>
      <h2>
        Annotate <span className="mono">{edge.left_table}</span> ↔ <span className="mono">{edge.right_table}</span>
      </h2>
      <p className="muted small">
        Keys {edge.keys.map((k) => `${k.left} = ${k.right}`).join(", ")}. A relationship is checked against the warehouse
        (rows vs distinct keys on each side).
      </p>
      <Field label="Relationship" htmlFor="ja-rel">
        <RelationshipSelect
          id="ja-rel"
          value={draft.relationship}
          onChange={(relationship) => setDraft({ ...draft, relationship })}
          left={edge.left_table}
          right={edge.right_table}
        />
      </Field>
      <Field label="Traps" htmlFor="ja-traps" hint="One per line, e.g. “fan-out: one deal has many invoices”.">
        <textarea id="ja-traps" rows={3} value={draft.traps} onChange={(e) => setDraft({ ...draft, traps: e.target.value })} />
      </Field>
      <Field label="Notes" htmlFor="ja-notes">
        <textarea id="ja-notes" rows={2} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
      </Field>
      <Field label="Summary" htmlFor="ja-summary" hint="For reviewers.">
        <input id="ja-summary" value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} />
      </Field>
      <div className="row-buttons">
        <button type="submit" className="btn btn-primary" disabled={!request || busy}>
          {busy ? "Proposing…" : "Propose annotation"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
