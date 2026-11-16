import { useMemo } from "react";
import { diffStats, parseUnifiedDiff } from "../lib/diff";

const SIGN: Record<string, string> = { add: "added", remove: "removed" };

export function DiffViewer({ diff }: { diff: string }) {
  const lines = useMemo(() => parseUnifiedDiff(diff), [diff]);
  const stats = diffStats(lines);
  return (
    <figure className="diff">
      <figcaption className="sql-bar">
        <span className="sql-label">Changes</span>
        <span className="diff-stat add">+{stats.added}</span>
        <span className="diff-stat remove">−{stats.removed}</span>
      </figcaption>
      <div className="diff-body" tabIndex={0} role="region" aria-label="Unified diff">
        <table className="diff-table">
          <tbody>
            {lines.map((l, i) => (
              <tr key={i} className={`dl dl-${l.kind}`}>
                <td className="dl-no" aria-hidden="true">
                  {l.oldNo ?? ""}
                </td>
                <td className="dl-no" aria-hidden="true">
                  {l.newNo ?? ""}
                </td>
                <td className="dl-text">
                  {SIGN[l.kind] && <span className="visually-hidden">{SIGN[l.kind]}: </span>}
                  {l.text || "​"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
