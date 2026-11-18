import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, type SearchHit, type SearchResponse, type SearchResult } from "../api";
import { mmss } from "../format";
import { inputClass } from "../ui";

/** Search across every recorded session of the mosque, whatever language was spoken: words (in the
 *  transcript and its translations), Quran verses (« 2:153 », « Al-Baqara », « البقرة ») and meaning.
 *  One row per passage, grouped by session; a row opens the session with the player on the passage.
 *  The query lives in the address (?q=), so a search can be shared and Back returns to it. */
export default function Search() {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [saisie, setSaisie] = useState(q);
  // A new query from outside (the top bar field) replaces the text; our own debounce does not.
  const [qVue, setQVue] = useState(q);
  if (qVue !== q) { setQVue(q); if (saisie.trim() !== q) setSaisie(q); }
  // The answer is kept with the query it answers: a stale answer is never shown for a new query.
  const [reponse, setReponse] = useState<{ q: string; res?: SearchResponse; erreur?: string }>({ q: "" });
  const actuelle = reponse.q === q;
  const res = actuelle ? (reponse.res ?? null) : null;
  const erreur = actuelle ? (reponse.erreur ?? "") : "";

  // Debounced: the address follows the input, the request follows the address.
  useEffect(() => {
    const id = window.setTimeout(() => {
      if (saisie.trim() !== q) setParams(saisie.trim() ? { q: saisie.trim() } : {}, { replace: true });
    }, 300);
    return () => window.clearTimeout(id);
  }, [saisie, q, setParams]);

  useEffect(() => {
    if (q.trim().length < 2) return;
    let actif = true;
    api
      .search(q)
      .then((r) => actif && setReponse({ q, res: r }))
      .catch((e) => actif && setReponse({ q, erreur: e?.message ?? t("common.error") }));
    return () => { actif = false; };
  }, [q, t]);

  const passages = res?.results.reduce((n, r) => n + r.hits.length, 0) ?? 0;

  return (
    <div className="grid gap-5">
      <header className="grid gap-1">
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("search.title")}</h1>
        <p className="m-0 min-h-[1.45rem] text-[0.95rem] text-muted">
          {res && q ? t("search.summary", { count: res.results.length, passages, q }) : t("search.help")}
        </p>
      </header>

      <form role="search" onSubmit={(e) => { e.preventDefault(); setParams(saisie.trim() ? { q: saisie.trim() } : {}); }} className="relative">
        <svg aria-hidden viewBox="0 0 20 20" className="pointer-events-none absolute top-1/2 left-3.5 size-[1.05rem] -translate-y-1/2 text-muted" fill="none" stroke="currentColor" strokeWidth="1.6">
          <circle cx="8.8" cy="8.8" r="5.3" />
          <path d="m12.9 12.9 3.4 3.4" strokeLinecap="round" />
        </svg>
        <input
          type="search"
          autoFocus
          dir="auto"
          className={`${inputClass} h-12 w-full pr-4 pl-10 text-[1.02rem] placeholder:text-muted/80`}
          placeholder={t("search.placeholder")}
          value={saisie}
          onChange={(e) => setSaisie(e.target.value)}
          aria-label={t("search.placeholder")}
        />
      </form>

      {res && !res.semantic && q && (
        <p className="m-0 text-[0.85rem] text-muted">{t("search.noSemantic")}</p>
      )}

      {erreur ? (
        <div className="rounded-[10px] border border-[#efc9c4] bg-err-bg px-4.5 py-3.5 text-[#7a1f20]">{erreur}</div>
      ) : q.trim().length >= 2 && !res ? (
        <div className="grid gap-2" aria-busy="true" aria-label={t("common.loading")}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="grid gap-2 rounded-[10px] border border-line bg-white px-5 py-4">
              <div className="h-3 w-[40%] animate-pulse rounded bg-paper-2" />
              <div className="h-2.5 w-[75%] animate-pulse rounded bg-paper-2/70" />
              <div className="h-2.5 w-[60%] animate-pulse rounded bg-paper-2/70" />
            </div>
          ))}
        </div>
      ) : res && res.results.length === 0 ? (
        <div className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-16 text-center">
          <p className="mx-auto max-w-[54ch] text-[1.02rem] leading-relaxed text-muted">{t("search.empty", { q })}</p>
        </div>
      ) : res ? (
        <div className="overflow-hidden rounded-[10px] border border-line bg-white">
          {res.results.map((r, i) => (
            <Seance key={r.id} r={r} premier={i === 0} langue={i18n.language} />
          ))}
        </div>
      ) : (
        <Exemples onChoisir={setSaisie} />
      )}
    </div>
  );
}

