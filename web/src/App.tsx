import { Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/AppLayout";
import { Loading } from "./components/Loading";
import { ToastViewport } from "./components/ToastViewport";
import { AuditPage } from "./pages/AuditPage";
import { DefinitionPage } from "./pages/DefinitionPage";
import { ImportPage } from "./pages/ImportPage";
import { LibraryPage } from "./pages/LibraryPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProposalPage } from "./pages/ProposalPage";
import { ReviewPage } from "./pages/ReviewPage";
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
        <Route path="/import" element={<ImportPage />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/review/:id" element={<ProposalPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppLayout>
  );
}
