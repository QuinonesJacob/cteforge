import { ApiError, errorMessage } from "../api/client";
import { IssueList } from "./IssueList";

interface Props {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}

/** Inline error for a failed load, with the API's structured issues when present. */
export function ErrorState({ error, title = "Could not load this", onRetry }: Props) {
  const api = error instanceof ApiError ? error : null;
  return (
    <div className="callout callout-danger" role="alert">
      <p className="callout-title">
        {title}
        {api && api.type !== "HTTPError" && <span className="muted"> · {api.type}</span>}
      </p>
      <p>{errorMessage(error)}</p>
      {api && api.issues.length > 0 && <IssueList issues={api.issues} />}
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
