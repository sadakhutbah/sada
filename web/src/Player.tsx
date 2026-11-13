import { useCallback, useEffect, useRef, useState, type Ref, type VideoHTMLAttributes } from "react";
import { useTranslation } from "react-i18next";

/** Lecteur vidéo maison, aux couleurs de Sadā, sans dépendance.
 *  - Le <video> reste un vrai élément : `ref`, `onTimeUpdate`, `src`… passent tels quels, donc les
 *    pages qui sautent à un passage (SermonDetail) n'ont rien à changer.
 *  - Clavier quand le lecteur a le focus : espace / K lecture, ← → 5 s, M muet, F plein écran, C sous-titres.
 *  - Les contrôles se masquent après 2,5 s de lecture sans mouvement et reviennent au toucher.
 *  - Sous-titres : des `<track>` WebVTT fournis par l'appelant, un bouton CC (langue ou aucun) et le
 *    choix retenu d'une séance à l'autre. Sans piste, rien ne s'affiche : les clips ont leurs
 *    sous-titres incrustés et ne passent aucune piste. */

const VITESSES = [1, 1.25, 1.5, 2];
/** Choix de langue retenu dans le navigateur : code de langue, absent = sous-titres désactivés. */
const CLE_SOUS_TITRES = "sada-player-sous-titres";

/** Une piste servie par le serveur : langue BCP 47, libellé déjà traduit, adresse du WebVTT. */
export type Piste = { langue: string; libelle: string; url: string };

function hms(s: number) {
  if (!Number.isFinite(s) || s < 0) s = 0;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.floor(s % 60);
  const mm = h ? String(m).padStart(2, "0") : String(m);
  return (h ? `${h}:` : "") + `${mm}:${String(r).padStart(2, "0")}`;
}

type Props = VideoHTMLAttributes<HTMLVideoElement> & {
  ref?: Ref<HTMLVideoElement>;
  cadre?: string;
  pistes?: Piste[];
};

