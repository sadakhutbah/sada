import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { currentSession } from "../App";
import { Btn, Card, Kicker, Pill, Tabs, Title, inputClass } from "../ui";
import { dateFr, mmss } from "../format";

const VIDEO_MAX = "max-h-[50vh]";

/** Fiche d'une khutba : la vidéo et ses clips à gauche, la transcription corrigeable à droite.
 *  Rien n'est inventé côté front : les versets, les statuts et les libellés viennent du serveur. */
export default function SermonDetail() {
  const { id } = useParams();
  const kid = Number(id);
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [onglet, setOnglet] = useState("transcription");
  const [avis, setAvis] = useState<string | null>(null);
  const [tps, setTps] = useState(0);        // position de lecture, pour suivre le passage à l'écran
  const video = useRef<HTMLVideoElement | null>(null);

  const perms = currentSession()?.user.permissions ?? [];
  const peutCorriger = perms.includes("validate");
  const peutAssigner = perms.includes("assign");

  const recharger = () =>
    api.sermon(kid).then(setD).catch((e) => setErreur(e?.message ?? t("common.error")));

  useEffect(() => {
    recharger();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kid]);

  /** Cliquer une ligne de transcription amène la vidéo au passage : c'est le seul moyen de vérifier
   *  une correction à l'oreille. La ligne en cours de lecture reste mise en évidence. */
  const aller = (secondes: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, secondes);
    setTps(Math.max(0, secondes));
    v.play().catch(() => {});
  };

  if (erreur) return <Card><p className="m-0 text-muted">{erreur}</p></Card>;
  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const k = d.sermon;
  const onglets = [
    { cle: "transcription", label: t("sermons.detail.transcript"), compte: d.transcript.length },
    { cle: "versets", label: t("sermons.detail.verses"), compte: d.verses.length },
    { cle: "notes", label: t("sermons.detail.notes") },
    ...(peutAssigner ? [{ cle: "rattachement", label: t("sermons.detail.assignment") }] : []),
  ];

  return (
    <div className="grid gap-4">
      <div>
        <Link to="/sermons" className="text-[0.88rem] font-semibold text-green no-underline hover:underline">
          ← {t("nav.sermons")}
        </Link>
        <Kicker>{k.label}</Kicker>
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{dateFr(k.date, i18n.language)}</h1>
        <p className="mt-1.5 mb-0 flex flex-wrap gap-x-4 gap-y-1 text-[0.9rem] text-muted">
          <span className="tabular-nums">{k.start_clock ?? "?"}–{k.end_clock ?? "?"}</span>
          <span>{t("sermons.detail.duration", { d: mmss(k.duration_s) })}</span>
          <span>{t("sermons.detail.verseCount", { n: d.verses.length })}</span>
          <span>{t("sermons.detail.imamLabel")} : {d.imam?.display_name ?? (k.speaker || t("sermons.detail.noImam"))}</span>
        </p>
        {k.theme && <p className="mt-2 mb-0 text-[0.95rem]">{k.theme}</p>}
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)]">
        {/* Colonne gauche : la vidéo, puis les clips qu'elle a produits. */}
        <div className="grid gap-4">
          <div className="overflow-hidden rounded-[10px] border border-line bg-white">
            {d.has_video ? (
              <video ref={video} controls preload="metadata" playsInline
                     onTimeUpdate={(e) => setTps(e.currentTarget.currentTime)}
                     className={`w-full ${VIDEO_MAX} bg-night object-contain`}
                     src={`/media/sermon/${k.id}.mp4`} />
            ) : (
              <p className="m-0 px-4 py-10 text-center text-muted">{t("sermons.detail.noVideo")}</p>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2.5 text-[0.85rem] text-muted">
              <span className="font-semibold text-deep">{k.label}</span>
              <span className="tabular-nums">{mmss(k.duration_s)}</span>
              <span>{t("sermons.detail.clipCount", { n: d.clips.length })}</span>
            </div>
          </div>

          <Card>
            <Title>{t("sermons.detail.clipsTitle")}</Title>
            {d.clips.length === 0 ? (
              <p className="m-0 text-muted">{t("sermons.detail.noClips")}</p>
            ) : (
              <div className="grid gap-2">
                {d.clips.map((c: any) => (
                  <Link key={c.id} to={`/clips/${c.id}`}
                        className="flex items-center justify-between gap-3 rounded-[8px] border border-line px-3 py-2.5 no-underline transition-colors hover:bg-paper-2/60">
                    <span className="min-w-0">
                      <span className="block font-semibold text-deep">
                        {t("clips.clip")} {c.idx} · {t(`clips.kinds.${c.kind}`, c.kind)}
                      </span>
                      <span className="block text-[0.85rem] text-muted tabular-nums">
                        {mmss(c.start)}–{mmss(c.end)} · {t("clips.detail.version", { n: c.version })}
                      </span>
                    </span>
                    <Pill statut={c.status} label={(d.status_labels ?? {})[c.status] ?? c.status} />
                  </Link>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* Colonne droite : un seul bloc à la fois, comme la fiche de clip. */}
        <Card className="lg:sticky lg:top-4">
          <Tabs items={onglets} valeur={onglet} onChange={setOnglet} />
          {avis && <p className="mt-0 mb-3 text-[0.88rem] font-semibold text-green">{avis}</p>}

          {onglet === "transcription" && (
            <div className={`grid gap-2.5 overflow-y-auto pr-1 ${d.has_video ? "lg:max-h-[58vh]" : "max-h-[70vh]"}`}>
              {d.transcript.map((r: any) => {
                const actif = tps >= r.start && tps < r.end;
                return (
                <div key={r.i} onClick={() => aller(r.start)}
                     role="button" tabIndex={0}
                     onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); aller(r.start); } }}
                     title={t("sermons.detail.seek")}
                     className={`cursor-pointer rounded-r-[6px] border-l-2 py-1 pr-2 pl-3 transition-colors hover:bg-paper-2/70 ${
                       actif ? "border-green bg-leaf/10" : r.verse || r.coran ? "border-leaf/60" : "border-line"}`}>
                  <span className={`mb-1 block text-[0.8rem] font-semibold tabular-nums ${actif ? "text-deep" : "text-green"}`}>
                    {mmss(r.start)}
                  </span>
                  {r.arabic && <p className="m-0 text-right text-[1.05rem] leading-[1.9] text-deep" dir="rtl">{r.text}</p>}
                  {r.verse && (
                    <p className="m-0 text-[0.88rem]">
                      <span className={`font-semibold ${r.verse.ambiguous ? "text-warn" : "text-green"}`}>
                        {r.verse.ref}{r.verse.surah ? ` · ${r.verse.surah}` : ""}
                        {r.verse.ambiguous ? ` · ${t("sermons.detail.ambiguous")}` : ""}
                      </span>
                      <span className="block text-deep">{r.verse.fr}</span>
                    </p>
                  )}
                  {!r.verse && r.coran && (
                    <p className="m-0 text-[0.85rem] text-muted">{t("sermons.detail.recitation")}</p>
                  )}
                  {!r.verse && !r.coran && r.fr && (
                    <p className="m-0 text-[0.93rem] text-deep">
                      {r.fr}
                      {r.corrected && <span className="ml-2 text-[0.76rem] font-semibold uppercase tracking-wide text-green">{t("sermons.detail.corrected")}</span>}
                    </p>
                  )}
                  {peutCorriger && r.field && (
                    // La correction se déplie sans emmener la vidéo ailleurs : le clic reste au champ.
                    <details className="mt-1.5" onClick={(e) => e.stopPropagation()}>
                      <summary className="cursor-pointer text-[0.82rem] font-semibold text-muted hover:text-deep">
                        {r.field === "text" ? t("sermons.detail.fixSubtitle") : t("sermons.detail.fixTranslation")}
                      </summary>
                      <CorrectionForm
                        valeurInitiale={r.field === "fr" ? (r.fr ?? "") : r.text}
                        onSave={async (valeur) => {
                          const r2 = await api.correction(kid, r.i, r.field, valeur);
                          setAvis(r2?.message ?? t("common.save"));
                          await recharger();
                        }}
                      />
                    </details>
                  )}
                </div>
                );
              })}
            </div>
          )}

          {onglet === "versets" && (
            <div className="grid max-h-[58vh] gap-2.5 overflow-y-auto pr-1">
              {d.verses.length === 0 ? (
                <p className="m-0 text-muted">{t("sermons.detail.noVerses")}</p>
              ) : (
                d.verses.map((v: any, i: number) => (
                  <div key={i} className="rounded-[8px] border border-line px-3 py-2.5">
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <button type="button" onClick={() => aller(v.start)}
                              className={`cursor-pointer border-0 bg-transparent p-0 font-semibold ${v.ambiguous ? "text-warn" : "text-green"}`}>
                        {v.ref}{v.surah_name ? ` · ${v.surah_name}` : ""}
                      </button>
                      <span className="text-[0.82rem] tabular-nums text-muted">{mmss(v.start)}</span>
                      {v.ambiguous && <span className="text-[0.8rem] font-semibold text-warn">{t("sermons.detail.ambiguous")}</span>}
                      {v.via === "francais" && <span className="text-[0.8rem] text-muted">{t("sermons.detail.viaTranslation")}</span>}
                    </div>
                    {v.verse_fr && <p className="mt-1 mb-0 text-[0.92rem] text-deep">{v.verse_fr}</p>}
                  </div>
                ))
              )}
            </div>
          )}

          {onglet === "notes" && (
            <pre className="m-0 max-h-[58vh] overflow-auto whitespace-pre-wrap font-sans text-[0.9rem] text-deep">
              {d.notes || t("sermons.detail.noNotes")}
            </pre>
          )}

          {onglet === "rattachement" && peutAssigner && (
            <Rattachement
              imams={d.imams ?? []}
              imamId={k.imam_id ? String(k.imam_id) : ""}
              intervenant={k.speaker ?? ""}
              onSave={async (imamId, speaker) => {
                const r = await api.intervenant(kid, imamId, speaker);
                setAvis(r?.message ?? t("common.save"));
                await recharger();
              }}
            />
          )}
        </Card>
      </div>
    </div>
  );
}

