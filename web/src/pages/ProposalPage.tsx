import { Link, useParams } from "react-router-dom";
import { getDefinition, getProposal } from "../api/endpoints";
import type { DefinitionReferences, JoinEdge, ProposalView, RelationshipEvidence } from "../api/types";
import { CompositionView } from "../components/CompositionView";
import { DiffViewer } from "../components/DiffViewer";
import { ErrorState } from "../components/ErrorState";
import { ImpactList } from "../components/ImpactList";
import { MetadataChanges } from "../components/MetadataChanges";
import { IssueList } from "../components/IssueList";
import { JoinDetail } from "../components/JoinDetail";
import { Loading } from "../components/Loading";
import { ProposalActions } from "../components/ProposalActions";
import { ReferencesList } from "../components/ReferencesList";
import { RelationshipEvidenceView } from "../components/RelationshipEvidenceView";
import { SqlViewer } from "../components/SqlViewer";
import { formatDateTime, timeAgo } from "../lib/format";
import { REVIEW_QUEUE_HREF } from "../lib/library";
import { can, canDecide, DEFINITION_KINDS, type DecideTarget } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

export function ProposalPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const { data, error, loading, reload } = useAsync((signal) => getProposal(id, { signal }), [id]);
  // The target definition's owner and state decide whether a non-approver may approve or
  // reject (owner), whether an agent approver may (not on a verified target) and whether
  // "keep it verified" applies. Fetched only for reviewers of a pending change to an existing
  // definition; a missing target (new definition) is null.
  const needTarget =
    data?.status === "pending" &&
    DEFINITION_KINDS.includes(data.kind) &&
    data.base_version != null &&
    can(user, "review_proposal");
  const targetId = data?.target_id;
  const target = useAsync<DecideTarget | null>(
    (signal) =>
      needTarget && targetId
        ? getDefinition(targetId, undefined, { signal }).then(
            (d) => ({ owner: d.owner ?? null, state: d.state }),
            () => null,
          )
        : Promise.resolve(null),
    [needTarget, targetId],
  );

  if (loading && !data) return <Loading label="Loading proposal…" />;
  if (error) return <ErrorState error={error} title="Could not load proposal" onRetry={reload} />;
  const p = data as ProposalView;
  const isDefinition = DEFINITION_KINDS.includes(p.kind);
  // Renames (ADR 0024) carry from/to and the references the proposer saw.
  const rename =
    p.kind === "rename_definition"
      ? {
          from: String(p.payload.from_id ?? ""),
          to: String(p.payload.to_id ?? ""),
          refs: isObject(p.payload.references) ? (p.payload.references as unknown as DefinitionReferences) : null,
        }
      : null;
  const yaml = typeof p.payload.definition_yaml === "string" ? p.payload.definition_yaml : null;
  // Join proposals carry the edge itself, the action and the relationship evidence (ADR 0026).
  const join = p.kind === "new_join" && isObject(p.payload.join) ? (p.payload.join as unknown as JoinEdge) : null;
  const joinAction = typeof p.payload.action === "string" ? p.payload.action : null;
  const evidence = isObject(p.payload.evidence) ? (p.payload.evidence as unknown as RelationshipEvidence) : null;
  // Legacy recipe proposals (removed by ADR 0026) only show their payload.
  const legacyRecipe = p.kind === "new_recipe";

  const decidable = p.status === "pending" && canDecide(user, p.kind, target.data ?? null) && user?.name !== p.author;
  const title = p.title || (p.kind === "new_join" && join ? `${join.left_table} ↔ ${join.right_table}` : p.target_id);
  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Library</Link> <span aria-hidden="true">/</span> <Link to={REVIEW_QUEUE_HREF}>Pending review</Link>{" "}
        <span aria-hidden="true">/</span> <span>Proposal</span>
      </nav>
      <section className="prop-hero">
        <div className="prop-kind">
          <span className="eyebrow eyebrow-accent">{kindTitle(p.kind, joinAction, target.data?.state === "verified")}</span>
          <span className={`status-chip status-${p.status}`}>
            <span className="dot" aria-hidden="true" />
            {p.auto_applied ? "Auto-applied" : p.status === "pending" && decidable ? "Waiting on you" : STATUS_TEXT[p.status]}
          </span>
        </div>
        <h1 className="display">{title}</h1>
        <p className="lead">
          Proposed by <strong>{p.author}</strong>
          {p.author_kind === "agent" && " (agent)"}, <span title={formatDateTime(p.created_at)}>{timeAgo(p.created_at)}</span>.
          {p.summary ? <> “{p.summary}”</> : <span className="muted"> No note.</span>}
        </p>
      </section>
      <div className="detail-grid">
        <div className="stack">
          {p.sql_diff != null ? (
            <section aria-label="Query change">
              <h2 className="visually-hidden">Query change</h2>
              {p.sql_diff ? (
                <DiffViewer
                  diff={p.sql_diff}
                  label={`${p.target_id}.sql${p.base_version != null ? ` · v${p.base_version} → proposed` : " · new"}`}
                />
              ) : (
                <p className="callout callout-info small" role="note">
                  The query does not change: this proposal changes the description only.
                </p>
              )}
            </section>
          ) : (
            !join &&
            !rename && (
              <section aria-label="Proposed change">
                <h2 className="visually-hidden">Proposed change</h2>
                {yaml ? (
                  <SqlViewer sql={yaml} label="Proposed definition" maxHeight={480} />
                ) : (
                  <pre className="card mono small payload">{JSON.stringify(p.payload, null, 2)}</pre>
                )}
              </section>
            )
          )}
          {p.metadata_changes && <MetadataChanges changes={p.metadata_changes} isNew={p.base_version == null} />}
          {legacyRecipe && (
            <p className="callout callout-info small" role="note">
              A recipe proposal from before recipes became composed definitions (ADR 0026). Recipes no longer exist; it can't
              be applied.
            </p>
          )}
          {rename && (
            <section className="card" aria-label="Proposed rename">
              <h2>Rename</h2>
              <p className="rename-arrow">
                {rename.from} → {rename.to}
              </p>
              <p className="small muted">
                No new version: every version, composed definition and pending proposal that uses the ID moves to the new one. The
                old ID stays searchable; looking it up fails with an error naming the new ID.
              </p>
              <h3 className="small">Used inside cteforge when proposed</h3>
              {rename.refs ? <ReferencesList refs={rename.refs} /> : <p className="muted small">Not recorded.</p>}
              <div className="callout callout-warn" role="note">
                <p className="small">
                  {rename.refs?.outside_warning ||
                    "References outside cteforge (agent instructions, scripts) stop working."}
                </p>
              </div>
            </section>
          )}
          {join && (
            <section className="card" aria-label="Proposed join">
              <h2>{joinAction === "add" ? "New join" : joinAction === "annotate" ? "Join annotation" : "Join"}</h2>
              {joinAction === "add" && p.author_kind === "agent" && (
                <p className="muted small">
                  Added by an agent: it never applies on its own and counts as known only once a person approves it.
                </p>
              )}
              <p className="mono small">
                {join.left_table} {join.join_type} join {join.right_table} on{" "}
                {join.keys.map((k) => `${k.left} = ${k.right}`).join(", ")}
              </p>
              <JoinDetail edge={{ ...join, known: false, confirmed: false, pending_proposals: [] }} />
              {evidence && (
                <>
                  <h3 className="small">Relationship check</h3>
                  <RelationshipEvidenceView evidence={evidence} />
                </>
              )}
            </section>
          )}
          {p.composition && (
            <section aria-label="Proposed composition">
              <h2 className="visually-hidden">Composition</h2>
              <CompositionView composition={p.composition} label={p.target_id} />
            </section>
          )}
          <section className="card">
            <h2>Checks</h2>
            <IssueList issues={p.checks} emptyText="All checks passed." />
          </section>
          <section className="card">
            <h2>Used by</h2>
            <ImpactList impact={p.impact} />
          </section>
        </div>
        <aside className="stack">
          <ProposalActions
            proposal={p}
            user={user}
            target={target.data ?? null}
            targetLoading={needTarget && target.loading}
            onDone={reload}
          />
          <section className="card activity" aria-labelledby="activity-h">
            <h2 id="activity-h">Activity</h2>
            <ul className="plain-list">
              <li>
                <strong>{p.author}</strong>
                {p.author_kind === "agent" && " (agent)"} proposed this · {formatDateTime(p.created_at)}
              </li>
              {p.auto_applied && <li>Applied at once (team mode allowed it) · became v{p.resulting_version ?? "?"}</li>}
              {p.decided_by && (
                <li>
                  <strong>{p.decided_by}</strong>
                  {p.decided_by_kind === "agent" && " (agent)"} {p.status === "withdrawn" ? "withdrew" : p.status} it ·{" "}
                  {formatDateTime(p.decided_at)}
                  {p.comment && <> — “{p.comment}”</>}
                </li>
              )}
              {p.resulting_version != null && !p.auto_applied && <li>Became v{p.resulting_version}</li>}
            </ul>
            <p className="small muted">
              Target:{" "}
              {isDefinition ? (
                <Link to={`/definitions/${encodeURIComponent(p.target_id)}`} className="mono">
                  {p.target_id}
                </Link>
              ) : (
                <code>{p.target_id}</code>
              )}
              {p.base_version != null && <> · based on v{p.base_version}</>}
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = { pending: "Pending", approved: "Approved", rejected: "Rejected", withdrawn: "Withdrawn" };

function kindTitle(kind: string, joinAction: string | null, verifiedTarget: boolean): string {
  switch (kind) {
    case "new_definition":
      return "New definition";
    case "revise_definition":
      return verifiedTarget ? "Change to a verified definition" : "Change to a definition";
    case "rename_definition":
      return "Rename";
    case "new_join":
      return joinAction === "add" ? "New join" : "Join annotation";
    default:
      return kind.replace(/_/g, " ");
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
