import { useToast } from "../state/ToastContext";
import { IssueList } from "./IssueList";

export function ToastViewport() {
  const { toasts, dismiss } = useToast();
  return (
    <div className="toasts" aria-live="polite" aria-relevant="additions">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind}`} role={t.kind === "error" ? "alert" : "status"}>
          <div className="toast-main">
            <p className="toast-title">{t.title}</p>
            {t.message && <p className="toast-msg">{t.message}</p>}
            {t.issues && t.issues.length > 0 && <IssueList issues={t.issues.slice(0, 4)} />}
          </div>
          <button type="button" className="icon-btn" aria-label="Dismiss notification" onClick={() => dismiss(t.id)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