/** Correction d'une ligne de transcription : un champ, un bouton, et le passage reste écoutable. */
function CorrectionForm({ valeurInitiale, onSave }: { valeurInitiale: string; onSave: (valeur: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [valeur, setValeur] = useState(valeurInitiale);
  const [envoi, setEnvoi] = useState(false);
  return (
    <div className="mt-1.5 grid gap-2">
      <textarea className={`${inputClass} min-h-[64px]`} value={valeur} onChange={(e) => setValeur(e.target.value)} />
      <Btn variante="ghost" className="justify-self-start" disabled={envoi || !valeur.trim()}
           onClick={async () => {
             setEnvoi(true);
             try {
               await onSave(valeur);
             } finally {
               setEnvoi(false);
             }
           }}>
        {envoi ? t("common.saving") : t("common.save")}
      </Btn>
    </div>
  );
}

/** Rattachement de la khutba à un compte imam, ou à un intervenant sans compte. */
function Rattachement({
  imams, imamId, intervenant, onSave,
}: { imams: any[]; imamId: string; intervenant: string; onSave: (imamId: string, speaker: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [choix, setChoix] = useState(imamId);
  const [nom, setNom] = useState(intervenant);
  const [envoi, setEnvoi] = useState(false);
  return (
    <div className="grid gap-3">
      <div>
        <label className="mb-1 block text-[0.85rem] font-semibold text-deep">{t("sermons.detail.imamLabel")}</label>
        <select className={inputClass} value={choix} onChange={(e) => setChoix(e.target.value)}>
          <option value="">{t("sermons.detail.noImam")}</option>
          {imams.map((i) => <option key={i.id} value={i.id}>{i.display_name}</option>)}
        </select>
      </div>
      <div>
        <label className="mb-1 block text-[0.85rem] font-semibold text-deep">{t("sermons.detail.speaker")}</label>
        <input className={inputClass} value={nom} onChange={(e) => setNom(e.target.value)} />
      </div>
      <Btn className="justify-self-start" disabled={envoi}
           onClick={async () => {
             setEnvoi(true);
             try {
               await onSave(choix, nom);
             } finally {
               setEnvoi(false);
             }
           }}>
        {envoi ? t("common.saving") : t("common.save")}
      </Btn>
    </div>
  );
}
