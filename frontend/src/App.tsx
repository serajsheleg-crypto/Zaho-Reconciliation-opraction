import { lazy, Suspense, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import DbBanner from "./components/DbBanner";
import Layout from "./components/Layout";
import { Loading, ToastProvider } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import { I18nProvider } from "./lib/i18n";
import Login from "./pages/Login";

const Overview = lazy(() => import("./pages/Overview"));
const NewRun = lazy(() => import("./pages/NewRun"));
const Runs = lazy(() => import("./pages/Runs"));
const RunDetail = lazy(() => import("./pages/RunDetail"));
const Exceptions = lazy(() => import("./pages/Exceptions"));
const UsersPage = lazy(() => import("./pages/Users"));
const Audit = lazy(() => import("./pages/Audit"));
const System = lazy(() => import("./pages/System"));
const MatchingSettings = lazy(() => import("./pages/MatchingSettings"));
const Companies = lazy(() => import("./pages/Companies"));
const TemplateEditor = lazy(() => import("./pages/TemplateEditor"));
const Account = lazy(() => import("./pages/Account"));

function Guard({ children, admin, edit }: { children: ReactNode; admin?: boolean; edit?: boolean }) {
  const { user, ready, can } = useAuth();
  const loc = useLocation();
  if (!ready) return <Loading />;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if ((admin && !can("admin")) || (edit && !can("edit"))) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function Routed() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Guard>
            <Layout />
          </Guard>
        }
      >
        <Route index element={<Page><Overview /></Page>} />
        <Route path="new" element={<Guard edit><Page><NewRun /></Page></Guard>} />
        <Route path="runs" element={<Page><Runs /></Page>} />
        <Route path="runs/:id" element={<Page><RunDetail /></Page>} />
        <Route path="exceptions" element={<Page><Exceptions /></Page>} />
        <Route path="users" element={<Guard admin><Page><UsersPage /></Page></Guard>} />
        <Route path="audit" element={<Guard admin><Page><Audit /></Page></Guard>} />
        <Route path="settings" element={<Page><MatchingSettings /></Page>} />
        <Route path="companies" element={<Page><Companies /></Page>} />
        <Route path="companies/:cid/templates/:tid" element={<Guard admin><Page><TemplateEditor /></Page></Guard>} />
        <Route path="system" element={<Page><System /></Page>} />
        <Route path="account" element={<Page><Account /></Page>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Loading />}>{children}</Suspense>;
}

export default function App() {
  return (
    <I18nProvider>
      <AuthProvider>
        <ToastProvider>
          <BrowserRouter>
            <Routed />
          </BrowserRouter>
          <DbBanner />
        </ToastProvider>
      </AuthProvider>
    </I18nProvider>
  );
}
