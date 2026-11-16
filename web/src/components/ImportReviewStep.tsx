import type { ApiError } from "../api/client";
import type { Dialect } from "../api/types";
import type { MetadataDraft, VariableDraft } from "../lib/importBuilder";
import { IssueList } from "./IssueList";
import { SqlViewer } from "./SqlViewer";

interface Props {
  meta: MetadataDraft;
  drafts: VariableDraft[];
  literalCount: number;
  missingDescriptions: string[];
  sql: string;
  dialect: Dialect;
  error: ApiError | null;
  submitting: boolean;
  onBack: () => void;
  onSubmit: () => void;
}

/** Import step 4: summary before submitting the proposal (checks run on submit). */
export function ImportReviewStep(props: Props) {
  const { meta, drafts, literalCount, missingDescriptions, sql, dialect, error, submitting, onBack, onSubmit } = props;
  // Grain variables name date truncations, not literals.
  const bound = drafts.filter((d) => d.type !== "grain").reduce((n, d) => n + d.literals.length, 0);
  return (
    <div className="stack">
      <section className="card" aria-label="Review before submitting">
        <h2>{meta.title}</h2>
        <p className="mono small">{meta.id}</p>
        <p>{meta.meaning}</p>
        <p className="small">
          {drafts.length} variables ({drafts.filter((d) => d.role === "changeable").length} changeable),{" "}
          {literalCount - bound} literals left as SQL.
        </p>
        {missingDescriptions.length > 0 && (
          <p className="callout callout-warn small">
            {missingDescriptions.join(", ")} still need a description. The definition will stay draft until they have one.
          </p>
        )}
        <SqlViewer sql={sql} label={`Source SQL (${dialect})`} maxHeight={280} />
      </section>
      {error && (
        <div className="callout callout-danger" role="alert">
          <p className="callout-title">Proposal refused ({error.type})</p>
          <p>{error.message}</p>
          <IssueList issues={error.issues} />
        </div>
      )}
      <div className="btn-row">
        <button type="button" className="btn" onClick={onBack}>
          Back
        </button>
        <button type="button" className="btn btn-primary" disabled={submitting} onClick={onSubmit}>
          {submitting ? "Running checks…" : "Submit proposal"}
        </button>
      </div>
    </div>
  );
}
