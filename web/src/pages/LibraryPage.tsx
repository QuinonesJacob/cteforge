import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { searchDefinitions } from "../api/endpoints";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { SearchHitCard } from "../components/SearchHitCard";
import { plural } from "../lib/format";
import { canPropose } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useAsync } from "../state/useAsync";

export function LibraryPage() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const drafts = params.get("drafts") === "1";
  const [text, setText] = useState(q);

  // Debounce typing into the URL (which drives the search).
  useEffect(() => {
    if (text === q) return;
    const t = window.setTimeout(() => {
      const next = new URLSearchParams(params);
      if (text) next.set("q", text);
      else next.delete("q");
      setParams(next, { replace: true });
    }, 250);
    return () => window.clearTimeout(t);
  }, [text, q, params, setParams]);

  const { data, error, loading, reload } = useAsync((signal) => searchDefinitions(q, drafts, { signal }), [q, drafts]);

  function toggleDrafts(on: boolean) {
    const next = new URLSearchParams(params);
    if (on) next.set("drafts", "1");
    else next.delete("drafts");
    setParams(next, { replace: true });
  }

  return (
    <div className="page">
      <PageHeader
        title="Library"
        subtitle="Trusted SQL your team and agents can search, run and build on."
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
          Search definitions
        </label>
        <input
          id="lib-q"
          type="search"
          placeholder="Search by meaning, keyword, table, variable…"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <label className="check">
          <input type="checkbox" checked={drafts} onChange={(e) => toggleDrafts(e.target.checked)} />
          <span>Include drafts</span>
        </label>
      </div>
      {loading && !data ? (
        <Loading label="Searching…" />
      ) : error ? (
        <ErrorState error={error} title="Search failed" onRetry={reload} />
      ) : data && data.length === 0 ? (
        <EmptyState
          title={q ? `Nothing matches “${q}”` : drafts ? "The library is empty" : "No usable definitions"}
          action={
            canPropose(user) && (
              <Link to="/import" className="btn btn-primary">
                Import your first query
              </Link>
            )
          }
        >
          {q
            ? "Try other words, or include drafts."
            : drafts
              ? "Paste SQL you already trust to create the first definition."
              : "In strict team mode only verified definitions are listed. Include drafts to see unverified ones, or paste SQL you already trust."}
        </EmptyState>
      ) : (
        <>
          <p className="muted small" aria-live="polite">
            {plural(data?.length ?? 0, "definition")}
            {loading && " · updating…"}
          </p>
          <div className="hit-grid">
            {(data ?? []).map((hit) => (
              <SearchHitCard key={hit.id} hit={hit} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
