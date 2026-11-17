import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { playMonitor, type MonitorState } from "../monitor";
import { Btn } from "../ui";

/** Control room (“Régie”): two monitors, the scene grid and the transport of the live.
 *
 *  Preview = raw feed of the mosque, Programme = what goes on air. Both are played through the back
 *  office (WebRTC, HLS fallback), which only serves the mosque of the session to the `live` right. */

type Health = { online: boolean; kbps?: number | null; readers?: number } | null;
type Live = {
  ok?: boolean; state?: string; scene?: string | null; clock?: string | null;
  session?: { n?: number | null; total?: number; planned?: string | null; started?: string | null } | null;
  has_sessions?: boolean; next_session?: string | null; stream_on?: boolean; override?: boolean; paused?: boolean;
  last_reason?: string; janaza_name?: string | null; janaza_info?: string | null;
};
type Commande = { key: string; destructive: boolean; choices: string[] };

/** "HH:MM:SS" -> seconds of the day. */
function secondes(h?: string | null): number | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(h ?? "");
  return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3] ?? 0) : null;
}

function hms(s: number): string {
  const v = Math.max(0, Math.floor(s));
  const h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), r = v % 60;
  return `${h > 0 ? `${h}:` : ""}${String(m).padStart(h > 0 ? 2 : 1, "0")}:${String(r).padStart(2, "0")}`;
}

/** One monitor: 16:9 picture, muted by default, a clean “offline” slate when nothing is received. */
/** Studio mode: the scene staged in the preview, before it is sent on air. */
type Prepa = { scene: string; plein: boolean };

