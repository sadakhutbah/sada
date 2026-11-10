import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { auditActionKey } from "../i18n";
import { Btn, Card, Field, Kicker, Pill, Segmented, inputClass } from "../ui";
import { dateFr, mmss } from "../format";

/** Sheet for a clip: review screen.
 *  Large screen: the video on the left (capped height, never full screen), on the right a sticky panel
 *  carrying the form split into sub-tabs (Decision, Subtitles, Publication, History).
 *  Under 1000 px, the two blocks stack. Everything needed to decide fits without scrolling. */

type Message = { texte: string; ko: boolean } | null;
type Agir = (action: () => Promise<any>, succes: string) => Promise<void>;

/** A path from the machine is never displayed: only web addresses stay readable. */
const estChemin = (v: unknown) => typeof v === "string" && /^(\/|~\/|[A-Za-z]:[\\/]|\\\\)/.test(v.trim());

/** Booleans coming from SQLite arrive as 0/1: `{0 && …}` would display “0”, so we force a real boolean. */
const oui = (v: unknown) => Boolean(v);

export default function ClipDetail() {
  const { id } = useParams();
  const { t, i18n } = useTranslation();
  const cid = Number(id);
  const [d, setD] = useState<any>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [onglet, setOnglet] = useState("decision");
  const [message, setMessage] = useState<Message>(null);
  const [occupe, setOccupe] = useState(false);

  const recharger = () =>
    api.clip(cid).then((r) => { setD(r); setErreur(null); }).catch((e) => setErreur(e?.message ?? String(e)));

  useEffect(() => {
    setD(null);
    setMessage(null);
    setOnglet("decision");
    recharger();
  }, [cid]);

  // Regeneration: the video is rebuilt on the server (about a minute), the sheet refreshes itself.
  const enCours = d?.clip?.regen_state === "en_cours";
  useEffect(() => {
    if (!enCours) return;
    const minuteur = window.setTimeout(recharger, 4000);
    return () => window.clearTimeout(minuteur);
  }, [enCours, d]);

  const agir: Agir = async (action, succes) => {
    setOccupe(true);
    setMessage(null);
    try {
      await action();
      setMessage({ texte: succes, ko: false });
      await recharger();
    } catch (e: any) {
      setMessage({ texte: e?.message ?? String(e), ko: true });
    } finally {
      setOccupe(false);
    }
  };

  if (!d) {
    return (
      <div className="grid gap-3">
        <Fil />
        {erreur ? (
          <div className="rounded-[10px] border border-[#efc9c4] bg-err-bg px-4.5 py-3.5">
            <p className="m-0 font-semibold text-[#8a2219]">{t("clips.detail.loadError")}</p>
            <p className="m-0 mt-0.5 text-[0.88rem] text-[#8a2219]/80">{erreur}</p>
          </div>
        ) : (
          <div className="grid gap-4 min-[1000px]:grid-cols-[minmax(0,1.3fr)_minmax(360px,1fr)]">
            <div className="h-[50vh] max-h-[560px] min-h-[240px] animate-pulse rounded-[10px] bg-night/85" />
            <Card><p className="m-0 text-muted">{t("common.loading")}</p></Card>
          </div>
        )}
      </div>
    );
  }

  const c = d.clip;
  const libelles = d.status_labels ?? {};
  const peutPublier = oui(d.can_publish);
  const ongletActif = onglet === "publication" && !peutPublier ? "decision" : onglet;

  return (
    <div className="grid gap-3">
      <header className="grid gap-1">
        <Fil sermon={d.sermon} langue={i18n.language} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="m-0 font-serif text-[1.9rem] font-normal leading-[1.1] text-deeper">
            {t("clips.clip")} {c.idx} · {t(`clips.kinds.${c.kind}`)}
          </h1>
          <Pill statut={c.status} label={libelles[c.status] ?? c.status} />
        </div>
      </header>

      <div className="grid items-start gap-4 min-[1000px]:grid-cols-[minmax(0,1.3fr)_minmax(360px,1fr)]">
        <Lecteur c={c} existe={oui(d.exists)} />

        <Card className="grid gap-3 min-[1000px]:sticky min-[1000px]:top-4 min-[1000px]:max-h-[calc(100vh-2rem)] min-[1000px]:overflow-y-auto">
          <Segmented
            valeur={ongletActif}
            onChange={(cle) => { setOnglet(cle); setMessage(null); }}
            items={[
              { cle: "decision", label: t("clips.detail.decision") },
              { cle: "sous-titres", label: t("clips.detail.subtitles") },
              { cle: "publication", label: t("clips.detail.publish"), eteint: !peutPublier },
              { cle: "historique", label: t("clips.detail.history") },
            ]}
          />

          {message && <Bandeau ko={message.ko}>{message.texte}</Bandeau>}

          {ongletActif === "decision" && (
            <Decision c={c} d={d} occupe={occupe} agir={agir} versPublication={() => setOnglet("publication")} />
          )}
          {ongletActif === "sous-titres" && <SousTitres d={d} occupe={occupe} agir={agir} />}
          {ongletActif === "publication" && <Publication d={d} occupe={occupe} agir={agir} />}
          {ongletActif === "historique" && <Historique d={d} />}
        </Card>
      </div>
    </div>
  );
}

