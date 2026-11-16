import { useSearchParams } from "react-router-dom";
import { listProposals } from "../api/endpoints";
import type { ProposalStatus } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { ProposalList } from "../components/ProposalList";
import { Tabs } from "../components/Tabs";
import { useAsync } from "../state/useAsync";
import { usePendingCount } from "../state/usePendingCount";

const STATUSES: ProposalStatus[] = ["pending", "approved", "rejected", "withdrawn"];

export function ReviewPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("status") as ProposalStatus | null;
  const status: ProposalStatus = raw && STATUSES.includes(raw) ? raw : "pending";
  const pending = usePendingCount(true);
  const { data, error, loading, reload } = useAsync((signal) => listProposals(status, { signal }), [status]);

  return (
    <div className="page">
      <PageHeader
        title="Review"
        subtitle="Every change to the library is a proposal. Checks run when it is made; a person decides."
        actions={
          <button type="button" className="btn" onClick={reload}>
            Refresh
          </button>
        }
      />
      <Tabs<ProposalStatus>
        idPrefix="rev"
        label="Proposal status"
        active={status}
        onChange={(s) => setParams(s === "pending" ? {} : { status: s }, { replace: true })}
        items={STATUSES.map((s) => ({
          key: s,
          label: s[0]!.toUpperCase() + s.slice(1),
          badge: s === "pending" ? pending : null,
        }))}
      />
      <div role="tabpanel" id={`rev-panel-${status}`} aria-labelledby={`rev-tab-${status}`} className="tab-panel">
        {loading && !data ? (
          <Loading label="Loading proposals…" />
        ) : error ? (
          <ErrorState error={error} title="Could not load proposals" onRetry={reload} />
        ) : !data || data.length === 0 ? (
          <EmptyState title={status === "pending" ? "Nothing waiting for review" : `No ${status} proposals`}>
            {status === "pending" ? "New imports and changes from people and agents appear here." : null}
          </EmptyState>
        ) : (
          <ProposalList proposals={data} />
        )}
      </div>
    </div>
  );
}
