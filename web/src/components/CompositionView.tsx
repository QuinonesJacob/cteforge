import { Link } from "react-router-dom";
import type { CompositionCard } from "../api/types";
import { formatValue } from "../lib/format";
import { Badge } from "./Badge";
import { IssueList } from "./IssueList";
import { SqlViewer } from "./SqlViewer";
import { StateBadge } from "./StateBadge";

/**
 * A composed definition (ADR 0026): its parts (alias → definition@version or "latest"),
 * the variables it fixes and those left open, its final SELECT and the full compiled SQL.
 */
export function CompositionView({ composition, label }: { composition: CompositionCard; label: string }) {
  const fixed = Object.entries(composition.fixed).flatMap(([alias, vars]) =>
    Object.entries(vars).map(([name, value]) => ({ name: `${alias}.${name}`, value })),
  );
  return (
    <div className="stack" aria-label="Composition">
      <section className="card">
        <h2>Parts</h2>
        <p className="muted small">
          Parts without a pinned version follow their latest usable version, so a fix to a part reaches this definition.
        </p>
        <ul className="part-list">
          {composition.parts.map((p) => (
            <li key={p.alias} className="part-item">
              <code>{p.alias}</code>
              <span aria-hidden="true">→</span>
              <Link to={`/definitions/${encodeURIComponent(p.id)}${p.version != null ? `?version=${p.version}` : ""}`} className="mono">
                {p.id}
              </Link>
              <span className="small">
                {p.pinned_version != null ? `pinned v${p.pinned_version}` : `latest${p.version != null ? ` (v${p.version})` : ""}`}
              </span>
              {p.state && <StateBadge state={p.state} />}
              {p.composed && <span className="tag">composed</span>}
              {p.superseded && (
                <Badge tone="newlogic" title={`Newer version: v${p.latest_version ?? "?"}`}>
                  superseded (latest v{p.latest_version})
                </Badge>
              )}
              {!p.usable && <Badge tone="danger">not usable</Badge>}
            </li>
          ))}
        </ul>
      </section>
      <section className="card">
        <h2>Variables</h2>
        <div className="grid-2">
          <div>
            <h3 className="small">Fixed by this definition</h3>
            {fixed.length ? (
              <ul className="plain-list small">
                {fixed.map((f) => (
                  <li key={f.name}>
                    <code>{f.name}</code> = {formatValue(f.value)}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">None.</p>
            )}
          </div>
          <div>
            <h3 className="small">Open</h3>
            {composition.open_variables.length ? (
              <ul className="chips">
                {composition.open_variables.map((v) => (
                  <li key={v} className="chip mono">
                    {v}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted small">None: runs exactly as composed.</p>
            )}
          </div>
        </div>
      </section>
      {composition.issues.length > 0 && (
        <section className="card">
          <h2>Notes</h2>
          <IssueList issues={composition.issues} />
        </section>
      )}
      {composition.select && (
        <section>
          <h2>Final SELECT</h2>
          <SqlViewer sql={composition.select} label={`${label}: final SELECT over the parts`} maxHeight={240} />
        </section>
      )}
      <section>
        <h2>Compiled SQL</h2>
        {composition.compiled_sql ? (
          <SqlViewer sql={composition.compiled_sql} label={`${label}: full query with original values${composition.dialect ? ` (${composition.dialect})` : ""}`} />
        ) : (
          <p className="muted">It does not compile right now (see the notes above).</p>
        )}
      </section>
    </div>
  );
}
