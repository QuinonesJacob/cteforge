import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { describeTables } from "../api/endpoints";
import { parseList } from "../lib/importBuilder";
import { useAsync } from "../state/useAsync";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { Field } from "./Field";
import { Loading } from "./Loading";
import { ProfileSelect } from "./ProfileSelect";

/** Table descriptions (GET /tables): columns + types from the warehouse, who reads them. */
export function TablesPanel() {
  const [draft, setDraft] = useState("");
  const [profile, setProfile] = useState("");
  const [query, setQuery] = useState<{ names: string[]; profile: string }>({ names: [], profile: "" });
  const tables = useAsync((signal) => describeTables(query.names, query.profile, { signal }), [query]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setQuery({ names: parseList(draft), profile });
  }

  return (
    <div className="stack">
      <form className="card" onSubmit={onSubmit} aria-label="Describe tables">
        <div className="grid-2">
          <Field label="Tables" htmlFor="tbl-names" hint="Comma separated. Empty: every table the library reads.">
            <input id="tbl-names" type="text" className="mono" value={draft} onChange={(e) => setDraft(e.target.value)} />
          </Field>
          <Field label="Profile" htmlFor="tbl-profile">
            <ProfileSelect id="tbl-profile" value={profile} onChange={setProfile} />
          </Field>
        </div>
        <div className="btn-row">
          <button type="submit" className="btn btn-primary">
            Describe
          </button>
        </div>
      </form>
      {tables.loading && !tables.data ? (
        <Loading label="Reading table schemas…" />
      ) : tables.error ? (
        <ErrorState error={tables.error} title="Could not describe tables" onRetry={tables.reload} />
      ) : (tables.data ?? []).length === 0 ? (
        <EmptyState title="No tables">Import a definition first, or name tables to describe.</EmptyState>
      ) : (
        (tables.data ?? []).map((t) => (
          <section key={t.name} className="card" aria-label={`Table ${t.name}`}>
            <h2 className="mono">{t.name}</h2>
            {!t.found && <p className="callout callout-warn small">The warehouse does not know this table.</p>}
            {t.used_by.length > 0 && (
              <p className="small">
                Read by{" "}
                {t.used_by.map((id, i) => (
                  <span key={id}>
                    {i > 0 && ", "}
                    <Link to={`/definitions/${encodeURIComponent(id)}`} className="mono">
                      {id}
                    </Link>
                  </span>
                ))}
              </p>
            )}
            {t.joins.length > 0 && <p className="small muted">{t.joins.length} join-map edge(s) touch it.</p>}
            {t.columns.length > 0 && (
              <div className="table-wrap" tabIndex={0} role="region" aria-label={`Columns of ${t.name}`}>
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Column</th>
                      <th scope="col">Type</th>
                      <th scope="col">Unit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {t.columns.map((c) => (
                      <tr key={c.name}>
                        <td className="mono">{c.name}</td>
                        <td className="mono small">{c.type}</td>
                        <td>{t.units[c.name] ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ))
      )}
    </div>
  );
}
