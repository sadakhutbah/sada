import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Pill, Segmented, inputClass } from "../ui";

/** Khutba list: grouped by month, one readable row per khutba (date, passage, imam, theme).
 *  Only what needs action stands out: clips to review. The whole row opens the sheet.
 *  Search, filter and paging are done by the server: a mosque accumulates years
 *  of khutbas, the page must not load the whole archive to show twenty.
 *  Layout: a header (title + count), a single toolbar (search + filter),
 *  then a single continuous surface where months are dividers — not one card per month. */

const TAILLE = 20; // khutbas per page

/** ISO date (YYYY-MM-DD) taken at noon, so that no timezone makes the day slip. */
function jour(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const majuscule = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Page Khutbas : uniquement les khutbas (les dars enregistrés sont sur la page Dars). */
export default function Sermons() {
  return <ListeSeances genre="khutba" />;
}

/** Liste des séances enregistrées d'une nature (`genre`, transmis tel quel à l'API : "khutba",
 *  "dars,conference"…). `textes` : espace i18n dont les phrases remplacent celles des khutbas
 *  (accords, « aucune khutba »…) ; `titre` faux quand la page qui l'accueille a déjà le sien. */
export function ListeSeances({ genre, textes = "sermons", titre = true }: { genre: string; textes?: string; titre?: boolean }) {
  const { t: tBase, i18n } = useTranslation();
  const t = ((cle: string, o?: any) =>
    cle.startsWith("sermons.") && i18n.exists(`${textes}.${cle.slice(8)}`, o) ? tBase(`${textes}.${cle.slice(8)}` as any, o) : tBase(cle as any, o)) as unknown as typeof tBase;
  const [saisie, setSaisie] = useState("");
  const [recherche, setRecherche] = useState("");   // debounced version of the input
  const [filtre, setFiltre] = useState("toutes");
  const [mois, setMois] = useState("");              // YYYY-MM, picked from the existing months
  const [moisDispos, setMoisDispos] = useState<{ mois: string; n: number }[]>([]);
  const [items, setItems] = useState<any[] | null>(null);
  const [total, setTotal] = useState(0);
  const [enAttente, setEnAttente] = useState(0);
  const [charge, setCharge] = useState(false);

  // The offered months are those that actually contain khutbas, with their count.
  useEffect(() => {
    api.sermonsMois(genre).then((r) => setMoisDispos(r.items)).catch(() => setMoisDispos([]));
  }, [genre]);

  // The server is not queried on every keystroke.
  useEffect(() => {
    const id = setTimeout(() => setRecherche(saisie.trim()), 300);
    return () => clearTimeout(id);
  }, [saisie]);

  // First page: on every change of search or filter.
  const requete = JSON.stringify([recherche, mois, filtre, genre]);
  const [requeteVue, setRequeteVue] = useState<string | null>(null);
  if (requeteVue !== requete) { setRequeteVue(requete); setCharge(true); setItems(null); }

  useEffect(() => {
    let vivant = true;
    api.sermons({ q: recherche, mois, pending: filtre === "a_valider", limit: TAILLE, kind: genre })
      .then((r) => {
        if (!vivant) return;
        setItems(r.items);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .catch(() => vivant && setItems([]))
      .finally(() => vivant && setCharge(false));
    return () => { vivant = false; };
  }, [recherche, mois, filtre, genre]);

  /** Next page: khutbas are appended, they do not replace. */
  const plus = () => {
    setCharge(true);
    api.sermons({ q: recherche, mois, pending: filtre === "a_valider", limit: TAILLE, offset: items?.length ?? 0, kind: genre })
      .then((r) => {
        setItems((v) => [...(v ?? []), ...r.items]);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .finally(() => setCharge(false));
  };

  // Grouping by month, in the order received from the server.
  const groupes = new Map<string, any[]>();
  for (const k of items ?? []) {
    const cle = String(k.date ?? "").slice(0, 7);
    groupes.set(cle, [...(groupes.get(cle) ?? []), k]);
  }
  const titreMois = (cle: string) => {
    const d = jour(`${cle}-01`);
    return d ? majuscule(new Intl.DateTimeFormat(i18n.language, { month: "long", year: "numeric" }).format(d)) : "—";
  };

  const filtreActif = Boolean(recherche) || Boolean(mois) || filtre !== "toutes";

  return (
    <div className="grid gap-5">
      {/* Header: the title, then what we are looking at in one figure. A single reading line. */}
      <div className="grid gap-4">
        <header className="grid gap-1">
          {titre && <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("sermons.title")}</h1>}
          {/* Reserved height: the title does not jump when the count disappears during a load. */}
          <p className="m-0 min-h-[1.45rem] text-[0.95rem] text-muted">
            {items ? (
              <>
                {/* The tabs already carry the “to review” count: repeating it here would be redundant. */}
                {recherche ? t("sermons.searchSummary", { count: total, q: recherche }) : t("sermons.summary", { count: total })}
                {filtre === "a_valider" && <span className="text-warn"> · {t("sermons.pendingTotal", { count: total })}</span>}
              </>
            ) : (
              <span aria-hidden className="block h-[0.75rem] w-40 animate-pulse rounded-full bg-paper-2/70" />
            )}
          </p>
        </header>

        {/* Toolbar: searching and filtering are the same gesture, hence the same line. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5">
          <div className="relative min-w-[16rem] flex-1">
            <svg
              aria-hidden
              viewBox="0 0 20 20"
              className="pointer-events-none absolute top-1/2 left-3.5 size-[1.05rem] -translate-y-1/2 text-muted"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
            >
              <circle cx="8.8" cy="8.8" r="5.3" />
              <path d="m12.9 12.9 3.4 3.4" strokeLinecap="round" />
            </svg>
            <input
              type="search"
              className={`${inputClass} h-11 w-full pr-11 pl-10 placeholder:text-muted/80 [&::-webkit-search-cancel-button]:appearance-none`}
              placeholder={t("sermons.search")}
              value={saisie}
              onChange={(e) => setSaisie(e.target.value)}
              aria-label={t("sermons.search")}
            />
            {saisie && (
              <button
                type="button"
                onClick={() => setSaisie("")}
                aria-label={t("sermons.clearSearch")}
                className="absolute top-1/2 right-2.5 grid size-6 -translate-y-1/2 cursor-pointer place-items-center rounded-full border-0 bg-paper-2 text-muted transition-colors hover:bg-line hover:text-ink"
              >
                <span aria-hidden className="text-[0.95rem] leading-none">×</span>
              </button>
            )}
          </div>

          {/* Period: the months when the mosque actually preached, not an empty calendar. */}
          <div className="w-[11.5rem] shrink-0">
            <select
              className={`${inputClass} h-11 w-full cursor-pointer select-chevron text-[0.92rem]`}
              value={mois}
              onChange={(e) => setMois(e.target.value)}
              aria-label={t("sermons.month")}
            >
              <option value="">{t("sermons.allMonths")}</option>
              {moisDispos.map((m) => (
                <option key={m.mois} value={m.mois}>{titreMois(m.mois)}</option>
              ))}
            </select>
          </div>

          <div className="max-w-full min-w-0 shrink-0 [&>div]:mb-0">
            <Segmented
              valeur={filtre}
              onChange={setFiltre}
              items={[
                { cle: "toutes", label: t("sermons.filterAll"), compte: enAttente ? total : undefined },
                { cle: "a_valider", label: t("sermons.pending"), compte: enAttente, eteint: enAttente === 0 },
              ]}
            />
          </div>
        </div>
      </div>

      {!items ? (
        /* Loading: the same silhouette as the rows, so nothing jumps when they arrive. */
        <div className="grid gap-2" aria-busy="true" aria-label={t("common.loading")}>
          <div className="h-3 w-28 animate-pulse rounded-full bg-paper-2" />
          <div className="divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-white">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-3.5 sm:px-5">
                <div className="grid w-[2.6rem] shrink-0 justify-items-center gap-1.5 border-r border-line pr-3">
                  <div className="h-1.5 w-6 animate-pulse rounded bg-paper-2" />
                  <div className="h-4 w-5 animate-pulse rounded bg-paper-2" />
                  <div className="h-1.5 w-6 animate-pulse rounded bg-paper-2" />
                </div>
                <div className="grid flex-1 gap-2">
                  <div className="h-3 w-[45%] animate-pulse rounded bg-paper-2" />
                  <div className="h-2.5 w-[65%] animate-pulse rounded bg-paper-2/70" />
                </div>
                <div className="hidden h-5 w-16 animate-pulse rounded-full bg-paper-2 sm:block" />
              </div>
            ))}
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-16 text-center">
          <p className="mx-auto max-w-[54ch] text-[1.02rem] leading-relaxed text-muted">
            {recherche ? t("sermons.searchEmpty", { q: recherche })
              : filtre === "a_valider" ? t("sermons.noPending")
                : mois ? t("sermons.emptyMonth") : t("sermons.empty")}
          </p>
          {filtreActif && (
            <Btn variante="ghost" className="mt-4" onClick={() => { setSaisie(""); setMois(""); setFiltre("toutes"); }}>
              {t("sermons.showAll")}
            </Btn>
          )}
        </div>
      ) : (
        /* A single surface: months are dividers, not stacked cards. */
        <div className="overflow-hidden rounded-[10px] border border-line bg-white">
          {[...groupes.entries()].map(([cle, lignes], i) => (
            <section key={cle}>
              <h2
                className={`m-0 flex items-baseline gap-2.5 bg-paper-2/55 px-4 py-2 text-[0.72rem] font-semibold uppercase tracking-[0.14em] text-green sm:px-5 ${
                  i > 0 ? "border-t border-line" : ""
                }`}
              >
                <span>{titreMois(cle)}</span>
                <span className="ml-auto font-normal tracking-normal tabular-nums text-muted">{lignes.length}</span>
              </h2>
              <ul className="m-0 list-none divide-y divide-line/80 p-0">
                {lignes.map((k) => (
                  <li key={k.id}>
                    <LigneKhutba k={k} langue={i18n.language} t={t} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {/* What follows is not guessed: the server said how many remain. */}
      {items && items.length > 0 && items.length < total && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-[0.85rem] tabular-nums text-muted">
            {t("sermons.shown", { n: items.length, total })}
          </span>
          <Btn variante="ghost" disabled={charge} onClick={plus}>
            {charge ? t("common.loading") : t("sermons.loadMore", { n: Math.min(TAILLE, total - items.length) })}
            {!charge && (
              <svg aria-hidden viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M8 3v9.5m0 0L4.2 8.8M8 12.5l3.8-3.7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            )}
          </Btn>
        </div>
      )}
    </div>
  );
}

/** A khutba: the date in the margin, set off by a rule; passage · imam, then the theme;
 *  on the right what awaits an action (clips to review), otherwise the clip count. */
function LigneKhutba({ k, langue, t }: { k: any; langue: string; t: ReturnType<typeof useTranslation>["t"] }) {
  const d = jour(k.date);
  const fmt = (o: Intl.DateTimeFormatOptions) => (d ? new Intl.DateTimeFormat(langue, o).format(d).replace(".", "") : "");
  const pending = Number(k.pending) || 0;
  const clips = Number(k.clips) || 0;

  return (
    <Link
      to={`/khutbas/${k.id}`}
      className="group grid grid-cols-[2.9rem_minmax(0,1fr)_auto] items-center gap-x-4 px-4 py-3 text-inherit no-underline transition-colors hover:bg-paper-2/50 focus-visible:bg-paper-2/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-leaf sm:gap-x-5 sm:px-5"
    >
      <span className="flex h-full flex-col items-center justify-center gap-0.5 border-r border-line/80 pr-3 group-hover:border-green/40">
        <span className="text-[0.6rem] font-semibold uppercase leading-none tracking-[0.12em] text-muted">{fmt({ weekday: "short" })}</span>
        <span className="mt-1 font-serif text-[1.5rem] leading-none tabular-nums text-deeper">{d ? d.getDate() : "—"}</span>
        <span className="mt-1 text-[0.6rem] font-semibold uppercase leading-none tracking-[0.12em] text-muted">{fmt({ month: "short" })}</span>
      </span>

      <span className="min-w-0">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <span className="font-semibold leading-snug text-deep transition-colors group-hover:text-green">{k.label}</span>
          <span className="min-w-0 truncate text-[0.85rem] text-muted">· {k.imam || t("sermons.noImam")}</span>
        </span>
        <span className={`mt-0.5 block truncate text-[0.92rem] leading-snug ${k.theme ? "text-ink/85" : "italic text-muted"}`}>
          {k.theme || t("sermons.noTheme")}
        </span>
      </span>

      <span className="flex shrink-0 items-center justify-self-end gap-3">
        {pending > 0 && <Pill statut="a_valider" label={t("sermons.pendingCount", { count: pending })} />}
        <span className="hidden whitespace-nowrap text-[0.82rem] tabular-nums text-muted sm:inline">
          {t("sermons.detail.clipCount", { count: clips })}
        </span>
        <svg
          aria-hidden
          viewBox="0 0 16 16"
          className="size-4 text-muted/70 transition-transform group-hover:translate-x-0.5 group-hover:text-deep"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
        >
          <path d="M6 3.5 10.5 8 6 12.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
    </Link>
  );
}
