import { useState } from "react";
import { listAudit, type AuditFilter } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { Loading } from "../components/Loading";
import { LogFilters } from "../components/LogFilters";
import { LogTable } from "../components/LogTable";
import { logSummary } from "../lib/audit";
import { plural } from "../lib/format";
import { useAsync } from "../state/useAsync";

const PAGE = 100;
const MAX = 1000; // GET /audit's limit

/** The Log (ADR 0029; the audit log, ADR 0008), newest first: every MCP call (what agents
 * looked up), every run and change, every refused call. People's own reads in the GUI /
 * API / CLI are not logged (ADR 0008 addendum). */
export function LogPage() {
  const [filter, setFilter] = useState<AuditFilter>({});
  const [limit, setLimit] = useState(PAGE);
  const { data, error, loading, reload } = useAsync(
    (signal) => listAudit({ ...filter, limit }, { signal }),
    [filter.actor, filter.kind, filter.definition_id, limit],
  );
  const s = data ? logSummary(data.events) : null;

  return (
    <div className="page">
      <div className="title-row">
        <div className="title-group">
          <h1>Log</h1>
          <span className="title-sub">Agent calls, runs and changes · result rows are never stored</span>
        </div>
        <button type="button" className="icon-btn icon-btn-outline" aria-label="Refresh" title="Refresh" onClick={reload}>
          <Icon name="refresh" size={16} strokeWidth={2.2} />
        </button>
      </div>
      <LogFilters
        value={filter}
        onApply={(f) => {
          setLimit(PAGE);
          setFilter(f);
        }}
      />
      {loading && !data ? (
        <Loading label="Loading events…" />
      ) : error ? (
        <ErrorState error={error} title="Could not load the log" onRetry={reload} />
      ) : !data || data.events.length === 0 ? (
        <EmptyState title="No events match">Try clearing the filters.</EmptyState>
      ) : (
        <section className="table-card" aria-label="Log">
          <div className="table-head">
            <span>
              <strong>{plural(data.events.length, "event")}</strong>, newest first{data.has_more && " · more exist"}
            </span>
            {s && (
              <span className="log-summary">
                <span>{plural(s.agents, "agent")}</span>
                <span>{plural(s.people, "person", "people")}</span>
                <span className={s.errors ? "txt-danger" : undefined}>{plural(s.errors, "error")}</span>
                <span className={s.denied ? "txt-pending" : undefined}>{s.denied} denied</span>
              </span>
            )}
          </div>
          <LogTable events={data.events} actors={data.actors} />
          <div className="table-foot">
            <span>
              Showing {data.events.length}
              {data.has_more ? " · more exist" : ""}
            </span>
            {data.has_more && limit < MAX && (
              <button type="button" className="btn btn-sm" onClick={() => setLimit((l) => Math.min(MAX, l + PAGE))} disabled={loading}>
                {loading ? "Loading…" : "Load more"}
              </button>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
