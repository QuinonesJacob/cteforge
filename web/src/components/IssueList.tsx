import type { Issue } from "../api/types";
import { severityRank } from "../lib/trust";

interface Props {
  issues: Issue[];
  emptyText?: string;
  title?: string;
}

const ICON: Record<string, string> = { error: "✕", warning: "!", info: "i" };

/** Structured issues (code, severity, message, variable, literal), most severe first. */
export function IssueList({ issues, emptyText, title }: Props) {
  if (issues.length === 0) return emptyText ? <p className="muted">{emptyText}</p> : null;
  const sorted = [...issues].sort((a, b) => severityRank(a.severity) - severityRank(b.severity));
  return (
    <div className="issues">
      {title && <h4>{title}</h4>}
      <ul className="issue-list">
        {sorted.map((issue, i) => (
          <li key={i} className={`issue sev-${issue.severity}`}>
            <span className="issue-icon" aria-hidden="true">
              {ICON[issue.severity] ?? "•"}
            </span>
            <div className="issue-body">
              <div className="issue-head">
                <span className="visually-hidden">{issue.severity}: </span>
                <code className="issue-code">{issue.code}</code>
                {issue.variable && (
                  <span className="issue-tag">
                    variable <code>{issue.variable}</code>
                  </span>
                )}
                {issue.literal && (
                  <span className="issue-tag">
                    literal <code>{issue.literal}</code>
                  </span>
                )}
              </div>
              <p className="issue-msg">{issue.message}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