export default function Player({ ref, className = "", cadre = "", pistes = [], ...video }: Props) {
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
  // Sous-titres : langue choisie (vide = aucun), mémorisée par navigateur ; le menu est un popover du bouton CC.
  const [langue, setLangue] = useState<string>(() => {
    try { return window.localStorage.getItem(CLE_SOUS_TITRES) ?? ""; } catch { return ""; }  // stockage refusé
  });
  const [menu, setMenu] = useState(false);
  const menuBoite = useRef<HTMLDivElement | null>(null);
  const boutonCC = useRef<HTMLButtonElement | null>(null);
  // Dernière langue regardée : C la remet, même après avoir tout désactivé.
  const derniere = useRef(langue);
  const actif = pistes.some((p) => p.langue === langue);

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

  /** Une seule piste à l'écran : la langue choisie en « showing », les autres en « hidden » (chargées,
   *  prêtes à être choisies). Aucune langue : toutes cachées, rien ne s'affiche. */
  const appliquerPistes = useCallback(() => {
    const liste = v.current?.textTracks;
    if (!liste) return;
    for (let i = 0; i < liste.length; i += 1) {
      liste[i].mode = langue && liste[i].language === langue ? "showing" : "hidden";
    }
  }, [langue]);

  useEffect(() => {
    appliquerPistes();
  }, [appliquerPistes, pistes]);

  /** Choix retenu pour ce navigateur : il vaut pour la prochaine khutba, et pour les clips sans piste. */
  const choisir = (code: string) => {
    setLangue(code);
    if (code) derniere.current = code;
    try {
      if (code) window.localStorage.setItem(CLE_SOUS_TITRES, code);
      else window.localStorage.removeItem(CLE_SOUS_TITRES);
    } catch {
      /* stockage refusé (navigation privée) : le choix vaut pour cette page */
    }
    setMenu(false);
    boutonCC.current?.focus();  // le bouton reste l'arrêt de tabulation après la fermeture
  };

  const basculerSousTitres = () => {
    if (!pistes.length) return;
    const reprise = pistes.some((p) => p.langue === derniere.current) ? derniere.current : pistes[0].langue;
    choisir(actif ? "" : reprise);
  };

  // Menu ouvert : le focus entre sur la langue retenue, et un clic dehors le referme.
  useEffect(() => {
    if (!menu) return;
    const boite = menuBoite.current;
    const items = Array.from(boite?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? []);
    (items.find((el) => el.getAttribute("aria-checked") === "true") ?? items[0])?.focus();
    const dehors = (e: PointerEvent) => {
      const cible = e.target as Node;
      if (!boite?.contains(cible) && !boutonCC.current?.contains(cible)) setMenu(false);
    };
    document.addEventListener("pointerdown", dehors);
    return () => document.removeEventListener("pointerdown", dehors);
  }, [menu]);

  /** Flèches, Début/Fin entre les langues ; Échap referme et rend le focus au bouton CC. */
  const clavierMenu = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuBoite.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? []);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setMenu(false);
      boutonCC.current?.focus();
      return;
    }
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    let cible: HTMLButtonElement | undefined;
    if (e.key === "ArrowDown") cible = items[(Math.max(0, i) + 1) % items.length];
    else if (e.key === "ArrowUp") cible = items[(Math.max(0, i) - 1 + items.length) % items.length];
    else if (e.key === "Home") cible = items[0];
    else if (e.key === "End") cible = items[items.length - 1];
    else return;
    e.preventDefault();
    e.stopPropagation();  // la flèche ne déplace pas la vidéo
    cible?.focus();
  };

  const clavier = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT") return;
    if (menu) return;  // le menu des sous-titres a son propre clavier
    const k = e.key.toLowerCase();
    if (k === " " || k === "k") basculer();
    else if (k === "arrowleft") aller((v.current?.currentTime ?? 0) - 5);
    else if (k === "arrowright") aller((v.current?.currentTime ?? 0) + 5);
    else if (k === "m") muter();
    else if (k === "f") ecran();
    else if (k === "c") basculerSousTitres();
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
        onLoadedMetadata={(e) => {
          setDuree(e.currentTarget.duration);
          appliquerPistes();  // un nouveau fichier remet les pistes à « disabled » : on réapplique le choix
          video.onLoadedMetadata?.(e);
        }}
        onDurationChange={(e) => setDuree(e.currentTarget.duration)}
        onProgress={(e) => { const b = e.currentTarget.buffered; if (b.length) setTampon(b.end(b.length - 1)); }}
        onVolumeChange={(e) => { setMuet(e.currentTarget.muted); setVolume(e.currentTarget.volume); }}
        className={`${className} ${plein ? "!h-full !max-h-none !w-full" : ""}`}
      >
        {/* Les navigateurs ne lisent que du WebVTT : une piste par langue, jamais affichée par défaut. */}
        {pistes.map((p) => (
          <track key={`${p.langue}-${p.url}`} kind="subtitles" src={p.url} srcLang={p.langue} label={p.libelle} />
        ))}
      </video>

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
            {pistes.length > 0 && (
              <div className="relative">
                <button
                  ref={boutonCC}
                  type="button"
                  onClick={() => setMenu((m) => !m)}
                  aria-label={t("player.subtitles")}
                  aria-pressed={actif}
                  aria-haspopup="menu"
                  aria-expanded={menu}
                  title={t("player.subtitlesKey")}
                  className={`relative grid h-9 min-w-9 cursor-pointer place-items-center rounded-md px-1.5 focus-visible:outline-2 focus-visible:outline-leaf ${
                    actif ? "text-leaf hover:bg-white/10" : "text-night-ink/70 hover:bg-white/10 hover:text-night-ink"
                  }`}
                >
                  <span aria-hidden className="text-[0.72rem] leading-none font-bold tracking-[0.06em]">CC</span>
                  {/* Désactivé : le sigle barré, comme sur les téléviseurs. */}
                  {!actif && (
                    <span aria-hidden
                      className="absolute inset-x-1.5 top-1/2 h-[1.5px] -translate-y-1/2 -rotate-[18deg] rounded-full bg-current" />
                  )}
                </button>
                {menu && (
                  <div ref={menuBoite} role="menu" aria-label={t("player.subtitlesChoose")} onKeyDown={clavierMenu}
                    className="absolute right-0 bottom-full z-30 mb-2 min-w-[11rem] overflow-hidden rounded-[8px] border border-white/15 bg-night/95 py-1 text-left text-[0.86rem] text-night-ink shadow-[0_10px_28px_rgba(10,16,12,0.45)] backdrop-blur-sm">
                    <BoutonLangue coche={!actif} onClick={() => choisir("")}>{t("player.subtitlesOff")}</BoutonLangue>
                    {pistes.map((p) => (
                      <BoutonLangue key={p.langue} coche={langue === p.langue} onClick={() => choisir(p.langue)}>
                        {p.libelle}
                      </BoutonLangue>
                    ))}
                  </div>
                )}
              </div>
            )}
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

/** Une langue du menu des sous-titres : `menuitemradio`, cochée d'un trait de feuille. */
function BoutonLangue({ coche, onClick, children }: {
  coche: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={coche}
      onClick={onClick}
      className="flex w-full cursor-pointer items-center gap-2 px-2.5 py-1.5 text-left whitespace-nowrap hover:bg-white/10 focus-visible:bg-white/10 focus-visible:outline-none"
    >
      <span aria-hidden className="w-3.5 shrink-0 text-leaf">{coche ? "✓" : ""}</span>
      <span className={coche ? "font-semibold" : ""}>{children}</span>
    </button>
  );
}
