import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { listLibrary } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Icon } from "../components/Icon";
import { LibraryFilters } from "../components/LibraryFilters";
import { LibraryRowCard } from "../components/LibraryRowCard";
import { Loading } from "../components/Loading";
import { TrustIcon } from "../components/TrustIcon";
import { WaitingPanel } from "../components/WaitingPanel";
import { plural } from "../lib/format";
import { filtersFromParams, isFiltered, libraryQuery, paramsFromFilters, type LibraryFilters as Filters } from "../lib/library";
import { can, canPropose } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

/**
 * The Library (ADR 0026, 0029): definitions and the proposals touching them on one page,
 * searched and filtered on the server (GET /library), sorted by relevance then trust. Filters
 * live in the URL, so `/?review=mine&sort=oldest` is the review queue. The side shows the
 * trust legend and "Waiting on you" (proposals the user may decide, oldest first).
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
  // Follow the URL when it changes elsewhere (e.g. the "Open review queue" link).
  useEffect(() => setText(filters.q), [filters.q]);

  const { data, error, loading, reload } = useAsync((signal) => listLibrary(query, { signal }), [key]);
  const rows = data?.rows ?? [];
  const definitions = data ? data.rows.filter((r) => r.version != null).length : 0;
  const filtered = Boolean(filters.q) || isFiltered(effective);

  return (
    <div className="page">
      <div className="title-row">
        <div className="title-group">
          <h1>Library</h1>
          {data && (
            <span className="title-sub">
              {filtered ? plural(rows.length, "match", "matches") : plural(definitions, "definition")}
              {data.proposals_visible && data.pending_count > 0 && ` · ${plural(data.pending_count, "pending proposal")}`}
            </span>
          )}
        </div>
        {canPropose(user) && (
          <Link to="/import" className="btn btn-primary">
            <Icon name="plus" size={14} strokeWidth={2.6} />
            Import SQL
          </Link>
        )}
      </div>
      <div className="library-search">
        <div className="search-field search-field-lg" role="search">
          <Icon name="search" size={20} strokeWidth={2} />
          <label htmlFor="lib-q" className="visually-hidden">
            Search the library
          </label>
          <input
            id="lib-q"
            type="search"
            placeholder="Search by words in the id, title, meaning or keywords"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>
        <LibraryFilters
          filters={effective}
          onChange={(next) => apply(next)}
          showApproval={showApproval && data?.proposals_visible !== false}
          showReview={showReview}
        />
      </div>
      <div className="library-layout">
        <div className="library-results">
          {loading && !data ? (
            <Loading label="Loading the library…" />
          ) : error ? (
            <ErrorState error={error} title="Could not load the library" onRetry={reload} />
          ) : rows.length === 0 ? (
            <EmptyState
              title={
                effective.needsMyReview ? "Nothing needs your review" : filtered ? "Nothing matches these filters" : "The library is empty"
              }
              action={
                !filtered &&
                canPropose(user) && (
                  <Link to="/import" className="btn btn-primary">
                    Import your first query
                  </Link>
                )
              }
            >
              {effective.needsMyReview
                ? "Proposals you may approve appear here."
                : filtered
                  ? "Try other words or fewer filters."
                  : "Paste SQL you already trust to create the first definition."}
            </EmptyState>
          ) : (
            <>
              <div className="results-head">
                <h2 className="eyebrow" aria-live="polite">
                  {plural(rows.length, "match", "matches")}
                  {data && data.total !== rows.length && ` of ${data.total}`} · {effective.sort === "oldest" ? "oldest first" : "best first"}
                  {loading && " · updating…"}
                </h2>
                <span className="muted small">
                  {effective.sort === "oldest" ? "Sorted by the oldest proposal waiting" : "Sorted by relevance, then trust"}
                </span>
              </div>
              <div className="lib-rows">
                {rows.map((row) => (
                  <LibraryRowCard key={`${row.id}:${row.version ?? row.latest_proposal?.id ?? ""}`} row={row} />
                ))}
              </div>
            </>
          )}
        </div>
        <aside className="library-side" aria-label="Trust and review">
          <div>
            <h2 className="side-label">Trust</h2>
            <ul className="trust-legend">
              <li>
                <TrustIcon value="verified" decorative /> Verified
              </li>
              <li>
                <TrustIcon value="llm_annotated" decorative /> LLM-annotated
              </li>
              <li>
                <TrustIcon value="draft" decorative /> Draft
              </li>
            </ul>
          </div>
          {showReview && showApproval && data?.proposals_visible && <WaitingPanel waiting={data.waiting ?? []} />}
        </aside>
      </div>
    </div>
  );
}
