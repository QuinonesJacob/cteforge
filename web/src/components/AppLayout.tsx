import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AppHeader } from "./AppHeader";
import { ErrorBoundary } from "./ErrorBoundary";
import { SideNav } from "./SideNav";
import { ToastViewport } from "./ToastViewport";

/** Sidebar + top bar + page (ADR 0029). */
export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  return (
    <div className="shell">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <SideNav />
      <div className="shell-main">
        <AppHeader />
        <main id="main" className="main" tabIndex={-1}>
          <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>
        </main>
      </div>
      <ToastViewport />
    </div>
  );
}