/** A session: its heading (kind, title, speaker, date), then one row per matching passage. */
function Seance({ r, premier, langue }: { r: SearchResult; premier: boolean; langue: string }) {
  const { t } = useTranslation();
  const date = r.date ? new Intl.DateTimeFormat(langue, { day: "numeric", month: "long", year: "numeric" }).format(new Date(`${r.date}T12:00:00`)) : "";
  return (
    <section className={premier ? "" : "border-t border-line"}>
      <h2 className="m-0 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 bg-paper-2/55 px-4 py-2.5 sm:px-5">
        <span className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-green">{t(`search.kind.${r.kind}`, { defaultValue: r.kind })}</span>
        <Link to={`/khutbas/${r.id}`} className="min-w-0 font-serif text-[1.15rem] font-normal leading-snug text-deeper no-underline hover:text-green">
          {r.title || r.label}
        </Link>
        <span className="text-[0.85rem] text-muted">{[r.speaker, date].filter(Boolean).join(" · ")}</span>
      </h2>
      <ul className="m-0 list-none divide-y divide-line/80 p-0">
        {r.hits.map((h, i) => (
          <li key={`${h.type}-${h.start}-${i}`}>
            <Passage id={r.id} h={h} />
          </li>
        ))}
      </ul>
    </section>
  );
}

const TYPE_CLASSE: Record<SearchHit["type"], string> = {
  word: "bg-leaf/15 text-deep",
  verse: "bg-gold/20 text-[#6b4d0f]",
  semantic: "bg-paper-2 text-muted",
};

/** A passage: the time in the margin, the excerpt with the query words marked, the kind of match. */
function Passage({ id, h }: { id: number; h: SearchHit }) {
  const { t, i18n } = useTranslation();
  const v = h.verse;
  const nom = v ? (i18n.language === "fr" ? v.name_fr : v.name_en) : "";
  return (
    <Link
      to={`/khutbas/${id}?t=${Math.floor(h.start)}`}
      className="group grid grid-cols-[3.4rem_minmax(0,1fr)_auto] items-start gap-x-4 px-4 py-3 text-inherit no-underline transition-colors hover:bg-paper-2/50 focus-visible:bg-paper-2/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-leaf sm:px-5"
    >
      <span className="pt-0.5 font-mono text-[0.85rem] tabular-nums text-green">{mmss(h.start)}</span>
      <span className="min-w-0 text-[0.95rem] leading-relaxed text-ink">
        {v ? (
          <span>
            {t("search.verse", { surah: v.surah, ayah: v.ayah, name: nom })}{" "}
            <span dir="rtl" lang="ar" className="font-arabic text-[1.05rem]">{v.name_ar}</span>
          </span>
        ) : (
          <span dir="auto" lang={h.lang || undefined} className="line-clamp-3 block">
            {h.snippet.map((p, i) =>
              p.hit ? <mark key={i} className="rounded-[3px] bg-gold/35 px-0.5 text-inherit">{p.text}</mark> : <span key={i}>{p.text}</span>,
            )}
          </span>
        )}
      </span>
      <span className="flex items-center gap-2 pt-0.5">
        {h.lang && <span className="hidden text-[0.72rem] font-semibold uppercase tracking-[0.1em] text-muted sm:inline">{h.lang}</span>}
        <span className={`rounded-full px-2 py-0.5 text-[0.72rem] font-semibold whitespace-nowrap ${TYPE_CLASSE[h.type]}`}>{t(`search.type.${h.type}`)}</span>
      </span>
    </Link>
  );
}

/** Before typing: what the search understands, as examples one can click. */
function Exemples({ onChoisir }: { onChoisir: (q: string) => void }) {
  const { t } = useTranslation();
  const exemples = ["patience", "صبر", "2:153", t("search.exampleSurah")];
  return (
    <div className="rounded-[10px] border border-dashed border-line bg-white/60 px-6 py-10 text-center">
      <p className="m-0 mx-auto max-w-[60ch] text-[0.98rem] leading-relaxed text-muted">{t("search.explain")}</p>
      <div className="mt-4 flex flex-wrap justify-center gap-2">
        {exemples.map((e) => (
          <button key={e} type="button" dir="auto" onClick={() => onChoisir(e)} className="min-h-11 cursor-pointer rounded-full border border-line bg-white px-4 text-[0.92rem] text-deep hover:border-green hover:text-green">
            {e}
          </button>
        ))}
      </div>
    </div>
  );
}
