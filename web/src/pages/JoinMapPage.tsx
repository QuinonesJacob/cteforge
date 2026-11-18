import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { listJoins, listProposals } from "../api/endpoints";
import type { JoinCard, JoinEdge, ProposalOutcome } from "../api/types";
import { ErrorState } from "../components/ErrorState";
import { IssueList } from "../components/IssueList";
import { JoinAddForm } from "../components/JoinAddForm";
import { JoinAnnotateForm } from "../components/JoinAnnotateForm";
import { JoinTable } from "../components/JoinTable";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { formatDateTime, plural } from "../lib/format";
import { relationshipLabel } from "../lib/joins";
import { can } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

/**
 * The join map (ADR 0026): edges mined from definitions, plus those people (or agents,
 * pending a person) added. People annotate an edge or add one through proposals.
 */
export function JoinMapPage() {
  const { user } = useAuth();
  const mayPropose = can(user, "propose_join");
  const [draft, setDraft] = useState("");
  const [table, setTable] = useState("");
  const [knownOnly, setKnownOnly] = useState(false);
  const [editing, setEditing] = useState<JoinCard | "add" | null>(null);
  const [last, setLast] = useState<ProposalOutcome | null>(null);
  const joins = useAsync((signal) => listJoins({ table: table || undefined, known_only: knownOnly }, { signal }), [table, knownOnly]);
  // Pending additions are proposals, not edges yet (an agent's never applies on its own).
  const mayList = can(user, "list_proposals");
  const proposals = useAsync((signal) => (mayList ? listProposals("pending", { signal }) : Promise.resolve([])), [mayList]);
  const known = new Set((joins.data ?? []).map((e) => e.id));
  const additions = (proposals.data ?? []).filter((p) => p.kind === "new_join" && p.payload.action === "add" && !known.has(p.target_id));

  function onFilter(e: FormEvent) {
    e.preventDefault();
    setTable(draft.trim());
  }

  function done(outcome: ProposalOutcome) {
    setLast(outcome);
    setEditing(null);
    joins.reload();
    proposals.reload();
  }

  return (
    <div className="page">
      <PageHeader
        title="Join map"
        subtitle="How tables join, mined from trusted definitions. SQL that joins only on known edges keeps a better trust label."
        actions={
          mayPropose &&
          editing !== "add" && (
            <button type="button" className="btn btn-primary" onClick={() => setEditing("add")}>
              Add a join
            </button>
          )
        }
      />
      {last && (
        <div className="callout callout-info" role="status">
          <p className="small">
            {last.auto_applied ? "Applied" : "Proposed — waiting for review"}:{" "}
            <Link to={`/review/${encodeURIComponent(last.proposal.id)}`}>{last.proposal.summary || last.proposal.target_id}</Link>
          </p>
          {last.issues.length > 0 && <IssueList issues={last.issues} />}
        </div>
      )}
      {mayPropose && editing === "add" && <JoinAddForm onDone={done} onCancel={() => setEditing(null)} />}
      {mayPropose && editing && editing !== "add" && (
        <JoinAnnotateForm key={editing.id} edge={editing} onDone={done} onCancel={() => setEditing(null)} />
      )}
      {additions.length > 0 && (
        <section className="card" aria-label="Proposed joins">
          <h2>Proposed joins</h2>
          <p className="muted small">New edges waiting for a person. They don't count as known until approved.</p>
          <ul className="plain-list">
            {additions.map((p) => {
              const edge = p.payload.join as JoinEdge | undefined;
              return (
                <li key={p.id} className="part-item">
                  <Link to={`/review/${encodeURIComponent(p.id)}`} className="mono">
                    {edge ? `${edge.left_table} ↔ ${edge.right_table}` : p.target_id}
                  </Link>
                  {edge && (
                    <span className="small mono">{edge.keys.map((k) => `${k.left} = ${k.right}`).join(", ")}</span>
                  )}
                  {edge?.relationship && <span className="small">{relationshipLabel(edge.relationship)}</span>}
                  <span className="small muted">
                    by {p.author}
                    {p.author_kind === "agent" && " (agent)"} · {formatDateTime(p.created_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <form className="search-bar" role="search" onSubmit={onFilter}>
        <label htmlFor="joins-table" className="visually-hidden">
          Filter by table
        </label>
        <input id="joins-table" type="search" placeholder="Filter by table" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" className="btn">
          Filter
        </button>
        <label className="check">
          <input type="checkbox" checked={knownOnly} onChange={(e) => setKnownOnly(e.target.checked)} />
          <span>Known only</span>
        </label>
      </form>
      {joins.loading && !joins.data ? (
        <Loading label="Loading join map…" />
      ) : joins.error ? (
        <ErrorState error={joins.error} title="Could not load the join map" onRetry={joins.reload} />
      ) : (
        <>
          <p className="muted small" aria-live="polite">
            {plural(joins.data?.length ?? 0, "edge")}
          </p>
          <JoinTable joins={joins.data ?? []} onAnnotate={mayPropose ? (edge) => setEditing(edge as JoinCard) : undefined} />
        </>
      )}
    </div>
  );
}
