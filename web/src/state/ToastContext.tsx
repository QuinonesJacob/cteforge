// Toasts for errors and confirmations. Errors carry ApiError issues when present.
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, errorMessage } from "../api/client";
import type { Issue } from "../api/types";

export type ToastKind = "error" | "success" | "info" | "warning";

export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  message?: string;
  issues?: Issue[];
}

interface ToastApi {
  toasts: Toast[];
  push: (toast: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
  error: (err: unknown, title?: string) => void;
  success: (title: string, message?: string) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);

  const push = useCallback(
    (toast: Omit<Toast, "id">) => {
      const id = next.current++;
      setToasts((ts) => [...ts.slice(-4), { ...toast, id }]);
      const ms = toast.kind === "error" ? 10_000 : 5_000;
      window.setTimeout(() => dismiss(id), ms);
    },
    [dismiss],
  );

  const error = useCallback(
    (err: unknown, title = "Something went wrong") => {
      if (err instanceof DOMException && err.name === "AbortError") return;
      const issues = err instanceof ApiError ? err.issues : undefined;
      const heading = err instanceof ApiError && err.type !== "HTTPError" ? `${title} (${err.type})` : title;
      push({ kind: "error", title: heading, message: errorMessage(err), issues });
    },
    [push],
  );

  const success = useCallback((title: string, message?: string) => push({ kind: "success", title, message }), [push]);

  const api = useMemo(() => ({ toasts, push, dismiss, error, success }), [toasts, push, dismiss, error, success]);
  return <ToastCtx.Provider value={api}>{children}</ToastCtx.Provider>;
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
