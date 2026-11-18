import { Link } from "react-router-dom";
import type { DefinitionCard } from "../api/types";
import { bulletLines, shownName } from "../lib/format";
import { joinKeysText } from "../lib/joins";
import { useAuth } from "../state/AuthContext";
import { CompositionView } from "./CompositionView";
import { SqlViewer } from "./SqlViewer";
import { UsedByList } from "./UsedByList";
import { VariableTable } from "./VariableTable";

function Bullets({ text }: { text: string }) {
  const lines = bulletLines(text);
  if (lines.length === 0) return <p className="muted">Not written yet.</p>;
  return (
    <ul className="bullets">
      {lines.map((l, i) => (
        <li key={i}>{l}</li>
      ))}
    </ul>
  );
}

/**
 * The Overview tab (ADR 0029): Use when / Don't use when, the variables agents can fill,
 * the trusted SQL (collapsible) or, for a composed definition (ADR 0026), its parts and
 * compiled SQL; a side card with grain, outputs, joins (→ Join map), used by, keywords, owner.
 */
export function DefinitionOverview({ d }: { d: DefinitionCard }) {
  const { user } = useAuth();
  const joins = d.joins ?? [];
  const lines = d.template ? d.template.split("\n").length : 0;
  // The owner is a user name; the display name is known here only for yourself (ADR 0029:
  // profiles are not part of definition cards).
  const owner = d.owner && user && d.owner === user.name && user.display_name ? `${shownName(user)} (${d.owner})` : d.owner;
  return (
    <div className="def-overview">
      <div className="def-main">
        <div className="usage-grid">
          <section className="card" aria-labelledby="use-when-h">
            <h2 id="use-when-h" className="eyebrow eyebrow-accent">
              Use when
            </h2>
            <Bullets text={d.use_when} />
          </section>
          <section className="card" aria-labelledby="dont-use-h">
            <h2 id="dont-use-h" className="eyebrow eyebrow-danger">
              Don’t use when
            </h2>
            <Bullets text={d.dont_use_when} />
          </section>
        </div>
        <section className="card" aria-labelledby="vars-h">
          <div className="card-head">
            <h2 id="vars-h" className="eyebrow">
              Variables agents can fill
            </h2>
            <span className="muted small">
              {d.composed ? (
                <>
                  Open variables of its parts, named <code>&lt;part&gt;.&lt;variable&gt;</code>
                </>
              ) : (
                "Everything else in the SQL is locked"
              )}
            </span>
          </div>
          <VariableTable variables={d.variables} />
          {d.variables.some((v) => v.role === "changeable") && (
            <p className="muted small var-note">A variable left out uses its original value, the one in the trusted SQL.</p>
          )}
        </section>
        {d.composed && d.composition ? (
          <CompositionView composition={d.composition} label={`${d.id} v${d.version}`} />
        ) : (
          <details className="sql-details">
            <summary>
              Trusted SQL · {lines} {lines === 1 ? "line" : "lines"}
            </summary>
            <SqlViewer sql={d.template} label={`${d.id} v${d.version} (${d.source_dialect})`} />
          </details>
        )}
      </div>
      <aside className="card side-card" aria-label="Facts">
        <dl className="side-facts">
          <div>
            <dt>Grain</dt>
            <dd className="strong">{d.grain || <span className="muted">not set</span>}</dd>
          </div>
          <div>
            <dt>Outputs</dt>
            <dd className="mono">{d.outputs.join(" · ") || "—"}</dd>
          </div>
          <div>
            <dt>Joins</dt>
            <dd>
              {joins.length ? (
                <ul className="plain-list join-lines" aria-label={`Joins of ${d.id}`}>
                  {joins.map((j) => (
                    <li key={j.id}>
                      <span className="mono">{j.left_table}</span> → <span className="mono">{j.right_table}</span> on{" "}
                      <code>{joinKeysText(j.keys)}</code>
                      {j.confirmed && <span className="tag">confirmed</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="muted">No join-map edges touch its tables.</span>
              )}
              <Link to={`/joins?definition=${encodeURIComponent(d.id)}`} className="see-link">
                See in Join map
              </Link>
            </dd>
          </div>
          <div>
            <dt>Used by</dt>
            <dd>
              <UsedByList usedBy={d.used_by ?? []} />
            </dd>
          </div>
          <div>
            <dt>Keywords</dt>
            <dd>
              {d.keywords.length ? (
                <ul className="keywords" aria-label="Keywords">
                  {d.keywords.map((k) => (
                    <li key={k}>{k}</li>
                  ))}
                </ul>
              ) : (
                <span className="muted">—</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Owner</dt>
            <dd>{owner || <span className="muted">—</span>}</dd>
          </div>
          <div>
            <dt>Tables</dt>
            <dd className="mono small">{d.tables.join(", ") || "—"}</dd>
          </div>
        </dl>
      </aside>
    </div>
  );
}
