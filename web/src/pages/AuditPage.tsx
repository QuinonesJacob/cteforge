import { useState } from "react";
import { listAudit, type AuditFilter } from "../api/endpoints";
import { AuditFilters } from "../components/AuditFilters";
import { AuditTable } from "../components/AuditTable";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { plural } from "../lib/format";
import { useAsync } from "../state/useAsync";

export function AuditPage() {
  const [filter, setFilter] = useState<AuditFilter>({ limit: 100 });
  const { data, error, loading, reload } = useAsync(
    (signal) => listAudit(filter, { signal }),
    [filter.actor, filter.kind, filter.definition_id, filter.limit],
  );

  return (
    <div className="page">
      <PageHeader
        title="Audit log"
        subtitle="cteforge's own calls: who ran what, with which trust label. Result rows are never stored."
        actions={
          <button type="button" className="btn" onClick={reload}>
            Refresh
          </button>
        }
      />
      <AuditFilters value={filter} onApply={setFilter} />
      {loading && !data ? (
        <Loading label="Loading events…" />
      ) : error ? (
        <ErrorState error={error} title="Could not load the audit log" onRetry={reload} />
      ) : !data || data.events.length === 0 ? (
        <EmptyState title="No events match">Try clearing the filters.</EmptyState>
      ) : (
        <>
          <p className="muted small">
            {plural(data.events.length, "event")}, newest first
            {data.has_more && " · more exist: raise the limit or narrow the filters"}
          </p>
          <AuditTable events={data.events} />
        </>
      )}
    </div>
  );
}
