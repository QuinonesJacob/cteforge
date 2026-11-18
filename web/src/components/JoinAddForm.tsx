import { useState, type FormEvent } from "react";
import { proposeJoin } from "../api/endpoints";
import { JOIN_TYPES, type ProposalOutcome } from "../api/types";
import { addRequest, emptyAddDraft } from "../lib/joins";
import { useToast } from "../state/ToastContext";
import { notifyProposalsChanged } from "../state/usePendingCount";
import { Field } from "./Field";
import { RelationshipSelect } from "./RelationshipSelect";

/** Propose an edge no definition contains (POST /proposals/joins add). */
export function JoinAddForm({ onDone, onCancel }: { onDone: (outcome: ProposalOutcome) => void; onCancel: () => void }) {
  const toast = useToast();
  const [draft, setDraft] = useState(emptyAddDraft);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function setKey(i: number, side: "left" | "right", value: string) {
    setDraft({ ...draft, keys: draft.keys.map((k, n) => (n === i ? { ...k, [side]: value } : k)) });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const built = addRequest(draft);
    setError(built.error);
    if (!built.request) return;
    setBusy(true);
    try {
      const outcome = await proposeJoin(built.request);
      notifyProposalsChanged();
      toast.success(outcome.auto_applied ? "Join added" : "Join proposed: it waits for review");
      onDone(outcome);
    } catch (err) {
      toast.error(err, "Could not propose the join");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack join-form" onSubmit={(e) => void submit(e)} aria-label="Add a join">
      <h2>Add a join</h2>
      <p className="muted small">
        For a join no definition contains yet. Joins mined from definitions are added automatically.
      </p>
      <div className="grid-2">
        <Field label="Left table" htmlFor="jn-left">
          <input id="jn-left" value={draft.left_table} onChange={(e) => setDraft({ ...draft, left_table: e.target.value })} />
        </Field>
        <Field label="Right table" htmlFor="jn-right">
          <input id="jn-right" value={draft.right_table} onChange={(e) => setDraft({ ...draft, right_table: e.target.value })} />
        </Field>
      </div>
      <fieldset className="key-pairs">
        <legend>Keys</legend>
        {draft.keys.map((k, i) => (
          <div key={i} className="key-pair">
            <label className="visually-hidden" htmlFor={`jn-kl-${i}`}>
              Left column {i + 1}
            </label>
            <input id={`jn-kl-${i}`} placeholder="left column" value={k.left} onChange={(e) => setKey(i, "left", e.target.value)} />
            <span aria-hidden="true">=</span>
            <label className="visually-hidden" htmlFor={`jn-kr-${i}`}>
              Right column {i + 1}
            </label>
            <input id={`jn-kr-${i}`} placeholder="right column" value={k.right} onChange={(e) => setKey(i, "right", e.target.value)} />
            {draft.keys.length > 1 && (
              <button
                type="button"
                className="btn btn-sm"
                aria-label={`Remove key ${i + 1}`}
                onClick={() => setDraft({ ...draft, keys: draft.keys.filter((_, n) => n !== i) })}
              >
                ×
              </button>
            )}
          </div>
        ))}
        <button type="button" className="link-btn small" onClick={() => setDraft({ ...draft, keys: [...draft.keys, { left: "", right: "" }] })}>
          + key pair
        </button>
      </fieldset>
      <div className="grid-2">
        <Field label="Join type" htmlFor="jn-type">
          <select id="jn-type" value={draft.join_type} onChange={(e) => setDraft({ ...draft, join_type: e.target.value })}>
            {JOIN_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Relationship" htmlFor="jn-rel">
          <RelationshipSelect
            id="jn-rel"
            value={draft.relationship}
            onChange={(relationship) => setDraft({ ...draft, relationship })}
            left={draft.left_table}
            right={draft.right_table}
          />
        </Field>
      </div>
      <Field label="Traps" htmlFor="jn-traps" hint="One per line.">
        <textarea id="jn-traps" rows={2} value={draft.traps} onChange={(e) => setDraft({ ...draft, traps: e.target.value })} />
      </Field>
      <Field label="Notes" htmlFor="jn-notes">
        <textarea id="jn-notes" rows={2} value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
      </Field>
      <Field label="Summary" htmlFor="jn-summary" hint="For reviewers.">
        <input id="jn-summary" value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} />
      </Field>
      {error && (
        <p className="field-warn" role="alert">
          {error}
        </p>
      )}
      <div className="row-buttons">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Proposing…" : "Propose join"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
