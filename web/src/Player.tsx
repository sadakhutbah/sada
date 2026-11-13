import { useEffect, useRef, useState, type Ref, type VideoHTMLAttributes } from "react";
import { useTranslation } from "react-i18next";

/** Lecteur vidéo maison, aux couleurs de Sadā, sans dépendance.
 *  - Le <video> reste un vrai élément : `ref`, `onTimeUpdate`, `src`… passent tels quels, donc les
 *    pages qui sautent à un passage (SermonDetail) n'ont rien à changer.
 *  - Clavier quand le lecteur a le focus : espace / K lecture, ← → 5 s, M muet, F plein écran.
 *  - Les contrôles se masquent après 2,5 s de lecture sans mouvement et reviennent au toucher. */

const VITESSES = [1, 1.25, 1.5, 2];

function hms(s: number) {
  if (!Number.isFinite(s) || s < 0) s = 0;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(r).padStart(2, "0")}`;
}

type Props = VideoHTMLAttributes<HTMLVideoElement> & { ref?: Ref<HTMLVideoElement>; cadre?: string };

export default function Player({ ref, className = "", cadre = "", ...video }: Props) {
  const { t } = useTranslation();
  const boite = useRef<HTMLDivElement>(null);
  const v = useRef<HTMLVideoElement | null>(null);
  const [lecture, setLecture] = useState(false);
  const [tps, setTps] = useState(0);
  const [duree, setDuree] = useState(0);
  const [tampon, setTampon] = useState(0);
  const [muet, setMuet] = useState(false);
  const [volume, setVolume] = useState(1);
  const [vitesse, setVitesse] = useState(1);
  const [plein, setPlein] = useState(false);
  const [visible, setVisible] = useState(true);
  const minuteur = useRef<number | undefined>(undefined);

  const lier = (el: HTMLVideoElement | null) => {
    v.current = el;
    if (typeof ref === "function") ref(el);
    else if (ref) (ref as { current: HTMLVideoElement | null }).current = el;
  };

  const reveiller = () => {
    setVisible(true);
    window.clearTimeout(minuteur.current);
    minuteur.current = window.setTimeout(() => { if (v.current && !v.current.paused) setVisible(false); }, 2500);
  };

  useEffect(() => {
    const f = () => setPlein(document.fullscreenElement === boite.current);
    document.addEventListener("fullscreenchange", f);
    return () => { document.removeEventListener("fullscreenchange", f); window.clearTimeout(minuteur.current); };
  }, []);

  const basculer = () => { const el = v.current; if (!el) return; if (el.paused) el.play().catch(() => {}); else el.pause(); };
  const aller = (s: number) => { const el = v.current; if (el) el.currentTime = Math.min(Math.max(0, s), el.duration || s); };
  const muter = () => { const el = v.current; if (el) el.muted = !el.muted; };
  const ecran = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else boite.current?.requestFullscreen?.().catch(() => {});
  };
  const changerVitesse = () => {
    const n = VITESSES[(VITESSES.indexOf(vitesse) + 1) % VITESSES.length];
    if (v.current) v.current.playbackRate = n;
    setVitesse(n);
  };

  const clavier = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    const k = e.key.toLowerCase();
    if (k === " " || k === "k") basculer();
    else if (k === "arrowleft") aller((v.current?.currentTime ?? 0) - 5);
    else if (k === "arrowright") aller((v.current?.currentTime ?? 0) + 5);
    else if (k === "m") muter();
    else if (k === "f") ecran();
    else return;
    e.preventDefault();
    reveiller();
  };

  const pct = duree ? (tps / duree) * 100 : 0;
  const pctTampon = duree ? (tampon / duree) * 100 : 0;
  const montre = visible || !lecture;

  return (
    <div
      ref={boite}
      tabIndex={0}
      onKeyDown={clavier}
      onMouseMove={reveiller}
      onTouchStart={reveiller}
      onMouseLeave={() => lecture && setVisible(false)}
      className={`group relative isolate overflow-hidden bg-night outline-none focus-visible:ring-2 focus-visible:ring-leaf ${plein ? "grid place-items-center" : ""} ${cadre} ${montre ? "" : "cursor-none"}`}
    >
      <video
        {...video}
        ref={lier}
        controls={false}
        playsInline
        onClick={basculer}
        onPlay={(e) => { setLecture(true); reveiller(); video.onPlay?.(e); }}
        onPause={(e) => { setLecture(false); setVisible(true); video.onPause?.(e); }}
        onTimeUpdate={(e) => { setTps(e.currentTarget.currentTime); video.onTimeUpdate?.(e); }}
        onLoadedMetadata={(e) => { setDuree(e.currentTarget.duration); video.onLoadedMetadata?.(e); }}
        onDurationChange={(e) => setDuree(e.currentTarget.duration)}
        onProgress={(e) => { const b = e.currentTarget.buffered; if (b.length) setTampon(b.end(b.length - 1)); }}
        onVolumeChange={(e) => { setMuet(e.currentTarget.muted); setVolume(e.currentTarget.volume); }}
        className={`${className} ${plein ? "!h-full !max-h-none !w-full" : ""}`}
      />

      {/* Gros bouton central tant que la vidéo est à l'arrêt. */}
      {!lecture && (
        <button
          type="button"
          onClick={basculer}
          aria-label={t("player.play")}
          className="absolute top-1/2 left-1/2 grid size-16 -translate-x-1/2 -translate-y-1/2 cursor-pointer place-items-center rounded-full border border-leaf/60 bg-night/70 text-leaf backdrop-blur-sm transition hover:scale-105 hover:bg-night/85"
        >
          <svg viewBox="0 0 24 24" className="ml-1 size-7" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15l13-7.5z" /></svg>
        </button>
      )}

      {/* Barre de contrôle. */}
      <div
        className={`absolute inset-x-0 bottom-0 bg-gradient-to-t from-night/95 via-night/70 to-transparent px-3 pt-8 pb-2.5 text-night-ink transition-opacity duration-200 ${montre ? "opacity-100" : "pointer-events-none opacity-0"}`}
      >
        {/* Progression : filet d'or sur fond translucide, la zone tamponnée en plus clair. */}
        <div className="relative h-4">
          <div className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-white/15" />
          <div className="absolute top-1/2 left-0 h-[3px] -translate-y-1/2 rounded-full bg-white/25" style={{ width: `${pctTampon}%` }} />
          <div className="absolute top-1/2 left-0 h-[3px] -translate-y-1/2 rounded-full bg-leaf" style={{ width: `${pct}%` }} />
          <div className="pointer-events-none absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-leaf shadow-[0_0_0_3px_rgba(226,194,122,0.25)]" style={{ left: `${pct}%` }} />
          <input
            type="range"
            min={0}
            max={duree || 0}
            step={0.1}
            value={tps}
            onChange={(e) => aller(Number(e.target.value))}
            aria-label={t("player.seek")}
            className="absolute inset-0 w-full cursor-pointer opacity-0"
          />
        </div>

        <div className="mt-1 flex items-center gap-1">
          <Bouton label={lecture ? t("player.pause") : t("player.play")} onClick={basculer}>
            {lecture
              ? <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" />
              : <path d="M7 4.5v15l13-7.5z" />}
          </Bouton>
          <Bouton label={t("player.back")} onClick={() => aller(tps - 10)}>
            <path d="M12 5V2L7 6l5 4V7a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z" />
          </Bouton>
          <Bouton label={t("player.forward")} onClick={() => aller(tps + 10)}>
            <path d="M12 5V2l5 4-5 4V7a6 6 0 1 0 6 6h2a8 8 0 1 1-8-8z" />
          </Bouton>
          <span className="ml-1.5 text-[0.82rem] tabular-nums text-night-ink/90">
            {hms(tps)} <span className="text-night-muted">/ {hms(duree)}</span>
          </span>

          <span className="ml-auto flex items-center gap-1">
            <Bouton label={muet || volume === 0 ? t("player.unmute") : t("player.mute")} onClick={muter}>
              {muet || volume === 0
                ? <path d="M3 9v6h4l5 5V4L7 9H3zm13.59 3L14 9.41 15.41 8 18 10.59 20.59 8 22 9.41 19.41 12 22 14.59 20.59 16 18 13.41 15.41 16 14 14.59z" />
                : <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" />}
            </Bouton>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={muet ? 0 : volume}
              onChange={(e) => { const el = v.current; if (el) { el.volume = Number(e.target.value); el.muted = el.volume === 0; } }}
              aria-label={t("player.volume")}
              className="hidden w-20 cursor-pointer accent-[var(--color-leaf)] sm:block"
            />
            <button
              type="button"
              onClick={changerVitesse}
              aria-label={t("player.speed")}
              className="min-w-11 cursor-pointer rounded-md px-1.5 py-1 text-[0.8rem] font-semibold tabular-nums text-night-ink hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-leaf"
            >
              {vitesse}×
            </button>
            <Bouton label={plein ? t("player.exitFullscreen") : t("player.fullscreen")} onClick={ecran}>
              {plein
                ? <path d="M8 3v5H3v2h7V3zm8 0h-2v7h7V8h-5zM3 16h5v5h2v-7H3zm11 5h2v-5h5v-2h-7z" />
                : <path d="M3 3h7v2H5v5H3zm11 0h7v7h-2V5h-5zM3 14h2v5h5v2H3zm16 5h-5v2h7v-7h-2z" />}
            </Bouton>
          </span>
        </div>
      </div>
    </div>
  );
}

function Bouton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="grid size-9 cursor-pointer place-items-center rounded-md text-night-ink hover:bg-white/10 hover:text-leaf focus-visible:outline-2 focus-visible:outline-leaf"
    >
      <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" aria-hidden="true">{children}</svg>
    </button>
  );
}