/** Breadcrumb: back to the queue, then the original khutba. */
function Fil({ sermon, langue }: { sermon?: any; langue?: string }) {
  const { t } = useTranslation();
  const lien = "text-deep underline decoration-leaf underline-offset-4 hover:text-deeper";
  return (
    <p className="m-0 text-[0.85rem] text-muted">
      <Link to="/clips" className={lien}>← {t("clips.title")}</Link>
      {sermon && (
        <>
          {" · "}
          <Link to={`/khutbas/${sermon.id}`} className={lien}>
            {dateFr(sermon.date, langue)} · {sermon.label}
          </Link>
        </>
      )}
    </p>
  );
}

/** Video stage: night background, fixed capped height, whole image (contain) whatever the format. */
function Lecteur({ c, existe }: { c: any; existe: boolean }) {
  const { t } = useTranslation();
  const enCours = c.regen_state === "en_cours";
  return (
    <div className="relative overflow-hidden rounded-[10px] bg-night">
      {existe ? (
        <video
          key={c.version}
          controls
          preload="metadata"
          playsInline
          className="block h-[50vh] max-h-[560px] min-h-[240px] w-full bg-night object-contain"
          src={`/media/clip/${c.id}.mp4?v=${c.version}`}
        />
      ) : (
        <div className="grid h-[50vh] max-h-[560px] min-h-[240px] place-items-center px-6 text-center">
          <p className="m-0 text-[0.95rem] text-night-muted">{t("clips.detail.noFile")}</p>
        </div>
      )}
      {enCours && (
        <span className="absolute top-3 left-3 inline-flex items-center gap-2 rounded-full bg-night/85 px-3 py-1 text-[0.8rem] font-semibold text-night-ink">
          <span className="size-2 animate-pulse rounded-full bg-leaf" />
          {t("clips.detail.regenerate")}
        </span>
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-white/10 px-3.5 py-2 text-[0.82rem] text-night-muted">
        <span className="tabular-nums">
          {mmss(c.start)}–{mmss(c.end)} · {t("clips.detail.seconds", { s: Math.round(c.end - c.start) })}
        </span>
        <span>{t("clips.detail.version", { n: c.version })}</span>
      </div>
    </div>
  );
}

function Bandeau({ ko, children }: { ko: boolean; children: ReactNode }) {
  return (
    <p className={`m-0 rounded-lg px-3 py-2 text-[0.88rem] leading-snug ${ko ? "bg-err-bg text-[#8a2219]" : "bg-paper-2 text-deep"}`}>
      {children}
    </p>
  );
}

/** Sheet row: discreet label on the left, value on the right. */
function Ligne({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted">{label}</dt>
      <dd className="m-0 min-w-0 text-ink">{children}</dd>
    </>
  );
}

function Decision({ c, d, occupe, agir, versPublication }: {
  c: any; d: any; occupe: boolean; agir: Agir; versPublication: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [motif, setMotif] = useState("");
  const aValider = c.status === "a_valider";
  const enCours = c.regen_state === "en_cours";
  const aRegenerer = oui(c.needs_regen) || c.regen_state === "erreur";
  const valider = aValider && oui(d.can_validate) && !oui(c.needs_regen);
  const duree = Math.round(c.end - c.start);
  const contexte = d.contexte ?? { theme: "", avant: [], versets: [] };
  const contexteActif = Boolean(contexte.theme || contexte.avant.length || contexte.versets.length);

  return (
    <div className="grid gap-3.5">
      {/* The sheet: what one needs to know in order to decide. */}
      <dl className="m-0 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[0.9rem]">
        <Ligne label={t("clips.status")}>
          {c.status === "publie" ? (
            <>
              {t("clips.detail.published", { n: c.version })}
              {c.decided_at ? ` — ${dateFr(String(c.decided_at).slice(0, 10), i18n.language)}` : ""}
            </>
          ) : (
            d.status_labels?.[c.status] ?? c.status
          )}
          {!aValider && c.reject_reason ? <span className="text-muted"> — {c.reject_reason}</span> : null}
        </Ligne>
        <Ligne label={t("clips.detail.excerpt")}>
          <span className="tabular-nums">{mmss(c.start)}–{mmss(c.end)}</span>
        </Ligne>
        <Ligne label={t("clips.duration")}>
          <span className="tabular-nums">{t("clips.detail.seconds", { s: duree })}</span>
        </Ligne>
        <Ligne label={t("clips.detail.versionLabel")}>{c.version}</Ligne>
        {c.why ? <Ligne label={t("clips.why")}>{c.why}</Ligne> : null}
      </dl>

      {/* Context: an excerpt is not judged outside its khutba. Theme, verse carried, and the sentence
          that precedes the excerpt — it is what gives the cue the clip starts from. */}
      {contexteActif && (
        <div className="rounded-[8px] border border-line bg-paper-2/40 px-3 py-2.5">
          <Kicker>{t("clips.detail.context")}</Kicker>
          {contexte.theme && <p className="m-0 text-[0.88rem] text-deep">{contexte.theme}</p>}
          {contexte.versets.map((v: any, i: number) => (
            <p key={i} className="mt-1.5 mb-0 text-[0.88rem]">
              <span className="font-semibold text-green">{v.ref}{v.surah ? ` · ${v.surah}` : ""}</span>
              {v.fr && <span className="block text-deep">{v.fr}</span>}
            </p>
          ))}
          {contexte.avant.length > 0 && (
            <>
              <p className="mt-2 mb-1 text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-muted">
                {t("clips.detail.before")}
              </p>
              {contexte.avant.map((a: any) => (
                <p key={a.start} className="m-0 text-[0.86rem] text-muted">« {a.texte} »</p>
              ))}
            </>
          )}
        </div>
      )}

      {/* Video states: regeneration expected, running or failed. */}
      {enCours && <Bandeau ko={false}>{t("clips.detail.regenRunning")}</Bandeau>}
      {!enCours && c.regen_state === "erreur" && (
        <Bandeau ko>{t("clips.detail.regenError")}{c.regen_error && !estChemin(c.regen_error) ? ` : ${c.regen_error}` : ""}</Bandeau>
      )}
      {!enCours && oui(c.needs_regen) && (
        <Bandeau ko>{c.status === "publie" ? t("clips.detail.publishedRegen", { n: c.version + 1 }) : t("clips.detail.needsRegen")}</Bandeau>
      )}

      {/* When the video must be rebuilt, regeneration becomes the main action. */}
      {aRegenerer && !enCours && (
        d.can_regenerate ? (
          <Btn
            variante="leaf"
            disabled={occupe}
            onClick={() => agir(() => api.regenerer(c.id), t("clips.detail.regenStarted"))}
          >
            {t("clips.detail.regenerate")}
          </Btn>
        ) : (
          d.regenerate_reason ? <p className="m-0 text-[0.85rem] text-muted">{d.regenerate_reason}</p> : null
        )
      )}

      {aValider && (
        <div className="grid gap-2.5 border-t border-line pt-3.5">
          <Btn
            variante={aRegenerer ? "ghost" : "plein"}
            disabled={occupe || !valider || enCours}
            onClick={() => agir(() => api.decision(c.id, "valide"), t("clips.detail.validated"))}
          >
            {t("clips.detail.validate")}
          </Btn>
          {!valider && !aRegenerer && (
            <p className="m-0 text-[0.82rem] text-muted">{t("clips.detail.validateBlocked")}</p>
          )}
          <div className="flex gap-2">
            <input
              className={`${inputClass} min-h-11 py-2 text-[0.9rem]`}
              placeholder={t("clips.detail.reason")}
              aria-label={t("clips.detail.reason")}
              value={motif}
              onChange={(e) => setMotif(e.target.value)}
            />
            <Btn
              variante="dangerGhost"
              className="shrink-0"
              disabled={occupe}
              onClick={() => agir(() => api.decision(c.id, "rejete", motif), t("clips.detail.rejected"))}
            >
              {t("clips.detail.reject")}
            </Btn>
          </div>
        </div>
      )}

      {!aValider && (
        <div className="flex flex-wrap gap-2 border-t border-line pt-3.5">
          {c.status === "valide" && oui(d.can_publish) && (
            <Btn onClick={versPublication}>{t("clips.detail.goPublish")}</Btn>
          )}
          <Btn
            variante="ghost"
            disabled={occupe}
            onClick={() => agir(() => api.decision(c.id, "rouvrir"), t("clips.detail.reopened"))}
          >
            {t("clips.detail.reopen")}
          </Btn>
        </div>
      )}

      {aValider && <p className="m-0 text-[0.8rem] leading-snug text-muted">{t("clips.intro")}</p>}
    </div>
  );
}

function SousTitres({ d, occupe, agir }: { d: any; occupe: boolean; agir: Agir }) {
  const { t } = useTranslation();
  const [saisie, setSaisie] = useState<Record<number, string>>({});
  const lignes = useMemo(() => d.segments ?? [], [d]);
  const modifiable = oui(d.can_validate);
  const initial = (r: any) => (r.field === "fr" ? r.fr ?? "" : r.text);

  if (!lignes.length) return <p className="m-0 text-[0.9rem] text-muted">{t("clips.detail.noSubtitles")}</p>;

  return (
    <div className="grid gap-2">
      <p className="m-0 text-[0.82rem] leading-snug text-muted">{t("clips.detail.subtitlesHelp")}</p>
      <ul className="m-0 grid list-none p-0">
        {lignes.map((r: any) => {
          const valeur = saisie[r.i] ?? initial(r);
          const change = valeur !== initial(r);
          return (
            <li key={r.i} className="grid gap-1 border-b border-line py-2.5 last:border-0">
              <p className="m-0 flex items-baseline gap-2 text-[0.76rem] text-muted">
                <span className="tabular-nums font-semibold text-deep">{mmss(r.rel)}</span>
                <span>{r.verse ? r.verse.ref : t("clips.detail.line")}</span>
                {oui(r.corrected) && (
                  <span className="rounded-full bg-paper-2 px-1.5 font-semibold text-deep">{t("clips.detail.corrected")}</span>
                )}
              </p>
              <p className="m-0 text-[0.92rem] leading-snug" dir="auto">{r.text}</p>
              {r.verse?.fr ? <p className="m-0 text-[0.85rem] leading-snug text-muted" dir="auto">{r.verse.fr}</p> : null}
              {r.field && modifiable && (
                <div className="flex items-center gap-1.5">
                  <input
                    className={`${inputClass} py-1.5 text-[0.88rem]`}
                    dir="auto"
                    value={valeur}
                    onChange={(e) => setSaisie({ ...saisie, [r.i]: e.target.value })}
                  />
                  <Btn
                    variante={change ? "plein" : "ghost"}
                    className="min-h-9 shrink-0 px-3 py-1 text-[0.85rem]"
                    disabled={occupe || !change}
                    onClick={() => agir(() => api.correction(d.sermon.id, r.i, r.field, valeur), t("clips.detail.saved"))}
                  >
                    {t("common.save")}
                  </Btn>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Publication({ d, occupe, agir }: { d: any; occupe: boolean; agir: Agir }) {
  const { t, i18n } = useTranslation();
  const connecteurs = d.connectors ?? [];
  const dispo = connecteurs.filter((x: any) => oui(x.available));
  const [dest, setDest] = useState(dispo[0]?.key ?? "local");
  const [legende, setLegende] = useState(d.caption ?? "");
  const pubs = d.publications ?? [];

  return (
    <div className="grid gap-3">
      <Field label={t("clips.detail.destination")}>
        <select className={`${inputClass} py-2`} value={dest} onChange={(e) => setDest(e.target.value)}>
          {connecteurs.map((x: any) => (
            <option key={x.key} value={x.key} disabled={!oui(x.available)}>
              {x.label}{oui(x.available) || !x.why ? "" : ` — ${x.why}`}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t("clips.detail.caption")}>
        <textarea
          className={`${inputClass} min-h-28 py-2 text-[0.92rem]`}
          dir="auto"
          value={legende}
          onChange={(e) => setLegende(e.target.value)}
        />
      </Field>
      <Btn disabled={occupe || !dispo.length} onClick={() => agir(() => api.publier(d.clip.id, dest, legende), t("clips.detail.publishedOk"))}>
        {t("clips.detail.publishBtn")}
      </Btn>

      {pubs.length > 0 && (
        <div className="border-t border-line pt-3">
          <Kicker>{t("clips.detail.publications")}</Kicker>
          <ul className="m-0 grid list-none gap-2 p-0 text-[0.88rem]">
            {pubs.map((p: any) => {
              const lien = typeof p.location === "string" && /^https?:\/\//.test(p.location) ? p.location : null;
              const note = lien ? null : (p.message && !estChemin(p.message) ? p.message : null);
              return (
                <li key={p.id} className="grid gap-0.5">
                  <span className="flex flex-wrap items-center gap-x-2">
                    <Pill statut={p.status === "publie" ? "publie" : "rejete"} label={p.status} />
                    <span className="font-semibold">{p.connector}</span>
                    <span className="text-muted">v{p.clip_version} · {dateFr(String(p.created_at ?? "").slice(0, 10), i18n.language)}</span>
                  </span>
                  {lien && (
                    <a href={lien} target="_blank" rel="noreferrer" className="truncate text-deep underline decoration-leaf underline-offset-4">
                      {lien}
                    </a>
                  )}
                  {note && <span className="text-muted">{note}</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Action details: JSON flattened to “key: value”, never showing a path. */
function details(brut: unknown): string {
  if (!brut || brut === "{}") return "";
  try {
    const o = typeof brut === "string" ? JSON.parse(brut) : brut;
    if (o && typeof o === "object") {
      return Object.entries(o)
        .filter(([, v]) => v !== null && v !== "" && typeof v !== "object" && !estChemin(v))
        .map(([k, v]) => `${k} : ${v}`)
        .join(" · ");
    }
  } catch {
    /* free text */
  }
  return estChemin(brut) ? "" : String(brut);
}

function Historique({ d }: { d: any }) {
  const { t } = useTranslation();
  const actions = d.history ?? [];
  if (!actions.length) return <p className="m-0 text-[0.9rem] text-muted">{t("clips.detail.noHistory")}</p>;
  return (
    <ol className="m-0 grid list-none p-0">
      {actions.map((a: any) => {
        const plus = details(a.details);
        return (
          <li key={a.id} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 border-b border-line py-2 text-[0.88rem] last:border-0">
            <span className="tabular-nums text-muted">{String(a.created_at ?? "").replace("T", " ").slice(0, 16)}</span>
            <span>
              <b className="font-semibold">{t(auditActionKey(a.action), { defaultValue: a.action })}</b>
              <span className="text-muted"> · {a.who ?? t("clips.detail.system")}</span>
              {plus && <span className="block text-[0.82rem] text-muted">{plus}</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
