import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, type Session } from "./api";
import { LANGUAGES, setLanguage } from "./i18n";
import { Deroulant, itemMenuActif, itemMenuBase, itemMenuRepos } from "./ui";

const itemMenu = (actif = false) => `${itemMenuBase} ${actif ? itemMenuActif : itemMenuRepos}`;

/** Top bar: brand (back to the dashboard), three sections (Live, Content, Distribution), a central
 *  search field (« / » or Ctrl/Cmd+K), then the live indicator, the settings menu and the account
 *  menu. Under 768 px: logo, a compact live pill, a search icon and a burger opening a side drawer
 *  (search field, the same sections grouped, account at the bottom). */
export default function TopBar({ session, onLogout, onSwitchMosque }: {
  session: Session; onLogout: () => void; onSwitchMosque?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const mosque = session.mosque!;
  const perms = session.user.permissions;
  const peut = (p?: string) => !p || perms.includes(p);
  // Studio devices: the people who drive the live as administrator or manager (same rule as the server).
  const gereAppareils = peut("live") && session.user.role !== "imam";
  const pending = session.counts?.pending ?? 0;
  const failed = session.counts?.failed ?? 0;

  // Phone menu: closed again on every page change.
  const [menuOuvert, setMenuOuvert] = useState(false);
  const [pageVue, setPageVue] = useState(pathname);
  if (pageVue !== pathname) { setPageVue(pathname); setMenuOuvert(false); }
  const fermer = () => setMenuOuvert(false);
  const boutonMenu = useRef<HTMLButtonElement>(null);
  const boutonFermer = useRef<HTMLButtonElement>(null);
  // Open drawer: page scroll locked, Escape closes, focus moves in and back; widening past the
  // phone breakpoint closes it (the drawer does not exist there).
  useEffect(() => {
    if (!menuOuvert) return;
    const { documentElement: html, body } = document;
    const avant = [html.style.overflow, body.style.overflow];
    html.style.overflow = "hidden";
    body.style.overflow = "hidden";
    const surTouche = (e: KeyboardEvent) => { if (e.key === "Escape") setMenuOuvert(false); };
    const large = window.matchMedia("(min-width: 768px)");
    const surLarge = () => { if (large.matches) setMenuOuvert(false); };
    window.addEventListener("keydown", surTouche);
    large.addEventListener("change", surLarge);
    boutonFermer.current?.focus();
    const retour = boutonMenu.current;
    return () => {
      [html.style.overflow, body.style.overflow] = avant;
      window.removeEventListener("keydown", surTouche);
      large.removeEventListener("change", surLarge);
      retour?.focus({ preventScroll: true });
    };
  }, [menuOuvert]);

  // « On air » only while the mosque broadcasts: a light flag, read again every 20 s.
  const [enDirect, setEnDirect] = useState(false);
  useEffect(() => {
    let actif = true;
    const lire = () => {
      if (document.visibilityState === "hidden") return;
      api.liveIndicator().then((r) => actif && setEnDirect(!!r?.on)).catch(() => {});
    };
    lire();
    const id = window.setInterval(lire, 20_000);
    return () => { actif = false; window.clearInterval(id); };
  }, [mosque.id]);

  // Search: « / » (outside a field) or Ctrl/Cmd+K puts the cursor in the field; on a phone, where
  // the field is hidden, they open the search page.
  const champ = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  useEffect(() => {
    const surTouche = (e: KeyboardEvent) => {
      const cible = e.target as HTMLElement | null;
      const enSaisie = !!cible?.closest?.("input, textarea, select, [contenteditable='true']");
      const raccourciK = e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey) && !e.altKey;
      const barre = e.key === "/" && !enSaisie && !e.ctrlKey && !e.metaKey && !e.altKey;
      if (!raccourciK && !barre) return;
      e.preventDefault();
      const c = champ.current;
      if (c && c.offsetParent !== null) { c.focus(); c.select(); } else navigate("/search");
    };
    window.addEventListener("keydown", surTouche);
    return () => window.removeEventListener("keydown", surTouche);
  }, [navigate]);
  const chercher = (e: FormEvent) => {
    e.preventDefault();
    const texte = q.trim();
    navigate(texte ? `/search?${new URLSearchParams({ q: texte })}` : "/search");
    setQ("");
    champ.current?.blur();
    setMenuOuvert(false);
  };

  const commence = (...prefixes: string[]) => prefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const actifContenus = commence("/khutbas", "/dars", "/tarawih", "/clips", "/sermons");
  const actifReglages = commence("/settings", "/audit", "/users", "/platform", "/demarrage");

  const badges = (avecCompte = true) => (
    <>
      {avecCompte && pending > 0 && (
        <span className="inline-flex min-w-[1.35rem] justify-center rounded-full bg-leaf px-1.5 text-[0.78rem] font-bold text-night">
          {pending}
        </span>
      )}
      {failed > 0 && (
        <span title={t("states.menuFailed")} className="inline-flex min-w-[1.35rem] justify-center rounded-full bg-live px-1.5 text-[0.78rem] font-bold text-white">
          !
        </span>
      )}
    </>
  );

  const rubrique = (actif: boolean) =>
    `flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-[0.95rem] font-semibold whitespace-nowrap no-underline transition-colors ${
      actif ? "bg-leaf/15 text-white" : "text-night-link hover:bg-leaf/20 hover:text-white"
    }`;

  // Settings menu: each entry keeps its own right; without any, no gear at all.
  const reglages: { to: string; cle: string }[] = [
    ...(peut("admin") ? [{ to: "/settings", cle: "settings" }] : []),
    ...(peut("journal") ? [{ to: "/audit", cle: "audit" }] : []),
    ...(session.user.operator && !session.support ? [{ to: "/platform/settings", cle: "platform" }] : []),
  ];

  // Tarawih replays: only once the mosque has some (outside Ramadan, a dead entry otherwise).
  const tarawihReplays = (session.counts?.tarawih ?? 0) > 0;
  const contenus = [
    { to: "/khutbas", cle: "sermons" },
    { to: "/dars", cle: "dars" },
    ...(tarawihReplays ? [{ to: "/tarawih", cle: "tarawih" }] : []),
    { to: "/clips", cle: "clips", badge: true },
  ];

  // Le rôle n'est affiché que s'il apprend quelque chose (souvent le même mot que le nom).
  const roleLabel = t(`role.${session.user.role}`, { defaultValue: session.user.role_label });
  const initiales = session.user.name.split(/\s+/).filter(Boolean).slice(0, 2).map((m) => m[0]).join("").toUpperCase() || "?";

  // The visible pill sits in a 44 px tall box on a phone (touch target) and says « Direct » there.
  const pastille = "inline-flex items-center gap-2 rounded-full bg-live px-3 py-1 text-[0.85rem] font-semibold whitespace-nowrap text-white group-hover:bg-live/85 max-md:h-8 max-md:gap-1.5 max-md:px-2.5 max-md:py-0 max-md:text-[0.8rem]";
  const contenuPastille = (
    <span className={pastille}>
      <span aria-hidden className="size-2 animate-pulse rounded-full bg-live-off motion-reduce:animate-none" />
      <span className="md:hidden">{t("nav.onAirShort")}</span>
      <span className="max-md:hidden">{t("nav.onAir")}</span>
    </span>
  );
  const boitePastille = "group inline-flex shrink-0 items-center no-underline max-md:h-11";
  const indicateur = enDirect && (
    peut("live") ? (
      <Link to="/regie" title={t("nav.onAirOpen")} aria-label={t("nav.onAirOpen")} className={boitePastille}>{contenuPastille}</Link>
    ) : (
      <span role="status" className={boitePastille}>{contenuPastille}</span>
    )
  );

  const icone = "inline-flex size-11 shrink-0 lg:size-10 items-center justify-center rounded-lg border border-white/15 bg-white/5 text-night-ink hover:bg-white/10 hover:text-white";
  const loupe = (
    <svg aria-hidden viewBox="0 0 20 20" className="size-[1.05rem] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.7">
      <circle cx="8.8" cy="8.8" r="5.3" />
      <path d="m12.9 12.9 3.4 3.4" strokeLinecap="round" />
    </svg>
  );

  const langues = LANGUAGES.map((l) => (
    <button key={l.code} type="button" role="menuitemradio" aria-checked={i18n.language === l.code}
      onClick={() => setLanguage(l.code)} className={itemMenu(i18n.language === l.code)}>
      {l.label}
      {i18n.language === l.code && <span aria-hidden className="text-leaf">✓</span>}
    </button>
  ));

  // Phone drawer: the same sections, unfolded under their heading.
  const lienMobile = (to: string, libelle: ReactNode, end = false) => (
    <NavLink key={to} to={to} end={end} onClick={fermer} className={({ isActive }) => `${itemMenu(isActive)} min-h-11 font-semibold`}>
      {libelle}
    </NavLink>
  );
  const groupe = (titre: string, liens: ReactNode[]) =>
    liens.length > 0 && (
      <div className="grid gap-0.5">
        <p className="m-0 px-3 pb-1 text-[0.72rem] font-semibold tracking-[0.12em] text-night-muted uppercase">{titre}</p>
        {liens}
      </div>
    );
  const pastilleMenu = failed > 0 ? "!" : pending > 99 ? "99+" : pending > 0 ? String(pending) : "";

  return (
    <header className="bg-night text-night-ink shadow-[0_1px_0_rgba(0,0,0,0.2)]">
      <div className="mx-auto flex max-w-[1280px] items-center gap-3 px-4 py-2.5 max-md:py-2 lg:gap-4">
        <Link to="/" aria-label={t("nav.home")} className="flex min-w-0 shrink-0 items-center gap-2.5 no-underline">
          <img src="/sada-mark-or.svg" alt="" className="h-[28px] w-auto shrink-0 max-md:h-[26px]" />
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="font-serif text-[1.35rem] leading-none text-white max-[359px]:sr-only">{t("app.name")}</span>
            <span className="truncate text-[0.82rem] text-night-muted max-lg:hidden lg:max-w-[12rem]">· {mosque.name}</span>
          </span>
        </Link>

        <nav aria-label={t("nav.menu")} className="ms-2 hidden items-center gap-1 md:flex">
          {peut("live") && (
            <NavLink to="/regie" className={({ isActive }) => rubrique(isActive)}>{t("nav.direct")}</NavLink>
          )}
          {gereAppareils && (
            <NavLink to="/appareils" className={({ isActive }) => rubrique(isActive)}>{t("nav.devices")}</NavLink>
          )}
          <Deroulant
            classeBouton={`${rubrique(actifContenus)} border-0 bg-transparent ${actifContenus ? "!bg-leaf/15" : ""}`}
            bouton={
              <>
                {t("nav.contents")}
                <svg aria-hidden viewBox="0 0 12 12" className="size-3 opacity-70" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="m3 4.5 3 3 3-3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                {badges()}
              </>
            }
          >
            {contenus.map((c) => (
              <Link key={c.to} to={c.to} role="menuitem" aria-current={commence(c.to) ? "page" : undefined} className={itemMenu(commence(c.to))}>
                {t(`nav.${c.cle}`)}
                {c.badge && <span className="flex gap-1">{badges()}</span>}
              </Link>
            ))}
          </Deroulant>
          {peut("publish") && (
            <NavLink to="/publications" className={({ isActive }) => rubrique(isActive)}>{t("nav.broadcast")}</NavLink>
          )}
        </nav>

        <form role="search" onSubmit={chercher} className="relative mx-auto hidden min-w-0 flex-1 lg:block lg:max-w-[440px]">
          <span className="pointer-events-none absolute inset-y-0 start-3 flex items-center text-night-muted">{loupe}</span>
          <input
            ref={champ}
            type="search"
            dir="auto"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Escape") { setQ(""); e.currentTarget.blur(); } }}
            placeholder={t("nav.searchPlaceholder")}
            aria-label={t("nav.searchShort")}
            aria-keyshortcuts="/ Control+K Meta+K"
            title={t("nav.searchKey")}
            className="peer w-full rounded-lg border border-white/12 bg-white/[0.04] py-2 ps-9 pe-9 text-[0.92rem] text-white placeholder:text-night-muted focus:border-leaf/60 focus:bg-white/[0.07] focus:outline-none [&::-webkit-search-cancel-button]:hidden"
          />
          <kbd aria-hidden className="pointer-events-none absolute inset-y-0 end-2.5 my-auto flex h-5 items-center rounded border border-white/25 px-1.5 font-sans text-[0.72rem] text-night-muted peer-focus:hidden">/</kbd>
        </form>

        <div className="ms-auto flex shrink-0 items-center gap-2 lg:ms-0">
          {indicateur}
          <Link to="/search" aria-label={t("nav.searchShort")} className={`${icone} lg:hidden`}>{loupe}</Link>

          {reglages.length > 0 && (
            <div className="hidden md:block">
              <Deroulant
                aligne="fin"
                label={t("nav.admin")}
                classeBouton={`${icone} ${actifReglages ? "!border-leaf/40 !bg-leaf/15 !text-white" : ""}`}
                bouton={
                  <svg aria-hidden viewBox="0 0 24 24" className="size-[1.15rem]" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                  </svg>
                }
              >
                {reglages.map((r) => (
                  <Link key={r.to} to={r.to} role="menuitem" aria-current={commence(r.to) ? "page" : undefined} className={itemMenu(commence(r.to))}>
                    {t(`nav.${r.cle}`)}
                  </Link>
                ))}
              </Deroulant>
            </div>
          )}

          <div className="hidden md:block">
            <Deroulant
              aligne="fin"
              label={`${t("nav.account")} : ${session.user.name}`}
              classeBouton="inline-flex size-11 lg:size-10 items-center justify-center rounded-full border-2 border-transparent bg-green p-0 text-[0.95rem] font-bold text-white hover:border-leaf/50 aria-expanded:border-leaf/60"
              bouton={initiales}
            >
              <div className="border-b border-white/10 px-3 pt-1.5 pb-2.5">
                <p className="m-0 font-semibold text-white">{session.user.name}</p>
                {roleLabel !== session.user.name && <p className="m-0 text-[0.82rem] text-night-muted">{roleLabel}</p>}
              </div>
              <div role="group" aria-label={t("nav.language")} className="border-b border-white/10 py-1">
                <p className="m-0 px-3 pt-1 pb-0.5 text-[0.72rem] font-semibold tracking-[0.12em] text-night-muted uppercase">{t("nav.language")}</p>
                {langues}
              </div>
              <div className="pt-1">
                {onSwitchMosque && (
                  <button type="button" role="menuitem" onClick={onSwitchMosque} className={itemMenu()}>{t("picker.switch")}</button>
                )}
                <button type="button" role="menuitem" onClick={onLogout} className={itemMenu()}>{t("nav.logout")}</button>
              </div>
            </Deroulant>
          </div>

          <button
            ref={boutonMenu}
            type="button"
            aria-expanded={menuOuvert}
            aria-controls="menu-principal"
            aria-label={pending > 0 ? t("nav.menuClips", { count: pending }) : t("nav.menu")}
            title={failed > 0 ? t("states.menuFailed") : undefined}
            onClick={() => setMenuOuvert((o) => !o)}
            className={`${icone} relative cursor-pointer md:hidden`}
          >
            <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 5h14M3 10h14M3 15h14" />
            </svg>
            {pastilleMenu && (
              <span aria-hidden className={`absolute -top-1.5 -end-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[0.7rem] leading-none font-bold ring-2 ring-night ${failed > 0 ? "bg-live text-white" : "bg-leaf text-night"}`}>
                {pastilleMenu}
              </span>
            )}
          </button>
        </div>
      </div>

      <div inert={!menuOuvert} className={`fixed inset-0 z-50 md:hidden ${menuOuvert ? "" : "pointer-events-none"}`}>
        <div aria-hidden onClick={fermer}
          className={`absolute inset-0 bg-black/60 transition-opacity duration-200 motion-reduce:transition-none ${menuOuvert ? "opacity-100" : "opacity-0"}`} />
        <div id="menu-principal" role="dialog" aria-modal="true" aria-label={t("nav.menu")}
          className={`absolute inset-y-0 end-0 flex max-h-dvh w-[min(22rem,88vw)] flex-col bg-night text-night-ink shadow-[0_0_40px_rgba(0,0,0,0.45)] duration-200 ease-out motion-reduce:transition-none ${
            // Visibility flips at once on opening (focus can move in), at the end on closing.
            menuOuvert ? "visible translate-x-0 transition-[translate]" : "invisible translate-x-full transition-[translate,visibility] rtl:-translate-x-full"}`}>
          <div className="flex min-h-[60px] items-center gap-3 border-b border-white/10 py-2 ps-4 pe-4">
            <img src="/sada-mark-or.svg" alt="" className="h-[24px] w-auto shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block font-serif text-[1.15rem] leading-tight text-white">{t("app.name")}</span>
              <span className="block text-[0.78rem] leading-snug text-night-muted">{mosque.name}</span>
            </span>
            <button ref={boutonFermer} type="button" onClick={fermer} aria-label={t("nav.closeMenu")} className={`${icone} cursor-pointer`}>
              <svg aria-hidden="true" viewBox="0 0 20 20" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M5 5l10 10M15 5L5 15" />
              </svg>
            </button>
          </div>

          <form role="search" onSubmit={chercher} className="relative px-4 pt-3 pb-1">
            <span className="pointer-events-none absolute inset-y-0 start-7 flex items-center pt-2 text-night-muted">{loupe}</span>
            <input
              type="search"
              dir="auto"
              enterKeyHint="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("nav.searchPlaceholder")}
              aria-label={t("nav.searchShort")}
              className="min-h-11 w-full rounded-lg border border-white/12 bg-white/[0.04] ps-9 pe-3 text-[1rem] text-white placeholder:text-night-muted focus:border-leaf/60 focus:bg-white/[0.07] focus:outline-none [&::-webkit-search-cancel-button]:hidden"
            />
          </form>

          <nav aria-label={t("nav.menu")} className="grid min-h-0 flex-1 content-start gap-4 overflow-y-auto overscroll-contain px-3 pt-3 pb-4">
            {lienMobile("/", t("nav.dashboard"), true)}
            {groupe(t("nav.direct"), [
              ...(peut("live") ? [lienMobile("/regie", t("nav.regie"))] : []),
              ...(gereAppareils ? [lienMobile("/appareils", t("nav.devices"))] : []),
            ])}
            {groupe(t("nav.contents"), contenus.map((c) =>
              lienMobile(c.to, <>{t(`nav.${c.cle}`)}{c.badge && <span className="flex gap-1">{badges()}</span>}</>)))}
            {groupe(t("nav.broadcast"), peut("publish") ? [lienMobile("/publications", t("nav.publications"))] : [])}
            {groupe(t("nav.admin"), reglages.map((r) => lienMobile(r.to, t(`nav.${r.cle}`))))}
          </nav>

          <div className="grid gap-3 border-t border-white/10 bg-black/15 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
            <div className="flex items-center gap-3">
              <span aria-hidden className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-green text-[0.9rem] font-bold text-white">{initiales}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-semibold break-words text-white">{session.user.name}</span>
                {roleLabel !== session.user.name && <span className="block text-[0.8rem] text-night-muted">{roleLabel}</span>}
              </span>
              <div role="group" aria-label={t("nav.language")} className="inline-flex shrink-0 rounded-lg border border-white/15 bg-white/5 p-0.5">
                {LANGUAGES.map((l) => {
                  const actif = i18n.language === l.code;
                  return (
                    <button key={l.code} type="button" lang={l.code} aria-pressed={actif} aria-label={l.label} title={l.label}
                      onClick={() => setLanguage(l.code)}
                      className={`min-h-11 min-w-11 cursor-pointer rounded-md border-0 px-2 text-[0.82rem] font-bold tracking-wide uppercase ${
                        actif ? "bg-leaf text-night" : "bg-transparent text-night-link hover:text-white"}`}>
                      {l.code}
                    </button>
                  );
                })}
              </div>
            </div>
            {onSwitchMosque && (
              <button type="button" onClick={() => { fermer(); onSwitchMosque(); }} className={`${itemMenu()} min-h-11 font-semibold`}>
                {t("picker.switch")}
              </button>
            )}
            <button type="button" onClick={onLogout}
              className="flex min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-white/15 bg-transparent px-3 text-[0.95rem] font-semibold text-night-link hover:border-live/60 hover:bg-live/15 hover:text-white">
              <svg aria-hidden viewBox="0 0 20 20" className="size-[1.05rem] rtl:-scale-x-100" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M13 6l4 4-4 4M17 10H8" />
              </svg>
              {t("nav.logout")}
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
