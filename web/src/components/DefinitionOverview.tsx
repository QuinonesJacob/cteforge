import type { DefinitionCard } from "../api/types";
import { SqlViewer } from "./SqlViewer";
import { VariableTable } from "./VariableTable";

/** Meaning, usage notes, facts, variables and the SQL template of one definition version. */
export function DefinitionOverview({ d }: { d: DefinitionCard }) {
  return (
    <div className="overview">
      <section className="card prose">
        <h2>Meaning</h2>
        <p>{d.meaning || <span className="muted">No meaning written yet.</span>}</p>
        <div className="grid-2">
          <div>
            <h3>Use when</h3>
            <p>{d.use_when || <span className="muted">—</span>}</p>
          </div>
          <div>
            <h3>Don't use when</h3>
            <p>{d.dont_use_when || <span className="muted">—</span>}</p>
          </div>
        </div>
        <dl className="facts facts-wrap">
          <div>
            <dt>Grain</dt>
            <dd>{d.grain || "—"}</dd>
          </div>
          <div>
            <dt>Join keys</dt>
            <dd className="mono small">{d.join_keys.join(", ") || "—"}</dd>
          </div>
          <div>
            <dt>Outputs</dt>
            <dd className="mono small">{d.outputs.join(", ") || "—"}</dd>
          </div>
          <div>
            <dt>Tables</dt>
            <dd className="mono small">{d.tables.join(", ") || "—"}</dd>
          </div>
        </dl>
        {d.keywords.length > 0 && (
          <ul className="keywords" aria-label="Keywords">
            {d.keywords.map((k) => (
              <li key={k}>{k}</li>
            ))}
          </ul>
        )}
      </section>
      <section>
        <h2>Variables</h2>
        <VariableTable variables={d.variables} />
      </section>
      <section>
        <h2>SQL template</h2>
        <p className="muted small">Placeholders are highlighted. Everything else is the trusted logic, kept as written.</p>
        <SqlViewer sql={d.template} label={`${d.id} v${d.version} (${d.source_dialect})`} />
      </section>
    </div>
  );
}
