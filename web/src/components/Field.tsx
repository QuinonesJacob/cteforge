import type { ReactNode } from "react";

interface Props {
  label: ReactNode;
  htmlFor: string;
  hint?: ReactNode;
  required?: boolean;
  error?: string | null;
  children: ReactNode;
  className?: string;
}

/** Label + control + hint/error, wired with ids for screen readers. */
export function Field({ label, htmlFor, hint, required, error, children, className = "" }: Props) {
  return (
    <div className={`field ${error ? "field-invalid" : ""} ${className}`.trim()}>
      <label htmlFor={htmlFor} className="field-label">
        {label}
        {required && (
          <span className="req" aria-label="required" title="Required">
            {" "}
            *
          </span>
        )}
      </label>
      {children}
      {hint && (
        <p className="field-hint" id={`${htmlFor}-hint`}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field-error" id={`${htmlFor}-error`} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