function Moniteur({ which, titre, sousTitre, available, sante, antenne, prepa, actions }: {
  which: "source" | "program"; titre: string; sousTitre: string; available: boolean; sante: Health; antenne: boolean;
  prepa?: Prepa | null; actions?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const video = useRef<HTMLVideoElement>(null);
  const [muet, setMuet] = useState(true);
  const [etat, setEtat] = useState<MonitorState>("connecting");
  useEffect(() => {
    if (!available || !video.current) return;
    return playMonitor(which, video.current, setEtat);
  }, [which, available]);
  const enLigne = available && (etat === "webrtc" || etat === "hls");
  return (
    <figure className="m-0 min-w-0">
      <figcaption className="mb-1.5 flex items-center justify-between gap-2">
        <span className="flex items-baseline gap-2">
          <span className="text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-leaf">{titre}</span>
          <span className="truncate text-[0.8rem] text-night-muted">{sousTitre}</span>
        </span>
        {antenne && enLigne && (
          <span className="rounded bg-live px-1.5 py-0.5 text-[0.7rem] font-bold uppercase tracking-wider text-white">
            {t("regie.onAir")}
          </span>
        )}
      </figcaption>
      <div className={`relative aspect-video overflow-hidden rounded-lg bg-black ring-2 ${antenne && enLigne ? "ring-live" : "ring-white/10"}`}>
        <video ref={video} autoPlay muted={muet} playsInline className={`size-full object-contain ${enLigne ? "" : "hidden"}`} />
        {prepa && (
          // Full screens hide the feed; a banner scene is drawn over it, as on air.
          <img src={`/api/v1/live/regie/scene/${prepa.scene}.png`} alt=""
               className={`absolute inset-0 z-10 size-full object-contain ${prepa.plein ? "bg-black" : ""}`} />
        )}
        {enLigne ? (
          <>
            <button
              type="button"
              onClick={() => setMuet((m) => !m)}
              aria-pressed={!muet}
              className="absolute right-2 bottom-2 z-20 inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-md border border-white/25 bg-black/60 px-2.5 text-[0.8rem] font-semibold text-white hover:bg-black/80 max-md:min-h-11"
            >
              <svg aria-hidden="true" viewBox="0 0 20 20" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 8h3l4-3v10l-4-3H3z" />
                {muet ? <path d="M13 8l4 4M17 8l-4 4" /> : <path d="M13.5 7.5a3.5 3.5 0 0 1 0 5M15.5 5.5a6.5 6.5 0 0 1 0 9" />}
              </svg>
              {muet ? t("regie.soundOn") : t("regie.soundOff")}
            </button>
          </>
        ) : prepa ? null : (
          <div className="flex size-full flex-col items-center justify-center gap-1.5 bg-[repeating-linear-gradient(135deg,#0b1d18_0_14px,#0e231d_14px_28px)] text-center">
            <span className="inline-flex items-center gap-2 text-[0.95rem] font-semibold text-night-ink">
              <span className={`inline-block size-2 rounded-full ${available && etat === "connecting" ? "animate-pulse bg-gold" : "bg-dot-off"}`} />
              {available && etat === "connecting" ? t("regie.connecting") : t("regie.offline")}
            </span>
            <span className="max-w-[80%] text-[0.8rem] text-night-muted">{t("regie.offlineHint")}</span>
          </div>
        )}
      </div>
      <div className="mt-1 flex min-h-[1.2rem] items-center gap-3">
      <p className="m-0 flex gap-3 text-[0.78rem] text-night-muted tabular-nums">
        {enLigne && <span>{etat === "webrtc" ? "WebRTC" : "HLS"}</span>}
        {sante?.online ? (
          <>
            {sante.kbps != null && <span>{t("regie.kbps", { n: sante.kbps.toLocaleString() })}</span>}
            {sante.readers != null && <span>{t("regie.readers", { n: sante.readers })}</span>}
          </>
        ) : !enLigne && <span>&nbsp;</span>}
      </p>
      {actions && <div className="ml-auto flex gap-2">{actions}</div>}
      </div>
    </figure>
  );
}

/** Narrower than an unfolded foldable or a tablet: a phone. The control room asks for a wider
 *  screen there, without forbidding it (soft block). */
const ETROIT = "(max-width: 599px)";

function useEtroit(): boolean {
  const [v, setV] = useState(() => window.matchMedia(ETROIT).matches);
  useEffect(() => {
    const mq = window.matchMedia(ETROIT);
    const f = () => setV(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, []);
  return v;
}

export default function Regie() {
  const etroit = useEtroit();
  const [forcer, setForcer] = useState(() => sessionStorage.getItem("regie.mobile") === "1");
  const { t } = useTranslation();
  if (etroit && !forcer) {
    return (
      <div className="mx-auto grid max-w-sm gap-3 rounded-[10px] bg-night p-5 text-night-ink">
        <h1 className="m-0 text-[1.1rem] font-bold text-white">{t("regie.narrowTitle")}</h1>
        <p className="m-0 text-[0.92rem] leading-snug">{t("regie.narrowText")}</p>
        <Btn variante="light" className="justify-self-start" onClick={() => { sessionStorage.setItem("regie.mobile", "1"); setForcer(true); }}>
          {t("regie.narrowAnyway")}
        </Btn>
      </div>
    );
  }
  return <RegieEcran />;
}

function RegieEcran() {
  const { t } = useTranslation();
  const [live, setLive] = useState<Live | null>(null);
  const [commandes, setCommandes] = useState<Commande[]>([]);
  const [moniteurs, setMoniteurs] = useState<Record<string, { available: boolean; health: Health }>>({});
  const [lu, setLu] = useState(() => Date.now());
  const [tic, setTic] = useState(() => Date.now());
  const [occupe, setOccupe] = useState(false);
  const [retour, setRetour] = useState<{ ok: boolean; texte: string } | null>(null);
  const [nom, setNom] = useState("");
  const [mention, setMention] = useState("");
  const [prepa, setPrepa] = useState<string | null>(null);

  // State refreshed every 3 s; a second ticker moves the clock and the duration in between.
  useEffect(() => {
    let vivant = true;
    let minuteur = 0;
    const lire = async () => {
      try {
        const d = await api.regie();
        if (!vivant) return;
        setLive(d.live ?? {}); setLu(Date.now());
        setCommandes(d.commands ?? []);
        setMoniteurs(d.monitors ?? {});
      } catch {
        if (vivant) setLive((v) => v ?? { ok: false });
      }
      if (vivant) minuteur = window.setTimeout(lire, 3000);
    };
    lire();
    return () => { vivant = false; window.clearTimeout(minuteur); };
  }, []);
  useEffect(() => {
    const id = window.setInterval(() => setTic(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  async function envoyer(key: string, corps: Record<string, string> = {}) {
    const c = commandes.find((x) => x.key === key);
    if (!c) return;
    if (c.destructive && !window.confirm(t(`dashboard.confirm.${key}`))) return;
    setOccupe(true);
    setRetour(null);
    try {
      const r = await api.liveCommande(key, corps);
      setRetour({ ok: true, texte: r.message });
      if (r.live) { setLive(r.live); setLu(Date.now()); }
      if (key === "scene") setPrepa(null);
    } catch (e: any) {
      setRetour({ ok: false, texte: e?.message ?? t("common.error") });
    } finally {
      setOccupe(false);
    }
  }

  if (!live) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const horsService = live.ok === false;
  const etat = horsService
    ? t("dashboard.unreachable")
    : (live.state && t(`dashboard.state.${live.state}`, { defaultValue: live.state })) || t("dashboard.liveOff");
  const ecoule = (tic - lu) / 1000;
  const horlogeS = secondes(live.clock);
  const maintenant = horlogeS != null ? horlogeS + ecoule : null;
  const debut = secondes(live.session?.started);
  const duree = live.stream_on && maintenant != null && debut != null ? hms(maintenant - debut) : "—";
  const scenes = commandes.find((c) => c.key === "scene")?.choices ?? [];
  const extend = commandes.find((c) => c.key === "extend")?.choices ?? [];
  const a = (key: string) => commandes.some((c) => c.key === key);
  const pastille = horsService ? "bg-dot-off" : live.stream_on ? "bg-live" : "bg-leaf";
  const panneau = "rounded-[10px] bg-night p-4 text-night-ink";
  const enDirect = !!live.stream_on;
  const enPause = !!live.paused;
  const plein = (sc: string) => sc !== "khutba" && sc !== "dars";
  const petit = "min-h-9 px-3 py-1.5 text-[0.9rem]";

  return (
    <div className="grid gap-3">
      {/* Status strip: engine state, time on air, next session, automatic mode. */}
      <section className={`${panneau} flex flex-wrap items-center gap-x-6 gap-y-2 py-3 lg:flex-nowrap`}>
        <p className="m-0 flex items-center gap-2.5 text-[1.25rem] font-bold leading-none text-white">
          <span className={`inline-block size-2.5 shrink-0 rounded-full ${pastille} ${live.stream_on ? "animate-pulse" : ""}`} />
          {etat}
        </p>
        {!horsService && (
          <dl className="m-0 flex min-w-0 flex-wrap gap-x-5 gap-y-1 text-[0.9rem]">
            {[
              [t("regie.clock"), maintenant != null ? hms(maintenant % 86400) : "—"],
              [t("regie.onAirFor"), duree],
              [t("regie.next"), live.next_session ?? "—"],
              [t("regie.mode"), live.override ? t("regie.manual") : t("regie.auto")],
            ].map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-1.5">
                <dt className="text-night-muted">{k}</dt>
                <dd className="m-0 font-semibold text-white tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {horsService && <p className="m-0 text-[0.9rem] text-night-muted">{t("dashboard.unreachableHint")}</p>}
      </section>

      {/* Monitors: preview (the mosque's raw feed) and programme (what goes on air). */}
      <section className={`${panneau} grid gap-4 py-3 md:grid-cols-2`}>
          <Moniteur which="source" titre={t("regie.preview")}
                    sousTitre={prepa ? t("regie.staged", { scene: t(`dashboard.choice.${prepa}`) }) : t("regie.previewHint")}
                    available={!!moniteurs.source?.available} sante={moniteurs.source?.health ?? null} antenne={false}
                    prepa={prepa ? { scene: prepa, plein: plein(prepa) } : null}
                    actions={prepa && (
                      <>
                        <Btn variante="light" className="min-h-8 px-2.5 py-1 text-[0.85rem]" disabled={occupe} onClick={() => setPrepa(null)}>{t("regie.unstage")}</Btn>
                        <Btn variante="leaf" className="min-h-8 px-2.5 py-1 text-[0.85rem]" disabled={occupe} onClick={() => envoyer("scene", { valeur: prepa })}>{t("regie.take")}</Btn>
                      </>
                    )} />
        <Moniteur which="program" titre={t("regie.program")} sousTitre={t("regie.programHint")}
                  available={!!moniteurs.program?.available} sante={moniteurs.program?.health ?? null} antenne={!!live.stream_on} />
      </section>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_minmax(0,0.85fr)]">
        {/* Scenes: the screens of the mosque; the active one is outlined, a click puts it on air. */}
        <section className={`${panneau} md:col-span-2 xl:col-span-1`}>
          <h2 className="m-0 mb-2.5 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-leaf">{t("regie.scenes")}</h2>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(88px,1fr))] gap-2">
            {scenes.map((sc) => {
              const actif = live.scene === sc;
              const prete = prepa === sc;
              return (
                <button key={sc} type="button" disabled={occupe} aria-pressed={prete}
                        onClick={() => setPrepa(prete || actif ? null : sc)}
                        className={`flex min-h-12 cursor-pointer flex-col items-start justify-center rounded-lg border px-2.5 py-1.5 text-left transition-colors disabled:cursor-not-allowed ${
                          actif ? "border-live bg-live/20 text-white"
                            : prete ? "border-leaf bg-leaf/20 text-white"
                            : "border-white/15 bg-white/5 text-night-ink hover:border-leaf hover:bg-white/10"
                        }`}>
                  <span className="text-[0.9rem] leading-tight font-semibold">{t(`dashboard.choice.${sc}`)}</span>
                  <span className={`text-[0.72rem] ${actif ? "text-[#ffb4ae]" : prete ? "text-leaf" : "text-night-muted"}`}>
                    {actif ? t("regie.sceneActive") : prete ? t("regie.sceneStaged") : plein(sc) ? t("regie.sceneScreen") : t("regie.sceneCamera")}
                  </span>
                </button>
              );
            })}
          </div>
        </section>

        {/* Transport: start, pause/resume, extend, skip, automatic; stop and cancel apart, in red. */}
        <section className={`${panneau} grid content-start gap-2.5`}>
          <h2 className="m-0 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-leaf">{t("regie.transport")}</h2>
          <div className="flex flex-wrap gap-2">
            {/* The transport follows the state: start only off air, pause or resume depending on the pause. */}
            {a("start") && !enDirect && <Btn variante="leaf" className={petit} disabled={occupe} onClick={() => envoyer("start")}>{t("dashboard.cmd.start")}</Btn>}
            {a("pause") && enDirect && !enPause && <Btn variante="light" className={petit} disabled={occupe} onClick={() => envoyer("pause")}>{t("dashboard.cmd.pause")}</Btn>}
            {a("resume") && enPause && <Btn variante="leaf" className={petit} disabled={occupe} onClick={() => envoyer("resume")}>{t("dashboard.cmd.resume")}</Btn>}
            {extend.map((m) => (
              <Btn key={m} variante="light" className={petit} disabled={occupe} onClick={() => envoyer("extend", { valeur: m })}>{t(`dashboard.choice.${m}`)}</Btn>
            ))}
            {a("skip") && <Btn variante="light" className={petit} disabled={occupe} onClick={() => envoyer("skip")}>{t("regie.skip")}</Btn>}
            {a("auto") && (
              <Btn variante={live.override ? "leaf" : "light"} className={petit} disabled={occupe || !live.override} onClick={() => envoyer("auto")}>
                {live.override ? t("regie.autoResume") : t("regie.autoOn")}
              </Btn>
            )}
          </div>
          <div className="flex flex-wrap gap-2 border-t border-white/10 pt-2.5">
            {a("stop") && <Btn variante="danger" className={petit} disabled={occupe} onClick={() => envoyer("stop")}>{t("dashboard.cmd.stop")}</Btn>}
            {a("cancel") && <Btn variante="danger" className={petit} disabled={occupe} onClick={() => envoyer("cancel")}>{t("dashboard.cmd.cancel")}</Btn>}
          </div>
          {retour && <p role="status" className={`m-0 text-[0.88rem] ${retour.ok ? "text-leaf" : "text-[#ff9b94]"}`}>{retour.texte}</p>}
        </section>

        {a("janaza") && (
          <section className={`${panneau} grid content-start gap-2`}>
            <h2 className="m-0 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-leaf">{t("dashboard.janaza")}</h2>
            {live.janaza_name && (
              <p className="m-0 text-[0.88rem] text-white">
                <span className="text-night-muted">{t("regie.janazaShown")}</span> {live.janaza_name}{live.janaza_info ? ` · ${live.janaza_info}` : ""}
              </p>
            )}
            <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted max-md:min-h-11"
                   placeholder={t("dashboard.janazaName")} value={nom} maxLength={80} onChange={(e) => setNom(e.target.value)} />
            <input className="rounded-md border border-white/25 bg-white/10 px-2.5 py-1.5 text-[0.9rem] text-white placeholder:text-night-muted max-md:min-h-11"
                   placeholder={t("dashboard.janazaInfo")} value={mention} maxLength={60} onChange={(e) => setMention(e.target.value)} />
            <Btn variante="leaf" className={`${petit} justify-self-start`} disabled={occupe || !nom.trim()}
                 onClick={() => envoyer("janaza", { name: nom.trim(), info: mention.trim() })}>
              {t("dashboard.janazaSend")}
            </Btn>
            <p className="m-0 text-[0.76rem] leading-snug text-night-muted">{t("regie.janazaHint")}</p>
          </section>
        )}
      </div>
    </div>
  );
}
