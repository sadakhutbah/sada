import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, onUnauthorized, setCsrf, type Session } from "./api";
import { LANGUAGES, setLanguage } from "./i18n";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Sermons from "./pages/Sermons";
import SermonDetail from "./pages/SermonDetail";
import Habillage from "./pages/Habillage";
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
  // Menu compact sur téléphone : refermé à chaque changement de page.
  const [menuOuvert, setMenuOuvert] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setMenuOuvert(false), [pathname]);

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
          `flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[0.95rem] font-semibold whitespace-nowrap no-underline transition-colors max-md:min-h-11 pointer-coarse:min-h-11 ${
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
        {/* Large écran : une ligne. Tablette : la navigation passe sur une seconde ligne qui défile
            dans son propre conteneur. Téléphone : bouton « Menu » qui déplie la liste. La page ne
            doit jamais être plus large que l'écran (sinon iOS dézoome tout le site). */}
        <div className="mx-auto flex max-w-[1100px] flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
          {/* Brand on two lines: the program, then the mosque below in small text. */}
          <div className="flex min-w-0 items-center gap-2.5">
            {/* Identité produit : la marque منبر ; le logo de la mosquée reste dans les écrans live et les clips. */}
            <img src="/sada-mark-or.svg" alt="" className="h-[26px] w-auto shrink-0" />
            <span className="flex min-w-0 flex-col">
              <span className="font-serif text-[1.3rem] leading-none text-white">{t("app.name")}</span>
              <span className="truncate text-[0.75rem] leading-tight text-night-muted">{session.mosque.name}</span>
            </span>
          </div>
          <button
            type="button"
            aria-expanded={menuOuvert}
            aria-controls="menu-principal"
            onClick={() => setMenuOuvert((o) => !o)}
            className="ml-auto inline-flex min-h-11 shrink-0 cursor-pointer items-center gap-2 rounded-lg border border-white/20 bg-white/5 px-3 text-[0.95rem] font-semibold text-white md:hidden"
          >
            <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              {menuOuvert ? <path d="M5 5l10 10M15 5L5 15" /> : <path d="M3 5h14M3 10h14M3 15h14" />}
            </svg>
            {t("nav.menu")}
            {(session.counts?.pending ?? 0) > 0 && (
              <span className="inline-flex min-w-[1.35rem] justify-center rounded-full bg-leaf px-1.5 text-[0.78rem] font-bold text-night">
                {session.counts?.pending}
              </span>
            )}
          </button>
          <nav
            id="menu-principal"
            className={`${menuOuvert ? "flex" : "hidden"} order-last w-full flex-col gap-0.5 border-t border-white/10 pt-2
              md:flex md:flex-row md:flex-nowrap md:items-center md:overflow-x-auto md:border-t-0 md:pt-0
              min-[1120px]:order-none min-[1120px]:ml-auto min-[1120px]:w-auto min-[1120px]:overflow-visible`}
          >
            {lien("/", "dashboard")}
            {lien("/khutbas", "sermons")}
            {lien("/dars", "dars")}
            {lien("/clips", "clips", undefined, session.counts?.pending ?? 0)}
            {lien("/publications", "publications", "publish")}
            {lien("/audit", "audit", "journal")}
            {lien("/settings", "settings", "admin")}
          </nav>
          <div
            className={`${menuOuvert ? "flex" : "hidden"} order-last w-full flex-wrap items-center gap-x-3 gap-y-2 border-t border-white/10 py-2
              md:order-none md:ml-auto md:flex md:w-auto md:flex-nowrap md:gap-2.5 md:border-t-0 md:py-0
              min-[1120px]:ml-0 min-[1120px]:border-l min-[1120px]:border-white/15 min-[1120px]:pl-3`}
          >
            <select
              aria-label="Langue"
              value={i18n.language}
              onChange={(e) => setLanguage(e.target.value)}
              className="appearance-none rounded-md border border-white/20 bg-white/5 px-1.5 py-1 text-[0.82rem] text-night-ink max-md:min-h-11 max-md:px-3 pointer-coarse:min-h-11"
            >
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code} className="text-ink">{l.label}</option>
              ))}
            </select>
            {/* Le rôle passe en info-bulle sur grand écran : la barre tient sur une ligne même avec un badge. */}
            <span className="text-[0.85rem] whitespace-nowrap text-night-link" title={roleLabel}>{session.user.name}</span>
            {roleLabel !== session.user.name && (
              <span className="text-[0.78rem] whitespace-nowrap text-night-muted min-[1120px]:hidden">{roleLabel}</span>
            )}
            <a
              href="/"
              onClick={deconnexion}
              className="inline-flex items-center text-[0.85rem] whitespace-nowrap text-night-link underline decoration-white/40 underline-offset-4 no-underline hover:text-white max-md:ml-auto max-md:min-h-11 pointer-coarse:min-h-11"
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
          <Route path="/khutbas/:id/habillage" element={<Habillage />} />
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
        {t("app.name")} · {session.mosque.name}
      </footer>
    </div>
  );
}
