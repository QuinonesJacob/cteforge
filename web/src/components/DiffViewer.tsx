import { useMemo, useState } from "react";
import { changesOnly, diffStats, fullView, lineText, parseUnifiedDiff, splitRows, type DiffLine } from "../lib/diff";
import { plural } from "../lib/format";

const SIGN: Record<string, string> = { add: "+", remove: "−" };
const SAID: Record<string, string> = { add: "added", remove: "removed" };

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <div className="seg seg-dark" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Text({ line }: { line: DiffLine }) {
  return (
    <>
      {SAID[line.kind] && <span className="visually-hidden">{SAID[line.kind]}: </span>}
      {lineText(line) || "​"}
    </>
  );
}

/**
 * A proposal's diff (ADR 0029): +/− line counts, unified or side by side, changes only or the
 * full file (when the server sent full context; else what the diff carries).
 */
export function DiffViewer({ diff, label = "Changes" }: { diff: string; label?: string }) {
  const lines = useMemo(() => parseUnifiedDiff(diff), [diff]);
  const stats = diffStats(lines);
  const [layout, setLayout] = useState<"unified" | "split">("unified");
  const [scope, setScope] = useState<"changes" | "full">("changes");
  const view = useMemo(() => (scope === "changes" ? changesOnly(lines) : fullView(lines)), [lines, scope]);
  const gap = (count: number, span: number, key: number) => (
    <tr key={key} className="dl-gap">
      <td colSpan={span}>⋯ {plural(count, "unchanged line")}</td>
    </tr>
  );
  return (
    <figure className="diff">
      <figcaption className="sql-bar">
        <span className="sql-label">{label}</span>
        <span className="diff-stat add" aria-label={`${stats.added} lines added`}>
          +{stats.added}
        </span>
        <span className="diff-stat remove" aria-label={`${stats.removed} lines removed`}>
          −{stats.removed}
        </span>
        <span className="diff-toggles">
          <Seg label="Diff layout" value={layout} options={[["unified", "Unified"], ["split", "Side by side"]]} onChange={setLayout} />
          <Seg label="Diff scope" value={scope} options={[["changes", "Changes only"], ["full", "Full"]]} onChange={setScope} />
        </span>
      </figcaption>
      <div className="diff-body" tabIndex={0} role="region" aria-label={layout === "unified" ? "Unified diff" : "Side-by-side diff"}>
        {layout === "unified" ? (
          <table className="diff-table">
            <tbody>
              {view.map((l, i) =>
                l.kind === "gap" ? (
                  gap(l.count, 4, i)
                ) : (
                  <tr key={i} className={`dl dl-${l.kind}`}>
                    <td className="dl-no" aria-hidden="true">
                      {l.kind === "add" ? "" : (l.oldNo ?? "")}
                    </td>
                    <td className="dl-no" aria-hidden="true">
                      {l.kind === "remove" ? "" : (l.newNo ?? "")}
                    </td>
                    <td className="dl-sign" aria-hidden="true">
                      {SIGN[l.kind] ?? ""}
                    </td>
                    <td className="dl-text">
                      <Text line={l} />
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        ) : (
          <table className="diff-table diff-split">
            <tbody>
              {splitRows(view).map((r, i) =>
                r.kind === "gap" ? (
                  gap(r.count, 4, i)
                ) : (
                  <tr key={i} className="dl">
                    <td className={`dl-no ${r.left ? `dl-${r.left.kind}` : ""}`} aria-hidden="true">
                      {r.left?.oldNo ?? ""}
                    </td>
                    <td className={`dl-text ${r.left ? `dl-${r.left.kind}` : "dl-empty"}`}>{r.left && <Text line={r.left} />}</td>
                    <td className={`dl-no ${r.right ? `dl-${r.right.kind}` : ""}`} aria-hidden="true">
                      {r.right?.newNo ?? ""}
                    </td>
                    <td className={`dl-text ${r.right ? `dl-${r.right.kind}` : "dl-empty"}`}>{r.right && <Text line={r.right} />}</td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}
      </div>
    </figure>
  );
}
