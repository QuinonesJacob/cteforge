import { useState, type FormEvent } from "react";
import { listJoins } from "../api/endpoints";
import { useAsync } from "../state/useAsync";
import { ErrorState } from "./ErrorState";
import { JoinTable } from "./JoinTable";
import { Loading } from "./Loading";

/** The join map (GET /joins): mined from trusted SQL or proposed. A reference, not a gate. */
export function JoinsPanel() {
  const [draft, setDraft] = useState("");
  const [table, setTable] = useState("");
  const joins = useAsync((signal) => listJoins(table, false, { signal }), [table]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setTable(draft.trim());
  }

  return (
    <div className="stack">
      <p className="muted small">
        Joins between base tables found in trusted SQL. Agent SQL whose joins all match keeps a better trust label
        (new_logic_on_known_joins instead of exploratory).
      </p>
      <form className="search-bar" role="search" onSubmit={onSubmit}>
        <label htmlFor="joins-table" className="visually-hidden">
          Filter by table
        </label>
        <input id="joins-table" type="search" placeholder="Filter by table" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button type="submit" className="btn">
          Filter
        </button>
      </form>
      {joins.loading && !joins.data ? (
        <Loading label="Loading join map…" />
      ) : joins.error ? (
        <ErrorState error={joins.error} title="Could not load the join map" onRetry={joins.reload} />
      ) : (
        <JoinTable joins={joins.data ?? []} />
      )}
    </div>
  );
}
