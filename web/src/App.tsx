import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, onUnauthorized, setCsrf, type Session } from "./api";
import { LANGUAGES, setLanguage } from "./i18n";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Sermons from "./pages/Sermons";
import SermonDetail from "./pages/SermonDetail";
import Dars from "./pages/Dars";
import Clips from "./pages/Clips";
import ClipDetail from "./pages/ClipDetail";
import Publications from "./pages/Publications";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import Audit from "./pages/Audit";
import Onboarding from "./pages/Onboarding";

let sessionCourante: Session | null = null;
export const currentSession = () => sessionCourante;

export default function App() {
  const { t, i18n } = useTranslation();
  const [session, setSession] = useState<Session | null>(null);
  const [pret, setPret] = useState(false);
  const navigate = useNavigate();
  const [demarragePropose, setDemarragePropose] = useState(false);

  useEffect(() => {
    api.session()
      .then((s) => { sessionCourante = s; setCsrf(s.csrf); setSession(s); })
      .catch(() => { sessionCourante = null; setSession(null); })
      .finally(() => setPret(true));
    onUnauthorized(() => { sessionCourante = null; setSession(null); });
  }, []);

  // First login: the three-step wizard is opened once, then everything lives in Settings.
  useEffect(() => {
    if (!session || demarragePropose) return;
    if (session.mosque?.needs_onboarding && session.user.permissions.includes("admin")) {
      setDemarragePropose(true);
      navigate("/demarrage", { replace: true });
    }
  }, [session, demarragePropose, navigate]);

  if (!pret) return <p className="px-4 py-16 text-center text-muted">{t("common.loading")}</p>;
  if (!session) return <Login onLogin={(s) => { sessionCourante = s; setCsrf(s.csrf); setSession(s); }} />;

  const lien = (to: string, cle: string, perm?: string, compte?: number) =>
    perm && !session.user.permissions.includes(perm) ? null : (
      <NavLink
        key={to}
        to={to}
        end={to === "/"}
        className={({ isActive }) =>
          `flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[0.95rem] font-semibold whitespace-nowrap no-underline transition-colors ${
            isActive ? "bg-leaf/15 text-white" : "text-night-link hover:bg-leaf/20 hover:text-white"
          }`
        }
      >
        {t(`nav.${cle}`)}
        {compte !== null && compte !== undefined && compte > 0 && (
          <span className="inline-flex min-w-[1.35rem] justify-center rounded-full bg-leaf px-1.5 text-[0.78rem] font-bold text-night">
            {compte}
          </span>
        )}
      </NavLink>
    );

  // Sign-out: the server clears the session, the login screen takes over without a reload.
  const deconnexion = async (e: React.MouseEvent) => {
    e.preventDefault();
    await api.logout().catch(() => {});
    sessionCourante = null;
    setCsrf("");
    setSession(null);
  };

  // Le nom du compte et son rôle portent souvent le même mot (« Administrateur ») : le rôle n'est
  // affiché que s'il apprend quelque chose.
  const roleLabel = t(`role.${session.user.role}`, { defaultValue: session.user.role_label });

  return (
    <div className="flex min-h-screen flex-col">
      <header className="bg-night text-night-ink shadow-[0_1px_0_rgba(0,0,0,0.2)]">
        <div className="mx-auto flex max-w-[1100px] flex-nowrap items-center gap-3 px-4 py-2.5">
          {/* Brand on two lines: the program, then the mosque below in small text. */}
          <div className="flex shrink-0 items-center gap-2.5">
            {session.mosque.logo && (
              <img src={session.mosque.logo} alt="" className="size-[34px] shrink-0 rounded-full border-2 border-leaf object-cover" />
            )}
            <span className="flex flex-col">
              <span className="font-serif text-[1.3rem] leading-none text-white">{t("app.name")}</span>
              <span className="text-[0.75rem] leading-tight text-night-muted">{session.mosque.name}</span>
            </span>
          </div>
          <nav className="ml-auto flex flex-nowrap items-center gap-0.5">
            {lien("/", "dashboard")}
            {lien("/khutbas", "sermons")}
            {lien("/dars", "dars")}
            {lien("/clips", "clips", undefined, session.counts?.pending ?? 0)}
            {lien("/publications", "publications", "publish")}
            {lien("/audit", "audit", "journal")}
            {lien("/settings", "settings", "admin")}
          </nav>
          <div className="flex shrink-0 items-center gap-2.5 border-l border-white/15 pl-3">

            <select
              aria-label="Langue"
              value={i18n.language}
              onChange={(e) => setLanguage(e.target.value)}
              className="appearance-none rounded-md border border-white/20 bg-white/5 px-1.5 py-1 text-[0.82rem] text-night-ink"
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code} className="text-ink">{l.label}</option>
              ))}
            </select>
            <span className="text-[0.85rem] whitespace-nowrap text-night-link">{session.user.name}</span>
            {roleLabel !== session.user.name && (
              <span className="text-[0.78rem] whitespace-nowrap text-night-muted">{roleLabel}</span>
            )}
            <a
              href="/"
              onClick={deconnexion}
              className="text-[0.85rem] whitespace-nowrap text-night-link underline decoration-white/40 underline-offset-4 no-underline hover:text-white"
            >
              {t("nav.logout")}
            </a>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1100px] flex-1 px-4 pt-6 pb-16">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/khutbas" element={<Sermons />} />
          <Route path="/dars" element={<Dars />} />
          <Route path="/clips" element={<Clips />} />
          <Route path="/khutbas/:id" element={<SermonDetail />} />
          {/* Old address: already-shared links keep leading to the list. */}
          <Route path="/sermons" element={<Navigate to="/khutbas" replace />} />
          <Route path="/sermons/:id" element={<Navigate to="/khutbas" replace />} />
          <Route path="/clips/:id" element={<ClipDetail />} />
          <Route path="/publications" element={<Publications />} />
          <Route path="/demarrage" element={<Onboarding />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/users" element={<Users />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<p className="py-16 text-center text-muted">404</p>} />
        </Routes>
      </main>
      <footer className="p-6 text-center text-[0.8rem] text-muted">
        {t("app.name")} · {t("footer")} · {session.mosque.name}
      </footer>
    </div>
  );
}
