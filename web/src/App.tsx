import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, onUnauthorized, type Session } from "./api";
import { LANGUAGES, setLanguage } from "./i18n";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Sermons from "./pages/Sermons";
import Clips from "./pages/Clips";
import Publications from "./pages/Publications";
import Settings from "./pages/Settings";
import Users from "./pages/Users";
import Audit from "./pages/Audit";

let sessionCourante: Session | null = null;
export const currentSession = () => sessionCourante;

export default function App() {
  const { t, i18n } = useTranslation();
  const [session, setSession] = useState<Session | null>(null);
  const [pret, setPret] = useState(false);

  useEffect(() => {
    api.session()
      .then((s) => { sessionCourante = s; setSession(s); })
      .catch(() => { sessionCourante = null; setSession(null); })
      .finally(() => setPret(true));
    onUnauthorized(() => { sessionCourante = null; setSession(null); });
  }, []);

  if (!pret) return <p className="px-4 py-16 text-center text-muted">{t("common.loading")}</p>;
  if (!session) return <Login onLogin={(s) => { sessionCourante = s; setSession(s); }} />;

  const lien = (to: string, cle: string, perm?: string) =>
    perm && !session.user.permissions.includes(perm) ? null : (
      <NavLink
        key={to}
        to={to}
        end={to === "/"}
        className={({ isActive }) =>
          `rounded-lg px-2.5 py-1.5 text-[0.95rem] font-semibold no-underline transition-colors ${
            isActive ? "bg-leaf/20 text-white" : "text-night-ink hover:bg-white/10 hover:text-white"
          }`
        }
      >
        {t(`nav.${cle}`)}
      </NavLink>
    );

  const deconnexion = async (e: React.MouseEvent) => {
    e.preventDefault();
    await fetch("/api/v1/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
    window.location.href = "/logout";
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 bg-night text-night-ink shadow-[0_1px_0_rgba(0,0,0,0.2)]">
        <div className="mx-auto flex max-w-[1180px] flex-nowrap items-center gap-3 px-4 py-2.5">
          <div className="flex shrink-0 items-center gap-2.5">
            {session.mosque.logo && (
              <img src={session.mosque.logo} alt="" className="size-8 rounded-full border-2 border-leaf object-cover" />
            )}
            <span className="font-serif text-[1.35rem] leading-none text-white">{session.mosque.name}</span>
            <span className="hidden text-[0.72rem] tracking-[0.14em] text-night-muted uppercase lg:inline">
              {t("app.office")}
            </span>
          </div>
          <nav className="ml-auto flex flex-nowrap items-center gap-0.5 overflow-x-auto">
            {lien("/", "dashboard")}
            {lien("/sermons", "sermons")}
            {lien("/clips", "clips")}
            {lien("/publications", "publications", "publish")}
            {lien("/settings", "settings", "admin")}
            {lien("/users", "users", "admin")}
            {lien("/audit", "audit", "journal")}
          </nav>
          <div className="flex shrink-0 items-center gap-2.5 border-l border-white/15 pl-3">
            <select
              aria-label="Langue"
              value={i18n.language}
              onChange={(e) => setLanguage(e.target.value)}
              className="rounded-md border border-white/20 bg-white/5 px-1.5 py-1 text-[0.82rem] text-night-ink"
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code} className="text-ink">{l.label}</option>
              ))}
            </select>
            <span className="text-[0.85rem] text-night-muted">{session.user.name}</span>
            <a
              href="/logout"
              onClick={deconnexion}
              className="rounded-lg border border-white/20 px-2.5 py-1 text-[0.85rem] font-semibold text-night-ink no-underline hover:bg-white/10"
            >
              {t("nav.logout")}
            </a>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1100px] px-4 pt-5.5 pb-15">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sermons" element={<Sermons />} />
          <Route path="/sermons/:id" element={<Sermons />} />
          <Route path="/clips" element={<Clips />} />
          <Route path="/publications" element={<Publications />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/users" element={<Users />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<p className="py-16 text-center text-muted">404</p>} />
        </Routes>
      </main>
    </div>
  );
}
