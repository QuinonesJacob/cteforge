import { useMemo } from "react";
import { markValues, tokenizeLines } from "../lib/sqlTokens";

/**
 * The parsed SQL on Import (ADR 0029): values underlined, those made into variables
 * highlighted, selected ones outlined. Display only.
 */
export function MarkedSql({
  sql,
  label,
  literals,
  variableIds,
  selectedIds,
}: {
  sql: string;
  label: string;
  literals: { id: string; value: string; is_string: boolean }[];
  variableIds: string[];
  selectedIds: string[];
}) {
  const lines = useMemo(
    () => markValues(tokenizeLines(sql), literals, new Set(variableIds), new Set(selectedIds)),
    [sql, literals, variableIds, selectedIds],
  );
  return (
    <figure className="marked-sql">
      <figcaption className="marked-sql-bar">
        <span>{label}</span>
        <span>Values are underlined</span>
      </figcaption>
      <pre tabIndex={0} aria-label={label}>
        <code>
          {lines.map((line, i) => (
            <span key={i}>
              {line.map((t, j) =>
                t.mark ? (
                  <span key={j} className={`val val-${t.mark}`}>
                    {t.text}
                  </span>
                ) : t.kind === "keyword" || t.kind === "comment" ? (
                  <span key={j} className={`tk-${t.kind}`}>
                    {t.text}
                  </span>
                ) : (
                  t.text
                ),
              )}
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
