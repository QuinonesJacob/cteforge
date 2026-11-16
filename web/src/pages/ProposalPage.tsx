import { Link, useParams } from "react-router-dom";
import { getDefinition, getProposal } from "../api/endpoints";
import type { ProposalView } from "../api/types";
import { Badge } from "../components/Badge";
import { DiffViewer } from "../components/DiffViewer";
import { ErrorState } from "../components/ErrorState";
import { ImpactList } from "../components/ImpactList";
import { IssueList } from "../components/IssueList";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { ProposalActions } from "../components/ProposalActions";
import { SqlViewer } from "../components/SqlViewer";
import { formatDateTime } from "../lib/format";
import { canApprove, roleAtLeast, type DecideTarget } from "../lib/roles";
import { statusTone } from "../lib/trust";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

export function ProposalPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const { data, error, loading, reload } = useAsync((signal) => getProposal(id, { signal }), [id]);
  // The target definition's owner (and state) decide whether a non-approver may approve or
  // reject (owner) and whether an agent approver may (not on a verified target). Fetched only
  // when it changes what is offered; a missing target (new definition) is null.
  const needTarget =
    data?.status === "pending" &&
    (data.kind === "new_definition" || data.kind === "revise_definition") &&
    user !== null &&
    (canApprove(user) ? user.kind === "agent" : roleAtLeast(user.role, "contributor"));
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
  const isDefinition = p.kind === "new_definition" || p.kind === "revise_definition";
  const yaml = typeof p.payload.definition_yaml === "string" ? p.payload.definition_yaml : null;

  return (
    <div className="page">
      <p className="small">
        <Link to="/review">← Review queue</Link>
      </p>
      <PageHeader
        title={p.summary || `${p.kind.replace(/_/g, " ")}: ${p.target_id}`}
        subtitle={
          <div className="def-meta">
            <Badge tone={statusTone(p.status)}>{p.auto_applied ? "auto-applied" : p.status}</Badge>
            <code>{p.kind}</code>
            <span className="small">
              by <strong>{p.author}</strong> <span className="tag">{p.author_kind}</span>
            </span>
            <span className="small muted">{formatDateTime(p.created_at)}</span>
          </div>
        }
      />
      <div className="detail-grid">
        <div className="stack">
          <section className="card">
            <h2>Target</h2>
            <p>
              {isDefinition ? (
                <Link to={`/definitions/${encodeURIComponent(p.target_id)}`} className="mono">
                  {p.target_id}
                </Link>
              ) : (
                <code>{p.target_id}</code>
              )}
              {p.base_version != null && <span className="muted"> · based on v{p.base_version}</span>}
              {p.resulting_version != null && <span className="muted"> · became v{p.resulting_version}</span>}
            </p>
            {p.decided_by && (
              <p className="small">
                Decided by <strong>{p.decided_by}</strong> {formatDateTime(p.decided_at)}
                {p.comment && <> — “{p.comment}”</>}
              </p>
            )}
          </section>
          <section className="card">
            <h2>Checks</h2>
            <IssueList issues={p.checks} emptyText="All checks passed." />
          </section>
          <section>
            <h2>Diff</h2>
            {p.diff ? (
              <DiffViewer diff={p.diff} />
            ) : yaml ? (
              <SqlViewer sql={yaml} label="Proposed definition (new, no previous version)" maxHeight={480} />
            ) : (
              <pre className="card mono small payload">{JSON.stringify(p.payload, null, 2)}</pre>
            )}
          </section>
        </div>
        <aside className="stack">
          <section className="card">
            <h2>Impact</h2>
            <ImpactList impact={p.impact} />
          </section>
          <ProposalActions
            proposal={p}
            user={user}
            target={target.data ?? null}
            targetLoading={needTarget && target.loading}
            onDone={reload}
          />
        </aside>
      </div>
    </div>
  );
}
