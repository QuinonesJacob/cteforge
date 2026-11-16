import type { RunOutcome } from "../api/types";
import { formatDuration, plural } from "../lib/format";
import { IssueList } from "./IssueList";
import { ResultsTable } from "./ResultsTable";
import { SqlViewer } from "./SqlViewer";
import { TrustBadge } from "./TrustBadge";

export function RunResult({ outcome }: { outcome: RunOutcome }) {
  return (
    <section className="run-result" aria-label="Run result">
      <div className="run-result-head">
        <TrustBadge label={outcome.label} reasons={outcome.reasons} large />
        <dl className="facts">
          <div>
            <dt>Usable</dt>
            <dd>{outcome.usable ? "yes" : "no"}</dd>
          </div>
          <div>
            <dt>Ran</dt>
            <dd>{outcome.ran ? "yes" : "compiled only"}</dd>
          </div>
          {outcome.ran && (
            <div>
              <dt>Rows</dt>
              <dd>{outcome.row_count ?? outcome.rows.length}</dd>
            </div>
          )}
          <div>
            <dt>Duration</dt>
            <dd>{formatDuration(outcome.duration_ms)}</dd>
          </div>
          <div>
            <dt>Dialect</dt>
            <dd>{outcome.dialect}</dd>
          </div>
          {outcome.compile_id && (
            <div>
              <dt>compile_id</dt>
              <dd className="mono">{outcome.compile_id}</dd>
            </div>
          )}
        </dl>
      </div>
      {outcome.definitions.length > 0 && (
        <p className="small muted">
          Definitions: <span className="mono">{outcome.definitions.join(", ")}</span>
        </p>
      )}
      <IssueList issues={outcome.issues} title={outcome.issues.length ? "Issues" : undefined} />
      <SqlViewer sql={outcome.sql} label="Rendered SQL" maxHeight={360} />
      {outcome.ran &&
        (outcome.columns.length > 0 ? (
          <>
            <h3 className="mt">Results</h3>
            {outcome.truncated && (
              <p className="callout callout-warn small" role="status">
                Showing the first {plural(outcome.rows.length, "row")}; the result was truncated at the row limit.
              </p>
            )}
            <ResultsTable columns={outcome.columns} rows={outcome.rows} />
          </>
        ) : (
          <p className="muted">The query returned no columns.</p>
        ))}
    </section>
  );
}
