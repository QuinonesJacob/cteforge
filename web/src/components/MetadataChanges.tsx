import type { MetadataChange } from "../api/types";
import { formatValue } from "../lib/format";
import { fieldLabel, listDelta } from "../lib/metadata";

function Value({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0))
    return <span className="muted">not set</span>;
  return <span className="meta-value">{formatValue(value)}</span>;
}

/**
 * What a definition proposal changes besides the query (ADR 0029): each described field,
 * before → after. Lists (keywords, variables) show what was added and removed.
 */
export function MetadataChanges({ changes, isNew }: { changes: MetadataChange[]; isNew: boolean }) {
  return (
    <section className="card" aria-labelledby="meta-h">
      <h2 id="meta-h">{isNew ? "Description" : "Description changes"}</h2>
      {changes.length === 0 ? (
        <p className="muted small">No described field changes.</p>
      ) : (
        <dl className="meta-changes">
          {changes.map((c) => {
            const delta = !isNew ? listDelta(c.before, c.after) : null;
            return (
              <div key={c.field} className="meta-change">
                <dt>{fieldLabel(c.field)}</dt>
                <dd>
                  {delta ? (
                    <ul className="plain-list meta-delta">
                      {delta.removed.map((v) => (
                        <li key={`-${v}`} className="meta-removed">
                          <span aria-hidden="true">− </span>
                          <span className="visually-hidden">removed: </span>
                          {v}
                        </li>
                      ))}
                      {delta.added.map((v) => (
                        <li key={`+${v}`} className="meta-added">
                          <span aria-hidden="true">+ </span>
                          <span className="visually-hidden">added: </span>
                          {v}
                        </li>
                      ))}
                    </ul>
                  ) : isNew ? (
                    <Value value={c.after} />
                  ) : (
                    <span className="meta-before-after">
                      <span className="meta-before">
                        <span className="visually-hidden">before: </span>
                        <Value value={c.before} />
                      </span>
                      <span aria-hidden="true"> → </span>
                      <span className="meta-after">
                        <span className="visually-hidden">after: </span>
                        <Value value={c.after} />
                      </span>
                    </span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}
