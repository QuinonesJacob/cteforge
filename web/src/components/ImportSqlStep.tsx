import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import { DIALECTS, type Dialect } from "../api/types";
import { Field } from "./Field";
import { IssueList } from "./IssueList";
import { ProfileSelect } from "./ProfileSelect";

interface Props {
  sql: string;
  onSql: (sql: string) => void;
  dialect: Dialect;
  onDialect: (d: Dialect) => void;
  /** Replaces the dialect field's hint (e.g. where the default came from). */
  dialectHint?: ReactNode;
  profile: string;
  onProfile: (p: string) => void;
  parsing: boolean;
  parsed: boolean;
  /** SQL, dialect or profile changed since the last parse. */
  stale: boolean;
  error: ApiError | null;
  onParse: () => void;
  onBack: () => void;
}

/** Import step 1: paste SQL, choose dialect and profile, parse. */
export function ImportSqlStep(props: Props) {
  const { sql, onSql, dialect, onDialect, dialectHint, profile, onProfile, parsing, parsed, stale, error, onParse, onBack } =
    props;
  return (
    <section className="card" aria-label="Paste SQL">
      <Field label="Trusted SQL" htmlFor="imp-sql" required hint="One query. Formatting is normalized; logic is kept exactly.">
        <textarea
          id="imp-sql"
          className="mono sql-input"
          rows={16}
          spellCheck={false}
          value={sql}
          onChange={(e) => onSql(e.target.value)}
        />
      </Field>
      <div className="grid-2">
        <Field label="Dialect" htmlFor="imp-dialect" hint={dialectHint ?? "The dialect the SQL is written in."}>
          <select id="imp-dialect" value={dialect} onChange={(e) => onDialect(e.target.value as Dialect)}>
            {DIALECTS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Warehouse profile" htmlFor="imp-profile" hint="Used to read real column types.">
          <ProfileSelect id="imp-profile" value={profile} onChange={onProfile} />
        </Field>
      </div>
      {error && (
        <div className="callout callout-danger" role="alert">
          <p className="callout-title">
            {error.type === "SqlParseError" ? "SQL syntax error" : `Could not parse (${error.type})`}
          </p>
          <p>{error.message}</p>
          <IssueList issues={error.issues} />
        </div>
      )}
      {parsed && stale && (
        <p className="callout callout-warn small" role="status">
          The SQL, dialect or profile changed since the last parse. Parse again to continue: variables whose literals
          moved or changed are removed, so none is bound to the wrong value.
        </p>
      )}
      <div className="btn-row">
        <button type="button" className="btn btn-primary" disabled={!sql.trim() || parsing} onClick={onParse}>
          {parsing ? "Parsing…" : parsed ? "Parse again" : "Parse"}
        </button>
        {parsed && !stale && (
          <button type="button" className="btn" onClick={onBack}>
            Back to variables
          </button>
        )}
      </div>
    </section>
  );
}
