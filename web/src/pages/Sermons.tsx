import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Pill, Segmented, inputClass } from "../ui";

/** Liste des khutbas : regroupées par mois, une ligne lisible par khutba (date, passage, imam, thème).
 *  Seul ce qui demande une action ressort : les clips à valider. Toute la ligne ouvre la fiche.
 *  La recherche, le filtre et les pages sont faits par le serveur : une mosquée accumule des années
 *  de khutbas, la page ne doit pas charger l'archive entière pour en afficher vingt.
 *  Mise en page : un en-tête (titre + compte), une seule barre d'outils (recherche + filtre),
 *  puis une seule surface continue où les mois sont des intercalaires — pas une carte par mois. */

const TAILLE = 20; // khutbas par page

/** Date ISO (AAAA-MM-JJ) prise à midi, pour qu'aucun fuseau ne fasse glisser le jour. */
function jour(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const majuscule = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export default function Sermons() {
  const { t, i18n } = useTranslation();
  const [saisie, setSaisie] = useState("");
  const [recherche, setRecherche] = useState("");   // version retardée de la saisie
  const [filtre, setFiltre] = useState("toutes");
  const [mois, setMois] = useState("");              // AAAA-MM, choisi dans les mois existants
  const [moisDispos, setMoisDispos] = useState<{ mois: string; n: number }[]>([]);
  const [items, setItems] = useState<any[] | null>(null);
  const [total, setTotal] = useState(0);
  const [enAttente, setEnAttente] = useState(0);
  const [charge, setCharge] = useState(false);

  // Les mois proposés sont ceux qui contiennent réellement des khutbas, avec leur compte.
  useEffect(() => {
    api.sermonsMois().then((r) => setMoisDispos(r.items)).catch(() => setMoisDispos([]));
  }, []);

  // On n'interroge pas le serveur à chaque lettre.
  useEffect(() => {
    const id = setTimeout(() => setRecherche(saisie.trim()), 300);
    return () => clearTimeout(id);
  }, [saisie]);

  // Première page : à chaque changement de recherche ou de filtre.
  useEffect(() => {
    let vivant = true;
    setCharge(true);
    setItems(null);
    api.sermons({ q: recherche, mois, pending: filtre === "a_valider", limit: TAILLE })
      .then((r) => {
        if (!vivant) return;
        setItems(r.items);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .catch(() => vivant && setItems([]))
      .finally(() => vivant && setCharge(false));
    return () => { vivant = false; };
  }, [recherche, mois, filtre]);

  /** Page suivante : les khutbas s'ajoutent à la suite, elles ne remplacent pas. */
  const plus = () => {
    setCharge(true);
    api.sermons({ q: recherche, mois, pending: filtre === "a_valider", limit: TAILLE, offset: items?.length ?? 0 })
      .then((r) => {
        setItems((v) => [...(v ?? []), ...r.items]);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .finally(() => setCharge(false));
  };

  // Regroupement par mois, dans l'ordre reçu du serveur.
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
      {/* En-tête : le titre, puis ce qu'on regarde en un chiffre. Une seule ligne de lecture. */}
      <div className="grid gap-4">
        <header className="grid gap-1">
          <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("sermons.title")}</h1>
          {/* Hauteur réservée : le titre ne saute pas quand le compte disparaît pendant un chargement. */}
          <p className="m-0 min-h-[1.45rem] text-[0.95rem] text-muted">
            {items ? (
              <>
                {/* Les onglets portent déjà le compte « à valider » : le rappeler ici serait redondant. */}
                {recherche ? t("sermons.searchSummary", { n: total, q: recherche }) : t("sermons.summary", { n: total })}
                {filtre === "a_valider" && <span className="text-warn"> · {t("sermons.pendingTotal", { n: total })}</span>}
              </>
            ) : (
              <span aria-hidden className="block h-[0.75rem] w-40 animate-pulse rounded-full bg-paper-2/70" />
            )}
          </p>
        </header>

        {/* Barre d'outils : chercher et filtrer sont le même geste, donc la même ligne. */}
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

          {/* Période : les mois où la mosquée a réellement prêché, pas un calendrier vide. */}
          <div className="w-[11.5rem] shrink-0">
            <select
              className={`${inputClass} h-11 w-full cursor-pointer text-[0.92rem]`}
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

          <div className="shrink-0 [&>div]:mb-0">
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
        /* Chargement : la même silhouette que les lignes, pour que rien ne saute à l'arrivée. */
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
        /* Une seule surface : les mois sont des intercalaires, pas des cartes empilées. */
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
                    <LigneKhutba k={k} langue={i18n.language} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {/* La suite ne se devine pas : le serveur a dit combien il en reste. */}
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

/** Une khutba : la date en marge, détachée par un filet ; passage · imam, puis le thème ;
 *  à droite ce qui attend une action (clips à valider), sinon le nombre de clips. */
function LigneKhutba({ k, langue }: { k: any; langue: string }) {
  const { t } = useTranslation();
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
        {pending > 0 && <Pill statut="a_valider" label={t("sermons.pendingCount", { n: pending })} />}
        <span className="hidden whitespace-nowrap text-[0.82rem] tabular-nums text-muted sm:inline">
          {t("sermons.detail.clipCount", { n: clips })}
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
