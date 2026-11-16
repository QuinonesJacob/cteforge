import { useMemo } from "react";
import { tokenizeLines } from "../lib/sqlTokens";
import { CopyButton } from "./CopyButton";

interface Props {
  sql: string;
  label?: string;
  maxHeight?: number;
}

/** Read-only SQL with line numbers and keyword colouring (display only). */
export function SqlViewer({ sql, label = "SQL", maxHeight }: Props) {
  const lines = useMemo(() => tokenizeLines(sql), [sql]);
  return (
    <figure className="sql">
      <figcaption className="sql-bar">
        <span className="sql-label">{label}</span>
        <span className="muted small">{lines.length} lines</span>
        <CopyButton text={sql} label="Copy SQL" />
      </figcaption>
      <pre className="sql-body" style={maxHeight ? { maxHeight } : undefined} tabIndex={0} aria-label={label}>
        <code>
          {lines.map((tokens, i) => (
            <span className="sql-line" key={i}>
              <span className="sql-ln" aria-hidden="true">
                {i + 1}
              </span>
              <span className="sql-code">
                {tokens.length === 0
                  ? "​"
                  : tokens.map((t, j) =>
                      t.kind === "text" || t.kind === "operator" ? (
                        t.text
                      ) : (
                        <span key={j} className={`tk-${t.kind}`}>
                          {t.text}
                        </span>
                      ),
                    )}
              </span>
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
