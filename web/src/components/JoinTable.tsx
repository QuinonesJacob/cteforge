import type { JoinEdge } from "../api/types";
import { StateBadge } from "./StateBadge";

/** Join-map edges (DESIGN §3): tables, keys, relationship, usage and known traps. */
export function JoinTable({ joins, label = "Join map" }: { joins: JoinEdge[]; label?: string }) {
  if (joins.length === 0) return <p className="muted">No joins.</p>;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={label}>
      <table>
        <thead>
          <tr>
            <th scope="col">Left</th>
            <th scope="col">Right</th>
            <th scope="col">Keys</th>
            <th scope="col">Type</th>
            <th scope="col">Relationship</th>
            <th scope="col" className="num">
              Used by
            </th>
            <th scope="col">Traps</th>
            <th scope="col">State</th>
          </tr>
        </thead>
        <tbody>
          {joins.map((j) => (
            <tr key={j.id}>
              <td className="mono">{j.left_table}</td>
              <td className="mono">{j.right_table}</td>
              <td className="mono small">
                {j.keys.map((k) => (
                  <div key={`${k.left}=${k.right}`}>
                    {k.left} = {k.right}
                  </div>
                ))}
              </td>
              <td>{j.join_type}</td>
              <td>{j.relationship ?? <span className="muted">unknown</span>}</td>
              <td className="num">{j.usage_count}</td>
              <td className="small">{j.traps.length ? j.traps.join("; ") : <span className="muted">none known</span>}</td>
              <td>
                <StateBadge state={j.state} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
