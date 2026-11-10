import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { currentSession } from "../App";
import { Btn, Card, Field, Kicker, Pill, Segmented, Title, inputClass } from "../ui";
import { dateFr, mmss } from "../format";

/** Fiche d'une khutba, même grammaire que la fiche de clip.
 *  Grand écran (≥ 1000 px) : la vidéo et ses clips à gauche, à droite un panneau collant à sous-onglets
 *  (Transcription, Versets, Notes, Rattachement) dont le contenu défile seul. Sous 1000 px, tout s'empile.
 *  Rien n'est inventé côté front : versets, statuts et libellés viennent du serveur. */

type Message = { texte: string; ko: boolean } | null;

export default function SermonDetail() {
  const { id } = useParams();
  const kid = Number(id);
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [onglet, setOnglet] = useState("transcription");
  const [message, setMessage] = useState<Message>(null);
  const [tps, setTps] = useState(0); // position de lecture, pour surligner le passage en cours
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

  /** Une écriture (correction, rattachement) : message du serveur, puis fiche rechargée. */
  const enregistrer = async (action: () => Promise<any>) => {
    setMessage(null);
    try {
      const r = await action();
      setMessage({ texte: r?.message ?? t("sermons.detail.saved"), ko: false });
      await recharger();
    } catch (e: any) {
      setMessage({ texte: e?.message ?? t("common.error"), ko: true });
    }
  };

  /** Amène la vidéo au passage et la lance : c'est ainsi qu'on vérifie une correction à l'oreille. */
  const aller = (secondes: number) => {
    const v = video.current;
    if (!v) return;
    v.currentTime = Math.max(0, secondes);
    setTps(Math.max(0, secondes));
    v.play().catch(() => {});
  };

  if (erreur) {
    return (
      <div className="grid gap-3">
        <Retour />
        <div className="rounded-[10px] border border-[#efc9c4] bg-err-bg px-4.5 py-3.5">
          <p className="m-0 font-semibold text-[#8a2219]">{t("sermons.detail.loadError")}</p>
          <p className="m-0 mt-0.5 text-[0.88rem] text-[#8a2219]/80">{erreur}</p>
        </div>
      </div>
    );
  }

  if (!d) {
    return (
      <div className="grid gap-4">
        <Retour />
        <div className="grid gap-4 min-[1000px]:grid-cols-[minmax(0,1.2fr)_minmax(380px,1fr)]">
          <div className="h-[40vh] min-h-[220px] animate-pulse rounded-[10px] bg-night/85" />
          <Card><p className="m-0 text-muted">{t("common.loading")}</p></Card>
        </div>
      </div>
    );
  }

  const k = d.sermon;
  const libelles = d.status_labels ?? {};
  const clipsAValider = d.clips.filter((c: any) => c.status === "a_valider").length;
  const ongletActif = onglet === "rattachement" && !peutAssigner ? "transcription" : onglet;
  const onglets = [
    { cle: "transcription", label: t("sermons.detail.transcript"), compte: d.transcript.length },
    { cle: "versets", label: t("sermons.detail.verses"), compte: d.verses.length },
    { cle: "notes", label: t("sermons.detail.notes") },
    ...(peutAssigner ? [{ cle: "rattachement", label: t("sermons.detail.assignment") }] : []),
  ];

  return (
    <div className="grid gap-5">
      {/* En-tête : le passage et la date, le thème en chapeau, puis les faits en une ligne. */}
      <header className="grid gap-3">
        <Retour />
        <div>
          <Kicker>{k.label}</Kicker>
          <h1 className="m-0 font-serif text-[2.2rem] font-normal leading-[1.05] text-deeper first-letter:uppercase">
            {dateFr(k.date, i18n.language)}
          </h1>
          {k.theme && <p className="mt-2 mb-0 max-w-[65ch] text-[1.05rem] leading-snug text-ink">{k.theme}</p>}
        </div>
        <dl className="m-0 flex flex-wrap gap-x-8 gap-y-2.5">
          <Fait label={t("sermons.detail.imamLabel")}>
            {d.imam?.display_name ?? (k.speaker || <span className="font-normal text-muted">{t("sermons.detail.noImam")}</span>)}
          </Fait>
          <Fait label={t("sermons.detail.hours")}>
            <span className="tabular-nums">{k.start_clock ?? "?"}–{k.end_clock ?? "?"}</span>
          </Fait>
          <Fait label={t("sermons.detail.length")}><span className="tabular-nums">{mmss(k.duration_s)}</span></Fait>
          <Fait label={t("sermons.detail.verses")}><span className="tabular-nums">{d.verses.length}</span></Fait>
          <Fait label={t("sermons.clips")}><span className="tabular-nums">{d.clips.length}</span></Fait>
        </dl>
      </header>

      <div className="grid items-start gap-4 min-[1000px]:grid-cols-[minmax(0,1.2fr)_minmax(380px,1fr)]">
        {/* Colonne gauche : la vidéo, puis les clips qu'elle a produits. */}
        <div className="grid gap-4">
          <div className="overflow-hidden rounded-[10px] bg-night">
            {d.has_video ? (
              <video
                ref={video}
                controls
                preload="metadata"
                playsInline
                onTimeUpdate={(e) => setTps(e.currentTarget.currentTime)}
                className="block max-h-[50vh] w-full bg-night object-contain"
                src={`/media/sermon/${k.id}.mp4`}
              />
            ) : (
              <div className="grid h-[30vh] min-h-[200px] place-items-center px-6 text-center">
                <p className="m-0 text-[0.95rem] text-night-muted">{t("sermons.detail.noVideo")}</p>
              </div>
            )}
            {/* Bandeau : où en est la lecture, et de quel passage il s'agit. */}
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-white/10 px-3.5 py-2 text-[0.82rem] text-night-muted">
              <span className="tabular-nums">
                {d.has_video ? `${mmss(tps)} / ${mmss(k.duration_s)}` : mmss(k.duration_s)}
              </span>
              <span>{k.label} · {t("sermons.detail.clipCount", { n: d.clips.length })}</span>
            </div>
          </div>

          <Card>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <Title>{t("sermons.detail.clipsTitle")}</Title>
              {clipsAValider > 0 && <Pill statut="a_valider" label={t("sermons.pendingCount", { n: clipsAValider })} />}
            </div>
            {d.clips.length === 0 ? (
              <p className="m-0 text-muted">{t("sermons.detail.noClips")}</p>
            ) : (
              <ul className="m-0 grid list-none divide-y divide-line p-0">
                {d.clips.map((c: any) => (
                  <li key={c.id}>
                    <Link
                      to={`/clips/${c.id}`}
                      className="group -mx-2 grid grid-cols-[2.25rem_minmax(0,1fr)_auto] items-center gap-3 rounded-[8px] px-2 py-2.5 no-underline transition-colors hover:bg-paper-2/60"
                    >
                      <span className="grid size-9 place-items-center rounded-full bg-paper-2 font-semibold tabular-nums text-deep">
                        {c.idx}
                      </span>
                      <span className="min-w-0">
                        <span className="block font-semibold text-deep first-letter:uppercase">
                          {String(t(`clips.kinds.${c.kind}`, { defaultValue: c.kind }))}
                        </span>
                        <span className="block text-[0.84rem] tabular-nums text-muted">
                          {mmss(c.start)}–{mmss(c.end)} · {t("clips.detail.version", { n: c.version })}
                        </span>
                      </span>
                      <span className="flex items-center gap-2.5">
                        <Pill statut={c.status} label={libelles[c.status] ?? c.status} />
                        <span aria-hidden className="text-[1.2rem] leading-none text-muted group-hover:text-deep">›</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* Colonne droite : un seul sujet à la fois ; le panneau colle et son contenu défile seul. */}
        <Card className="flex flex-col min-[1000px]:sticky min-[1000px]:top-4 min-[1000px]:max-h-[calc(100vh-2rem)]">
          <div>
            <Segmented
              items={onglets}
              valeur={ongletActif}
              onChange={(cle) => { setOnglet(cle); setMessage(null); }}
            />
          </div>
          {message && <Bandeau ko={message.ko}>{message.texte}</Bandeau>}

          <div className="relative mt-1 max-h-[70vh] min-h-0 flex-1 overflow-y-auto pr-1 min-[1000px]:max-h-none">
            {ongletActif === "transcription" && (
              <Transcription
                lignes={d.transcript}
                tps={tps}
                video={Boolean(d.has_video)}
                peutCorriger={peutCorriger}
                aller={aller}
                corriger={(r, valeur) => enregistrer(() => api.correction(kid, r.i, r.field, valeur))}
              />
            )}
            {ongletActif === "versets" && <Versets versets={d.verses} video={Boolean(d.has_video)} aller={aller} />}
            {ongletActif === "notes" && (
              d.notes ? (
                <div className="whitespace-pre-wrap text-[0.95rem] leading-relaxed text-ink">{d.notes}</div>
              ) : (
                <Vide>{t("sermons.detail.noNotes")}</Vide>
              )
            )}
            {ongletActif === "rattachement" && peutAssigner && (
              <Rattachement
                imams={d.imams ?? []}
                imamId={k.imam_id ? String(k.imam_id) : ""}
                intervenant={k.speaker ?? ""}
                onSave={(imamId, speaker) => enregistrer(() => api.intervenant(kid, imamId, speaker))}
              />
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

/** Retour à la liste des khutbas. */
function Retour() {
  const { t } = useTranslation();
  return (
    <Link to="/khutbas" className="justify-self-start text-[0.85rem] text-deep underline decoration-leaf underline-offset-4 hover:text-deeper">
      ← {t("nav.sermons")}
    </Link>
  );
}

/** Un fait de l'en-tête : petit libellé au-dessus, valeur en dessous. */
function Fait({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5">
      <dt className="text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted">{label}</dt>
      <dd className="m-0 text-[0.98rem] font-semibold text-deep">{children}</dd>
    </div>
  );
}

function Bandeau({ ko, children }: { ko: boolean; children: ReactNode }) {
  return (
    <p className={`m-0 mb-2 rounded-lg px-3 py-2 text-[0.88rem] leading-snug ${ko ? "bg-err-bg text-[#8a2219]" : "bg-paper-2 text-deep"}`}>
      {children}
    </p>
  );
}

function Vide({ children }: { children: ReactNode }) {
  return <p className="m-0 py-8 text-center text-[0.92rem] text-muted">{children}</p>;
}

/** Transcription : une ligne = un passage. Cliquer la ligne amène la vidéo et la lance ;
 *  la ligne en cours de lecture est surlignée. La correction est un bloc à part, sous la ligne :
 *  l'ouvrir ou y taper ne déplace jamais la vidéo. */
function Transcription({ lignes, tps, video, peutCorriger, aller, corriger }: {
  lignes: any[];
  tps: number;
  video: boolean;
  peutCorriger: boolean;
  aller: (s: number) => void;
  corriger: (r: any, valeur: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  if (!lignes.length) return <Vide>{t("common.empty")}</Vide>;

  return (
    <div className="grid gap-2">
      {video && <p className="m-0 text-[0.8rem] text-muted">{t("sermons.detail.seekHelp")}</p>}
      <ol className="m-0 grid list-none gap-1 p-0">
        {lignes.map((r: any) => {
          const actif = tps >= r.start && tps < r.end;
          const coran = Boolean(r.verse || r.coran);
          return (
            <li
              key={r.i}
              className={`rounded-[8px] transition-colors ${actif ? "bg-leaf/12 shadow-[inset_3px_0_0_var(--color-green)]" : "hover:bg-paper-2/70"}`}
            >
              <button
                type="button"
                onClick={() => aller(r.start)}
                disabled={!video}
                title={video ? t("sermons.detail.seek") : undefined}
                className="grid w-full grid-cols-[3.25rem_minmax(0,1fr)] gap-3 rounded-[8px] border-0 bg-transparent px-2.5 py-2 text-left enabled:cursor-pointer disabled:cursor-default"
              >
                <span className={`pt-0.5 text-[0.78rem] font-semibold tabular-nums ${actif ? "text-deeper" : coran ? "text-green" : "text-muted"}`}>
                  {mmss(r.start)}
                </span>
                <span className="grid min-w-0 gap-1">
                  {r.arabic && (
                    <span dir="rtl" className="block text-right font-arabic text-[1.15rem] leading-[1.9] text-deep">{r.text}</span>
                  )}
                  {r.verse && (
                    <>
                      <span className={`block text-[0.8rem] font-semibold ${r.verse.ambiguous ? "text-warn" : "text-green"}`}>
                        {r.verse.ref}{r.verse.surah ? ` · ${r.verse.surah}` : ""}
                        {r.verse.ambiguous ? ` · ${t("sermons.detail.ambiguous")}` : ""}
                      </span>
                      <span className="block font-serif text-[1.08rem] leading-snug text-deep">{r.verse.fr}</span>
                    </>
                  )}
                  {!r.verse && r.coran && (
                    <span className="block text-[0.85rem] italic text-muted">{t("sermons.detail.recitation")}</span>
                  )}
                  {!r.verse && !r.coran && r.fr && (
                    <span className="block text-[0.95rem] leading-relaxed text-ink">
                      {r.fr}
                      {r.corrected && (
                        <span className="ml-2 rounded-full bg-paper-2 px-1.5 text-[0.72rem] font-semibold text-deep">
                          {t("sermons.detail.corrected")}
                        </span>
                      )}
                    </span>
                  )}
                </span>
              </button>
              {peutCorriger && r.field && (
                // Bloc frère du bouton, pas enfant : cliquer ou taper ici ne lance jamais la vidéo.
                <details className="pr-2.5 pb-2 pl-[4.625rem]" onClick={(e) => e.stopPropagation()}>
                  <summary className="cursor-pointer text-[0.8rem] font-semibold text-muted hover:text-deep">
                    {r.field === "text" ? t("sermons.detail.fixSubtitle") : t("sermons.detail.fixTranslation")}
                  </summary>
                  <CorrectionForm
                    valeurInitiale={r.field === "fr" ? (r.fr ?? "") : r.text}
                    onSave={(valeur) => corriger(r, valeur)}
                  />
                </details>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Versets détectés : chaque carte amène la vidéo au moment de la citation. */
function Versets({ versets, video, aller }: { versets: any[]; video: boolean; aller: (s: number) => void }) {
  const { t } = useTranslation();
  if (!versets.length) return <Vide>{t("sermons.detail.noVerses")}</Vide>;
  return (
    <ul className="m-0 grid list-none gap-2 p-0">
      {versets.map((v: any, i: number) => (
        <li key={i}>
          <button
            type="button"
            onClick={() => aller(v.start)}
            disabled={!video}
            title={video ? t("sermons.detail.seek") : undefined}
            className="block w-full rounded-[8px] border border-line bg-white px-3.5 py-3 text-left transition-colors enabled:cursor-pointer enabled:hover:border-leaf enabled:hover:bg-paper-2/50 disabled:cursor-default"
          >
            <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
              <span className={`font-semibold ${v.ambiguous ? "text-warn" : "text-green"}`}>
                {v.ref}{v.surah_name ? ` · ${v.surah_name}` : ""}
              </span>
              <span className="text-[0.8rem] tabular-nums text-muted">{mmss(v.start)}</span>
              {v.ambiguous && (
                <span className="rounded-full bg-warn-bg px-2 text-[0.75rem] font-semibold text-warn">{t("sermons.detail.ambiguous")}</span>
              )}
              {v.via === "francais" && <span className="text-[0.78rem] text-muted">{t("sermons.detail.viaTranslation")}</span>}
            </span>
            {v.verse_fr && <span className="mt-1.5 block font-serif text-[1.08rem] leading-snug text-deep">{v.verse_fr}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Correction d'une ligne de transcription : un champ, un bouton, et le passage reste écoutable. */
function CorrectionForm({ valeurInitiale, onSave }: { valeurInitiale: string; onSave: (valeur: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [valeur, setValeur] = useState(valeurInitiale);
  const [envoi, setEnvoi] = useState(false);
  return (
    <div className="mt-1.5 grid gap-2">
      <textarea
        className={`${inputClass} min-h-[64px] py-2 text-[0.92rem]`}
        dir="auto"
        value={valeur}
        onChange={(e) => setValeur(e.target.value)}
      />
      <Btn
        variante="ghost"
        className="min-h-9 justify-self-start px-3 py-1 text-[0.85rem]"
        disabled={envoi || !valeur.trim() || valeur === valeurInitiale}
        onClick={async () => {
          setEnvoi(true);
          try {
            await onSave(valeur);
          } finally {
            setEnvoi(false);
          }
        }}
      >
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
    <div className="grid gap-3.5">
      <Field label={t("sermons.detail.imamLabel")}>
        <select className={`${inputClass} py-2`} value={choix} onChange={(e) => setChoix(e.target.value)}>
          <option value="">{t("sermons.detail.noImam")}</option>
          {imams.map((i) => <option key={i.id} value={i.id}>{i.display_name}</option>)}
        </select>
      </Field>
      <Field label={t("sermons.detail.speaker")}>
        <input className={`${inputClass} py-2`} value={nom} onChange={(e) => setNom(e.target.value)} />
      </Field>
      <Btn
        className="justify-self-start"
        disabled={envoi}
        onClick={async () => {
          setEnvoi(true);
          try {
            await onSave(choix, nom);
          } finally {
            setEnvoi(false);
          }
        }}
      >
        {envoi ? t("common.saving") : t("common.save")}
      </Btn>
    </div>
  );
}
