import { useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router";
import {
  ArrowRight,
  Banknote,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Provider, useWorkspace } from "./workspace";
import { API } from "./lib";
import Overview from "./pages/Overview";
import Applicants from "./pages/Applicants";
import Policies from "./pages/Policies";
function SignIn() {
  const { setToken, authError } = useWorkspace(),
    [draft, setDraft] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <main className="login">
      <div className="login-card">
        <div className="brand">
          <Banknote />
          clickpe
        </div>
        <h1>Operations workspace</h1>
        <p>
          Sign in to import applicants, review matches and manage lender
          policies.
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const r = await fetch(`${API}/api/dashboard`, {
                headers: { "X-Operator-Token": draft.trim() },
              });
              if (!r.ok)
                throw new Error(
                  r.status === 401
                    ? "That operator token was not accepted."
                    : "The workspace is unavailable. Try again shortly.",
                );
              setToken(draft.trim());
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label htmlFor="token">Operator token</label>
          <input
            id="token"
            type="password"
            required
            autoComplete="current-password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="button primary" disabled={busy}>
            {busy ? "Signing in…" : "Open workspace"}
            <ArrowRight size={16} />
          </button>
        </form>
        {(error || authError) && (
          <p className="alert" role="alert">
            {error || authError}
          </p>
        )}
        <small>Your session is kept in this browser tab.</small>
      </div>
    </main>
  );
}
function Shell() {
  const { token, setToken, error, refresh, job } = useWorkspace(),
    location = useLocation();
  if (!token) return <SignIn />;
  const current =
    location.pathname === "/policies"
      ? "Policies"
      : location.pathname === "/applicants"
        ? "Applicants"
        : "Overview";
  return (
    <div className="shell">
      <aside className="sidebar">
        <Link className="brand" to="/overview">
          <Banknote />
          clickpe
        </Link>
        <span className="workspace-label">OPERATIONS</span>
        <nav aria-label="Workspace">
          <NavLink to="/overview">
            <LayoutDashboard size={19} />
            Overview
          </NavLink>
          <NavLink to="/applicants">
            <Users size={19} />
            Applicants
          </NavLink>
          <NavLink to="/policies">
            <ShieldCheck size={19} />
            Policies
          </NavLink>
          <button
            className="mobile-signout"
            aria-label="Sign out"
            onClick={() => setToken("")}
          >
            <LogOut size={16} />
          </button>
        </nav>
        <div className="sidebar-foot">
          <span>Operator workspace</span>
          <button onClick={() => setToken("")}>
            <LogOut size={17} />
            Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span>
            Workspace <span className="muted">/</span>{" "}
            <strong>{current}</strong>
          </span>
          <button
            className="icon-button"
            aria-label="Refresh workspace"
            onClick={() => void refresh()}
          >
            <RefreshCw size={17} />
          </button>
        </header>
        <main className="content">
          {error && (
            <div role="alert" className="alert">
              {error}
              <button onClick={() => void refresh()}>Try again</button>
            </div>
          )}
          {job && current !== "Overview" && (
            <Link className="job-link" to="/overview">
              {job.phase === "ready"
                ? "Your applicants are ready"
                : job.phase === "failed"
                  ? "An upload needs attention"
                  : `Processing ${job.filename}`}
              <ArrowRight size={16} />
            </Link>
          )}
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/applicants" element={<Applicants />} />
            <Route path="/policies" element={<Policies />} />
            <Route
              path="*"
              element={
                <div className="empty">
                  <h1>Page not found</h1>
                  <Link to="/overview">Return to overview</Link>
                </div>
              }
            />
          </Routes>
          <footer>ClickPe · Eligibility screening workspace</footer>
        </main>
      </div>
    </div>
  );
}
export default function App() {
  return (
    <BrowserRouter>
      <Provider>
        <Shell />
      </Provider>
    </BrowserRouter>
  );
}
