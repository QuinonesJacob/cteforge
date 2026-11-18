import { Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { Loading } from "./components/Loading";
import { ToastViewport } from "./components/ToastViewport";
import { DefinitionPage } from "./pages/DefinitionPage";
import { ImportPage } from "./pages/ImportPage";
import { JoinMapPage } from "./pages/JoinMapPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LogPage } from "./pages/LogPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProposalPage } from "./pages/ProposalPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignInPage } from "./pages/SignInPage";
import { useAuth } from "./state/AuthContext";

export function App() {
  const { status } = useAuth();
  if (status === "loading")
    return (
      <div className="boot">
        <Loading label="Connecting to cteforge…" />
      </div>
    );
  if (status === "signedOut")
    return (
      <>
        <SignInPage />
        <ToastViewport />
      </>
    );
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<LibraryPage />} />
        <Route path="/definitions/:id" element={<DefinitionPage />} />
        {/* ADR 0026: Explore and the Review list are gone; their links land in the Library. */}
        <Route path="/explore" element={<Navigate to="/" replace />} />
        <Route path="/joins" element={<JoinMapPage />} />
        <Route path="/import" element={<ImportPage />} />
        <Route path="/review" element={<Navigate to="/?review=mine" replace />} />
        <Route path="/review/:id" element={<ProposalPage />} />
        {/* ADR 0029: the audit log is the Log; /audit keeps working. */}
        <Route path="/log" element={<LogPage />} />
        <Route path="/audit" element={<Navigate to="/log" replace />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/:section" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppLayout>
  );
}
