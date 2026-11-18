import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listLibrary } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LibraryFilters } from "../components/LibraryFilters";
import { LibraryRowCard } from "../components/LibraryRowCard";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { plural } from "../lib/format";
import { filtersFromParams, isFiltered, libraryQuery, paramsFromFilters, type LibraryFilters as Filters } from "../lib/library";
import { can, canPropose } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

/**
 * The Library (ADR 0026): definitions and the proposals touching them on one page, filtered
 * on the server (GET /library). Filters live in the URL, so `/?review=mine` is the old
 * Review queue.
 */
export function LibraryPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const filters = filtersFromParams(params);
  const showApproval = can(user, "list_proposals");
  const showReview = can(user, "review_proposal");
  // A token without review_proposal can't ask for "needs my review" (403): drop it.
  const effective: Filters = showReview ? filters : { ...filters, needsMyReview: false };
  const query = libraryQuery(effective);
  const key = JSON.stringify(query);
  const [text, setText] = useState(filters.q);

  function apply(next: Filters, replace = true) {
    setParams(paramsFromFilters(next), { replace });
  }

  // Debounce typing into the URL (which drives the list).
  useEffect(() => {
    if (text === filters.q) return;
    const t = window.setTimeout(() => apply({ ...filters, q: text }), 250);
    return () => window.clearTimeout(t);
  }, [text, filters.q]);

  const { data, error, loading, reload } = useAsync((signal) => listLibrary(query, { signal }), [key]);
  const rows = data?.rows ?? [];

  return (
    <div className="page">
      <PageHeader
        title="Library"
        subtitle="Trusted SQL and the proposals changing it: search, review and build on it."
        actions={
          canPropose(user) && (
            <Link to="/import" className="btn btn-primary">
              Import SQL
            </Link>
          )
        }
      />
      <div className="search-bar" role="search">
        <label htmlFor="lib-q" className="visually-hidden">
          Search the library
        </label>
        <input
          id="lib-q"
          type="search"
          placeholder="Filter by words in the id, title, meaning or keywords…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      <div className="library-layout">
        <LibraryFilters
          filters={effective}
          onChange={(next) => apply(next)}
          showApproval={showApproval && data?.proposals_visible !== false}
          showReview={showReview}
          onReset={() => apply({ ...filtersFromParams(new URLSearchParams()), q: filters.q })}
        />
        <div className="library-results">
          {loading && !data ? (
            <Loading label="Loading the library…" />
          ) : error ? (
            <ErrorState error={error} title="Could not load the library" onRetry={reload} />
          ) : rows.length === 0 ? (
            <EmptyState
              title={
                effective.needsMyReview
                  ? "Nothing needs your review"
                  : filters.q || isFiltered(effective)
                    ? "Nothing matches these filters"
                    : "The library is empty"
              }
              action={
                !filters.q &&
                !isFiltered(effective) &&
                canPropose(user) && (
                  <Link to="/import" className="btn btn-primary">
                    Import your first query
                  </Link>
                )
              }
            >
              {effective.needsMyReview
                ? "Proposals you may approve appear here."
                : filters.q || isFiltered(effective)
                  ? "Try other words or fewer filters."
                  : "Paste SQL you already trust to create the first definition."}
            </EmptyState>
          ) : (
            <>
              <p className="muted small" aria-live="polite">
                {plural(rows.length, "row")}
                {data && data.total !== rows.length && ` of ${data.total}`}
                {data && data.proposals_visible && data.pending_count > 0 && ` · ${plural(data.pending_count, "pending proposal")}`}
                {loading && " · updating…"}
              </p>
              <div className="hit-grid">
                {rows.map((row) => (
                  <LibraryRowCard key={`${row.id}:${row.version ?? row.latest_proposal?.id ?? ""}`} row={row} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
