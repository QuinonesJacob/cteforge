import type { Recipe, RecipeInput } from "../api/types";
import { formatValue } from "../lib/format";
import { SqlViewer } from "./SqlViewer";
import { StateBadge } from "./StateBadge";

/** A recipe (DESIGN §3): definitions by reference + values + optional final SELECT. */
export function RecipeSummary({ recipe }: { recipe: Recipe | RecipeInput }) {
  const aliases = Object.entries(recipe.uses);
  return (
    <div className="stack">
      <div className="def-meta">
        {"state" in recipe && <StateBadge state={recipe.state} />}
        <span className="mono small">{recipe.id}</span>
        {"version" in recipe && <span className="small muted">v{recipe.version}</span>}
        <span className="small muted">{recipe.dialect}</span>
      </div>
      {recipe.description && <p>{recipe.description}</p>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label={`Definitions used by ${recipe.id}`}>
        <table>
          <thead>
            <tr>
              <th scope="col">Alias</th>
              <th scope="col">Definition</th>
              <th scope="col">Values</th>
            </tr>
          </thead>
          <tbody>
            {aliases.map(([alias, ref]) => (
              <tr key={alias}>
                <td className="mono">{alias}</td>
                <td className="mono">
                  {ref.id}
                  {ref.version != null ? `@${ref.version}` : " (latest)"}
                </td>
                <td className="small">
                  {Object.entries(recipe.values[alias] ?? {}).map(([name, value]) => (
                    <div key={name}>
                      <code>{name}</code> = <span className="mono">{formatValue(value)}</span>
                    </div>
                  ))}
                  {Object.keys(recipe.values[alias] ?? {}).length === 0 && <span className="muted">original values</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {recipe.select ? (
        <SqlViewer sql={recipe.select} label="Final SELECT" maxHeight={240} />
      ) : (
        <p className="muted small">No final SELECT: the definition's own result.</p>
      )}
    </div>
  );
}
