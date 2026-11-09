import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
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
export const useSession = () => sessionCourante;

export default function App() {
  const { t, i18n } = useTranslation();
  const [session, setSession] = useState<Session | null>(null);
  const [pret, setPret] = useState(false);
  const navigate = useNavigate();

  const charger = () => {
    api.session()
      .then((s) => { sessionCourante = s; setSession(s); })
      .catch(() => { sessionCourante = null; setSession(null); })
      .finally(() => setPret(true));
  };

  useEffect(() => {
    charger();
    onUnauthorized(() => { sessionCourante = null; setSession(null); });
  }, []);

  if (!pret) return <p className="vide">{t("common.loading")}</p>;
  if (!session) return <Login onLogin={(s) => { sessionCourante = s; setSession(s); navigate("/"); }} />;

  const lien = (to: string, cle: string, perm?: string) =>
    perm && !session.user.permissions.includes(perm) ? null : (
      <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "on" : "")}>
        {t(`nav.${cle}`)}
      </NavLink>
    );

  return (
    <div className="app">
      <header className="top">
        <div className="mark">
          {session.mosque.logo && <img src={session.mosque.logo} alt="" width={22} height={22} />}
          <strong>{session.mosque.name}</strong>
          <span className="muted">{t("app.office")}</span>
        </div>
        <nav className="nav">
          {lien("/", "dashboard")}
          {lien("/sermons", "sermons")}
          {lien("/clips", "clips")}
          {lien("/publications", "publications", "publish")}
          {lien("/settings", "settings", "admin")}
          {lien("/users", "users", "admin")}
          {lien("/audit", "audit", "journal")}
        </nav>
        <div className="right">
          <select
            aria-label="Langue"
            value={i18n.language}
            onChange={(e) => setLanguage(e.target.value)}
          >
            {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </select>
          <span className="muted">{session.user.name}</span>
          <a className="btn sm ghost" href="/logout">{t("nav.logout")}</a>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/sermons" element={<Sermons />} />
          <Route path="/sermons/:id" element={<Sermons />} />
          <Route path="/clips" element={<Clips />} />
          <Route path="/publications" element={<Publications />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/users" element={<Users />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="*" element={<p className="vide">404</p>} />
        </Routes>
      </main>
    </div>
  );
}
