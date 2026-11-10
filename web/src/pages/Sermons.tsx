import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Pill, Segmented, inputClass } from "../ui";

/** Liste des khutbas : regroupées par mois, une ligne lisible par khutba (date, passage, imam, thème).
 *  Seul ce qui demande une action ressort : les clips à valider. Toute la ligne ouvre la fiche.
 *  La recherche, le filtre et les pages sont faits par le serveur : une mosquée accumule des années
 *  de khutbas, la page ne doit pas charger l'archive entière pour en afficher vingt. */

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
  const [items, setItems] = useState<any[] | null>(null);
  const [total, setTotal] = useState(0);
  const [enAttente, setEnAttente] = useState(0);
  const [charge, setCharge] = useState(false);

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
    api.sermons({ q: recherche, pending: filtre === "a_valider", limit: TAILLE })
      .then((r) => {
        if (!vivant) return;
        setItems(r.items);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .catch(() => vivant && setItems([]))
      .finally(() => vivant && setCharge(false));
    return () => { vivant = false; };
  }, [recherche, filtre]);

  /** Page suivante : les khutbas s'ajoutent à la suite, elles ne remplacent pas. */
  const plus = () => {
    setCharge(true);
    api.sermons({ q: recherche, pending: filtre === "a_valider", limit: TAILLE, offset: items?.length ?? 0 })
      .then((r) => {
        setItems((v) => [...(v ?? []), ...r.items]);
        setTotal(r.total);
        setEnAttente(r.pending);
      })
      .finally(() => setCharge(false));
  };

  const entete = (
    <div>
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("sermons.title")}</h1>
      {items && (
        <p className="mt-1.5 mb-0 text-[0.95rem] text-muted">
          {recherche ? t("sermons.searchSummary", { n: total, q: recherche }) : t("sermons.summary", { n: total })}
          {enAttente > 0 && (
            <>
              {" · "}
              <span className="font-semibold text-warn">{t("sermons.pendingTotal", { n: enAttente })}</span>
            </>
          )}
        </p>
      )}
    </div>
  );

  if (!items) {
    return (
      <div className="grid gap-6">
        <header className="grid gap-3">{entete}</header>
        <div className="grid gap-2" aria-label={t("common.loading")}>
          {[0, 1, 2].map((i) => <div key={i} className="h-[74px] animate-pulse rounded-[10px] bg-paper-2" />)}
        </div>
      </div>
    );
  }

  // Regroupement par mois, dans l'ordre reçu du serveur.
  const groupes = new Map<string, any[]>();
  for (const k of items) {
    const cle = String(k.date ?? "").slice(0, 7);
    groupes.set(cle, [...(groupes.get(cle) ?? []), k]);
  }
  const titreMois = (cle: string) => {
    const d = jour(`${cle}-01`);
    return d ? majuscule(new Intl.DateTimeFormat(i18n.language, { month: "long", year: "numeric" }).format(d)) : "—";
  };

  return (
    <div className="grid gap-6">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        {entete}
        <Segmented
          valeur={filtre}
          onChange={setFiltre}
          items={[
            { cle: "toutes", label: t("sermons.filterAll"), compte: enAttente ? total : undefined },
            { cle: "a_valider", label: t("sermons.pending"), compte: enAttente, eteint: enAttente === 0 },
          ]}
        />
      </header>

      {/* Recherche : passage, thème, imam ou date. */}
      <div className="relative">
        <input
          type="search"
          className={`${inputClass} w-full py-2.5 pl-10`}
          placeholder={t("sermons.search")}
          value={saisie}
          onChange={(e) => setSaisie(e.target.value)}
          aria-label={t("sermons.search")}
        />
        <span aria-hidden className="pointer-events-none absolute top-1/2 left-3.5 -translate-y-1/2 text-muted">⌕</span>
      </div>

      {items.length === 0 ? (
        <div className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-14 text-center">
          <p className="m-0 text-muted">
            {recherche ? t("sermons.searchEmpty", { q: recherche })
              : filtre === "a_valider" ? t("sermons.noPending") : t("sermons.empty")}
          </p>
        </div>
      ) : (
        [...groupes.entries()].map(([cle, lignes]) => (
          <section key={cle} className="grid gap-2">
            <h2 className="m-0 flex items-baseline gap-2 text-[0.78rem] font-semibold uppercase tracking-[0.12em] text-green">
              {titreMois(cle)}
              <span className="font-normal tracking-normal text-muted tabular-nums">{lignes.length}</span>
            </h2>
            <ul className="m-0 list-none divide-y divide-line overflow-hidden rounded-[10px] border border-line bg-white p-0">
              {lignes.map((k) => (
                <li key={k.id}>
                  <LigneKhutba k={k} langue={i18n.language} />
                </li>
              ))}
            </ul>
          </section>
        ))
      )}

      {/* La suite ne se devine pas : le serveur a dit combien il en reste. */}
      {items.length < total && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-[0.85rem] text-muted tabular-nums">
            {t("sermons.shown", { n: items.length, total })}
          </span>
          <Btn variante="ghost" disabled={charge} onClick={plus}>
            {charge ? t("common.loading") : t("sermons.loadMore", { n: Math.min(TAILLE, total - items.length) })}
          </Btn>
        </div>
      )}
    </div>
  );
}

/** Une khutba : pavé de date, puis passage · imam et thème ; à droite ce qui attend une action. */
function LigneKhutba({ k, langue }: { k: any; langue: string }) {
  const { t } = useTranslation();
  const d = jour(k.date);
  const fmt = (o: Intl.DateTimeFormatOptions) => (d ? new Intl.DateTimeFormat(langue, o).format(d).replace(".", "") : "");
  const pending = Number(k.pending) || 0;

  return (
    <Link
      to={`/khutbas/${k.id}`}
      className="group grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-center gap-4 px-4 py-3.5 text-inherit no-underline transition-colors hover:bg-paper-2/60 sm:px-5"
    >
      <span className="grid justify-items-center rounded-[8px] border border-line bg-paper py-1.5 leading-none">
        <span className="text-[0.64rem] font-semibold uppercase tracking-[0.1em] text-muted">{fmt({ weekday: "short" })}</span>
        <span className="mt-1 font-serif text-[1.65rem] leading-none text-deeper">{d ? d.getDate() : "—"}</span>
        <span className="mt-1 text-[0.64rem] font-semibold uppercase tracking-[0.1em] text-muted">{fmt({ month: "short" })}</span>
      </span>

      <span className="min-w-0">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-deep">{k.label}</span>
          <span className="text-[0.88rem] text-muted">· {k.imam || t("sermons.noImam")}</span>
        </span>
        <span className={`mt-0.5 block truncate text-[0.93rem] ${k.theme ? "text-ink" : "italic text-muted"}`}>
          {k.theme || t("sermons.noTheme")}
        </span>
      </span>

      <span className="flex items-center gap-3">
        {pending > 0 && <Pill statut="a_valider" label={t("sermons.pendingCount", { n: pending })} />}
        <span className="hidden text-[0.85rem] tabular-nums text-muted sm:inline">
          {t("sermons.detail.clipCount", { n: Number(k.clips) || 0 })}
        </span>
        <span aria-hidden className="text-[1.2rem] leading-none text-muted transition-transform group-hover:translate-x-0.5 group-hover:text-deep">
          ›
        </span>
      </span>
    </Link>
  );
}