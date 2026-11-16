import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";
import { ToastViewport } from "./ToastViewport";
import { TopNav } from "./TopNav";

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <>
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <TopNav />
      <main id="main" className="main" tabIndex={-1}>
        <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>
      </main>
      <ToastViewport />
    </>
  );
}
