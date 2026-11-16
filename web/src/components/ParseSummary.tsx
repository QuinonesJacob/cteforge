import type { ParseReport } from "../api/types";
import { IssueList } from "./IssueList";

/** What the parser found besides literals: tables, outputs, CTEs, joins, schema status. */
export function ParseSummary({ report }: { report: ParseReport }) {
  return (
    <div className="parse-summary">
      {!report.schema_found && (
        <p className="callout callout-warn small" role="status">
          No warehouse schema was found for these tables, so column types are unknown and type suggestions are guesses.
          Pick a profile to read real types.
        </p>
      )}
      <dl className="facts facts-wrap">
        <div>
          <dt>Dialect</dt>
          <dd>{report.dialect}</dd>
        </div>
        <div>
          <dt>Tables</dt>
          <dd className="mono small">{report.tables.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Outputs</dt>
          <dd className="mono small">{report.outputs.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>CTEs</dt>
          <dd className="mono small">{report.ctes.map((c) => c.name).join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Literals</dt>
          <dd>{report.literals.length}</dd>
        </div>
        <div>
          <dt>Range pairs</dt>
          <dd>{report.ranges.length}</dd>
        </div>
        <div>
          <dt>Grain candidates</dt>
          <dd>{report.grains?.length ?? 0}</dd>
        </div>
      </dl>
      {report.joins.length > 0 && (
        <details className="details">
          <summary>{report.joins.length} joins found</summary>
          <ul className="small">
            {report.joins.map((j, i) => (
              <li key={i}>
                <code>{j.left_table}</code> {j.join_type} join <code>{j.right_table}</code>
                {j.keys.length > 0 && <> on {j.keys.map(([l, r]) => `${l} = ${r}`).join(", ")}</>}
              </li>
            ))}
          </ul>
        </details>
      )}
      <IssueList issues={report.issues} />
    </div>
  );
}
