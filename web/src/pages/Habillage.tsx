import { useEffect, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, type Habillage as Etat, type HabillageOptions } from "../api";
import { Btn, Card, Field, Kicker, Pill, Segmented, Tabs, inputClass } from "../ui";
import { idOnglet, idPanneau } from "../onglets";

/** Branding of an imported video (dars, lecture, khutba): the mosque's identity over the picture.
 *  A single form, split in tabs (opening, speaker, logo and colour, end screen). On the right, a live
 *  mock-up that follows every change, the frames rendered by the server (“Update the preview”),
 *  then the rendering status and the download. The original video is never touched. */

type Onglet = "ouverture" | "intervenant" | "logo" | "fin";
type Message = { texte: string; ko: boolean } | null;

const POSITIONS = ["tl", "tr", "bl", "br"] as const;
const TAILLES = ["s", "m", "l"] as const;

export default function Habillage() {
  const { id } = useParams();
  const kid = Number(id);
  const { t } = useTranslation();
  const [etat, setEtat] = useState<Etat | null>(null);
  const [o, setO] = useState<HabillageOptions | null>(null);
  const [onglet, setOnglet] = useState<Onglet>("ouverture");
  const [occupe, setOccupe] = useState<"" | "apercu" | "rendu">("");
  const [message, setMessage] = useState<Message>(null);
  const [erreur, setErreur] = useState("");

  useEffect(() => {
    api.habillage(kid).then((e) => { setEtat(e); setO(e.options); }).catch((e) => setErreur(e?.message ?? t("common.error")));
  }, [kid, t]);

  // While rendering (or translating the subtitle tracks), the status is polled every 2 s; the form
  // keeps what is being typed.
  const renduEnCours = etat?.status === "en_cours";
  const traductionEnCours = etat?.translation?.status === "en_cours";
  useEffect(() => {
    if (!renduEnCours && !traductionEnCours) return;
    const h = window.setInterval(() => { api.habillage(kid).then(setEtat).catch(() => {}); }, 2000);
    return () => window.clearInterval(h);
  }, [renduEnCours, traductionEnCours, kid]);

  /** Relit l'état après une action (traduction lancée), sans toucher au formulaire en cours. */
  const rafraichir = () => { api.habillage(kid).then(setEtat).catch(() => {}); };

  if (erreur) return <p className="text-[#7a1f20]">{erreur}</p>;
  if (!etat || !o) return <p className="text-muted">{t("common.loading")}</p>;

  const lecture = !etat.can_edit;
  const bloque = lecture || !etat.has_source || occupe !== "" || renduEnCours;
  const maj = (champs: Partial<HabillageOptions>) => setO({ ...o, ...champs });

  async function agir(quoi: "apercu" | "rendu", e?: FormEvent) {
    e?.preventDefault();
    if (!o) return;
    setOccupe(quoi);
    setMessage(null);
    try {
      const r = quoi === "apercu" ? await api.habillageApercu(kid, o) : await api.habillageLancer(kid, o);
      setEtat(r);
      setO(r.options);
      if (quoi === "rendu") setMessage({ texte: r.message, ko: false });
    } catch (err: any) {
      setMessage({ texte: err?.message ?? t("common.error"), ko: true });
    } finally {
      setOccupe("");
    }
  }

  const onglets: { cle: Onglet; label: string; actif: boolean }[] = [
    { cle: "ouverture", label: t("habillage.tabs.ouverture"), actif: o.intro_on },
    { cle: "intervenant", label: t("habillage.tabs.intervenant"), actif: o.lower_on },
    { cle: "logo", label: t("habillage.tabs.logo"), actif: o.logo_on || o.bar_on },
    { cle: "fin", label: t("habillage.tabs.fin"), actif: o.outro_on },
  ];

  return (
    <div className="grid gap-5">
      <header className="grid gap-3">
        <Link to={`/khutbas/${kid}`} className="justify-self-start text-[0.85rem] text-deep underline decoration-leaf underline-offset-4 hover:text-deeper max-md:py-[13px] pointer-coarse:py-[13px]">
          ← {t("habillage.back")}
        </Link>
        <div>
          <Kicker>{t("habillage.kicker")}</Kicker>
          <h1 className="m-0 font-serif text-[2.2rem] font-normal leading-[1.05] text-deeper">
            {o.title || etat.mosque}
          </h1>
          <p className="mt-2 mb-0 max-w-[68ch] text-[1.02rem] leading-snug text-muted">{t("habillage.intro")}</p>
        </div>
      </header>

      {!etat.has_source && <Bandeau ko>{t("habillage.noSource")}</Bandeau>}
      {lecture && etat.has_source && <Bandeau>{t("habillage.readOnly")}</Bandeau>}

      <div className="grid items-start gap-4 min-[1000px]:grid-cols-[minmax(0,1fr)_minmax(400px,1.05fr)]">
        {/* Left: the single form, one tab per element. */}
        <Card className="p-0!">
          <form onSubmit={(e) => agir("rendu", e)} className="grid gap-4 p-5">
            <Tabs
              items={onglets.map((it) => ({ cle: it.cle, label: it.label }))}
              valeur={onglet}
              onChange={(c) => setOnglet(c as Onglet)}
              base="habillage"
            />
            <div className="-mt-2 flex flex-wrap gap-1.5" aria-hidden>
              {onglets.map((it) => (
                <span key={it.cle} className={`h-1 flex-1 rounded-full ${it.actif ? "bg-leaf" : "bg-paper-2"}`} />
              ))}
            </div>

            <fieldset disabled={lecture} className="m-0 grid min-w-0 gap-4 border-0 p-0"
              role="tabpanel" id={idPanneau("habillage", onglet)} aria-labelledby={idOnglet("habillage", onglet)}>
              {onglet === "ouverture" && (
                <>
                  <Interrupteur label={t("habillage.introOn")} aide={t("habillage.introHelp")}
                    valeur={o.intro_on} onChange={(v) => maj({ intro_on: v })} />
                  <Groupe actif={o.intro_on}>
                    <Field label={t("habillage.title")}>
                      <input className={inputClass} value={o.title} maxLength={140} onChange={(e) => maj({ title: e.target.value })} />
                    </Field>
                    <Field label={t("habillage.subtitle")} aide={t("habillage.subtitleHelp")}>
                      <input className={inputClass} value={o.subtitle} maxLength={140} onChange={(e) => maj({ subtitle: e.target.value })} />
                    </Field>
                    <Nombre label={t("habillage.seconds")} valeur={o.intro_s} min={2} max={15} onChange={(v) => maj({ intro_s: v })} />
                  </Groupe>
                </>
              )}

              {onglet === "intervenant" && (
                <>
                  <Interrupteur label={t("habillage.lowerOn")} aide={t("habillage.lowerHelp")}
                    valeur={o.lower_on} onChange={(v) => maj({ lower_on: v })} />
                  <Groupe actif={o.lower_on}>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label={t("habillage.speaker")}>
                        <input className={inputClass} value={o.speaker} maxLength={80} onChange={(e) => maj({ speaker: e.target.value })} />
                      </Field>
                      <Field label={t("habillage.role")} aide={t("habillage.roleHelp")}>
                        <input className={inputClass} value={o.role} maxLength={80} onChange={(e) => maj({ role: e.target.value })} />
                      </Field>
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Nombre label={t("habillage.lowerAt")} valeur={o.lower_at} min={0} max={600} onChange={(v) => maj({ lower_at: v })} />
                      <Nombre label={t("habillage.lowerS")} valeur={o.lower_s} min={3} max={30} onChange={(v) => maj({ lower_s: v })} />
                    </div>
                  </Groupe>
                </>
              )}

              {onglet === "logo" && (
                <>
                  <Interrupteur label={t("habillage.logoOn")} aide={etat.has_logo ? t("habillage.logoHelp") : t("habillage.noLogo")}
                    valeur={o.logo_on && etat.has_logo} eteint={!etat.has_logo} onChange={(v) => maj({ logo_on: v })} />
                  <Groupe actif={o.logo_on && etat.has_logo}>
                    <Field label={t("habillage.position")}>
                      <div className="grid w-fit grid-cols-2 gap-1.5 rounded-[10px] border border-line bg-paper-2 p-1.5">
                        {POSITIONS.map((p) => (
                          <button key={p} type="button" onClick={() => maj({ logo_pos: p })}
                            title={t(`habillage.positions.${p}`)} aria-label={t(`habillage.positions.${p}`)} aria-pressed={o.logo_pos === p}
                            className={`relative h-11 w-[78px] cursor-pointer rounded-[6px] border ${o.logo_pos === p ? "border-deep bg-white shadow-[inset_0_-2px_0_var(--color-leaf)]" : "border-transparent bg-white/50 hover:bg-white"}`}>
                            <span className={`absolute size-2.5 rounded-full ${o.logo_pos === p ? "bg-deep" : "bg-muted/50"} ${p[0] === "t" ? "top-1.5" : "bottom-1.5"} ${p[1] === "l" ? "left-1.5" : "right-1.5"}`} />
                          </button>
                        ))}
                      </div>
                      <span className="mt-1 block text-[0.82rem] text-muted">{t(`habillage.positions.${o.logo_pos}`)}</span>
                    </Field>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <Field label={t("habillage.size")}>
                        <Segmented items={TAILLES.map((s) => ({ cle: s, label: t(`habillage.sizes.${s}`) }))}
                          valeur={o.logo_size} onChange={(s) => maj({ logo_size: s as HabillageOptions["logo_size"] })} />
                      </Field>
                      <Field label={`${t("habillage.opacity")} · ${Math.round(o.logo_opacity * 100)} %`}>
                        <input type="range" min={0.2} max={1} step={0.05} value={o.logo_opacity}
                          onChange={(e) => maj({ logo_opacity: Number(e.target.value) })} className="mt-2 w-full accent-[var(--color-deep)]" />
                      </Field>
                    </div>
                  </Groupe>
                  <hr className="m-0 border-0 border-t border-line" />
                  <Interrupteur label={t("habillage.barOn")} aide={t("habillage.barHelp")}
                    valeur={o.bar_on} onChange={(v) => maj({ bar_on: v })} />
                  <div className="flex items-center gap-2.5 text-[0.85rem] text-muted">
                    <span className="size-5 rounded-full border border-line" style={{ background: etat.colors.green }} />
                    <span className="size-5 rounded-full border border-line" style={{ background: etat.colors.gold }} />
                    {t("habillage.colors")}
                  </div>
                </>
              )}

              {onglet === "fin" && (
                <>
                  <Interrupteur label={t("habillage.outroOn")} aide={t("habillage.outroHelp")}
                    valeur={o.outro_on} onChange={(v) => maj({ outro_on: v })} />
                  <Groupe actif={o.outro_on}>
                    <Field label={t("habillage.outroText")} aide={t("habillage.outroTextHelp")}>
                      <input className={inputClass} value={o.outro_text} maxLength={120} onChange={(e) => maj({ outro_text: e.target.value })} />
                    </Field>
                    <Field label={t("habillage.outroLink")} aide={t("habillage.outroLinkHelp")}>
                      <input className={inputClass} value={o.outro_link} maxLength={80} onChange={(e) => maj({ outro_link: e.target.value })} />
                    </Field>
                    <Nombre label={t("habillage.seconds")} valeur={o.outro_s} min={2} max={15} onChange={(v) => maj({ outro_s: v })} />
                  </Groupe>
                </>
              )}
            </fieldset>

            {message && (
              <p role="status" className={`m-0 rounded-[9px] px-3 py-2 text-[0.9rem] ${message.ko ? "bg-err-bg text-[#7a1f20]" : "bg-paper-2 text-deep"}`}>
                {message.texte}
              </p>
            )}

            {!lecture && (
              <div className="flex flex-wrap items-center gap-2.5 border-t border-line pt-4">
                <Btn variante="ghost" disabled={bloque} onClick={() => agir("apercu")}>
                  {occupe === "apercu" ? t("habillage.previewing") : t("habillage.previewDo")}
                </Btn>
                <Btn type="submit" disabled={bloque}>
                  {etat.video_ready ? t("habillage.rerender") : t("habillage.render")}
                </Btn>
                <span className="text-[0.82rem] text-muted">{t("habillage.renderHelp")}</span>
              </div>
            )}
          </form>
        </Card>

        {/* Right: what it will look like, then where the render stands. */}
        <div className="grid gap-4 min-[1000px]:sticky min-[1000px]:top-4">
          <Maquette o={o} etat={etat} onglet={onglet} />

          <Card className="grid gap-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="m-0 text-[1.05rem] font-semibold text-deeper">{t("habillage.preview")}</h2>
              <span className="text-[0.8rem] text-muted">{t("habillage.previewHelp")}</span>
            </div>
            {etat.preview_ready ? (
              <img src={`/api/v1/sermons/${kid}/habillage/apercu.jpg?v=${etat.preview_v}`} alt={t("habillage.preview")}
                className={`block w-full rounded-[8px] bg-night ${occupe === "apercu" ? "opacity-50" : ""}`} />
            ) : (
              <p className="m-0 rounded-[8px] border border-dashed border-line px-4 py-8 text-center text-[0.9rem] text-muted">
                {occupe === "apercu" ? t("habillage.previewing") : t("habillage.previewEmpty")}
              </p>
            )}
          </Card>

          {etat.status !== "" && <Statut etat={etat} kid={kid} />}
          {etat.has_source && <SousTitres etat={etat} kid={kid} onChange={rafraichir} />}
        </div>
      </div>
    </div>
  );
}

/** Live mock-up: a 16:9 frame in the mosque's colours, redrawn at each keystroke. It shows the
 *  element of the open tab (opening card, end screen) or the video with its overlays. */
function Maquette({ o, etat, onglet }: { o: HabillageOptions; etat: Etat; onglet: Onglet }) {
  const { green, gold } = etat.colors;
  const carton = (onglet === "ouverture" && o.intro_on) ? "intro" : (onglet === "fin" && o.outro_on) ? "fin" : "";
  const logoTaille = { s: "9%", m: "11.5%", l: "14.5%" }[o.logo_size];
  const pos = o.logo_pos;
  const logo = o.logo_on && etat.has_logo;
  const bandeauHaut = logo && pos === "bl";
  return (
    <div className="relative aspect-video overflow-hidden rounded-[10px] shadow-[0_8px_30px_rgba(11,26,20,0.18)]" aria-hidden>
      {carton ? (
        <div className="absolute inset-0 grid place-content-center justify-items-center gap-[3%] px-[10%] text-center"
          style={{ background: `radial-gradient(120% 90% at 50% 40%, ${green}, color-mix(in srgb, ${green} 55%, black))` }}>
          {etat.has_logo && <Disque couleur={gold} className="w-[13%] min-w-8" />}
          {carton === "intro" && (
            <span className="text-[clamp(0.5rem,1.3vw,0.72rem)] font-semibold tracking-[0.22em] uppercase" style={{ color: gold }}>
              {etat.mosque}
            </span>
          )}
          <span className="font-serif text-[clamp(1rem,3.2vw,1.9rem)] leading-tight text-white">
            {carton === "intro" ? (o.title || "—") : etat.mosque}
          </span>
          <span className="h-px w-[18%]" style={{ background: gold }} />
          <span className="text-[clamp(0.6rem,1.6vw,0.85rem)] text-white/80">
            {carton === "intro" ? o.subtitle : o.outro_text}
          </span>
          {carton === "fin" && o.outro_link && (
            <span className="text-[clamp(0.6rem,1.6vw,0.85rem)] font-semibold" style={{ color: gold }}>{o.outro_link}</span>
          )}
        </div>
      ) : (
        <div className="absolute inset-0 bg-[linear-gradient(135deg,#3b4a43,#1b2620_60%,#26322c)]">
          {/* a silhouette at the minbar, so the overlays read as overlays */}
          <div className="absolute bottom-0 left-1/2 h-[62%] w-[26%] -translate-x-1/2 rounded-t-[45%] bg-black/25" />
          <div className="absolute bottom-[52%] left-1/2 size-[13%] -translate-x-1/2 rounded-full bg-black/25" />
          {logo && (
            <Disque couleur={gold} className={`absolute ${pos[0] === "t" ? "top-[5%]" : "bottom-[7%]"} ${pos[1] === "l" ? "left-[3.5%]" : "right-[3.5%]"}`}
              style={{ width: logoTaille, opacity: o.logo_opacity }} />
          )}
          {o.lower_on && (
            <div className={`absolute left-[4%] flex max-w-[60%] ${bandeauHaut ? "bottom-[30%]" : "bottom-[11%]"}`}>
              <span className="w-[3px]" style={{ background: gold }} />
              <span className="grid gap-0.5 px-[0.9em] py-[0.55em] text-[clamp(0.55rem,1.5vw,0.9rem)]"
                style={{ background: `color-mix(in srgb, ${green} 35%, #0b1a14)` }}>
                <span className="truncate font-bold text-white">{o.speaker || "—"}</span>
                {o.role && <span className="truncate text-[0.72em] font-semibold tracking-[0.12em] uppercase" style={{ color: gold }}>{o.role}</span>}
              </span>
            </div>
          )}
          {o.bar_on && <div className="absolute inset-x-0 bottom-0 h-[1.2%]" style={{ background: gold }} />}
        </div>
      )}
    </div>
  );
}

function Disque({ couleur, className = "", style }: { couleur: string; className?: string; style?: CSSProperties }) {
  return (
    <span className={`block aspect-square rounded-full border-[2px] bg-white/95 ${className}`} style={{ borderColor: couleur, ...style }}>
      <span className="m-[22%] block aspect-square rounded-full" style={{ background: `color-mix(in srgb, ${couleur} 70%, white)` }} />
    </span>
  );
}

function Statut({ etat, kid }: { etat: Etat; kid: number }) {
  const { t } = useTranslation();
  const pct = Math.round(etat.progress * 100);
  const statutPill = etat.status === "pret" ? "termine" : etat.status;
  return (
    <Card className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="m-0 text-[1.05rem] font-semibold text-deeper">
          {etat.status === "pret" ? t("habillage.ready") : t(`habillage.status.${etat.status}`)}
        </h2>
        <Pill statut={statutPill} label={etat.status === "en_cours" ? t("habillage.progress", { n: pct }) : t(`habillage.status.${etat.status}`)} />
      </div>
      {etat.status === "en_cours" && (
        <div className="h-2 overflow-hidden rounded-full bg-paper-2" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full rounded-full bg-leaf transition-[width] duration-700" style={{ width: `${Math.max(3, pct)}%` }} />
        </div>
      )}
      {etat.status === "echec" && <p className="m-0 text-[0.9rem] text-[#7a1f20]">{t("habillage.failed", { e: etat.error })}</p>}
      {etat.video_ready && (
        <div className="flex flex-wrap items-center gap-2.5">
          <a href={`/api/v1/sermons/${kid}/habillage/video.mp4?download=1`}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-[9px] border border-leaf bg-leaf px-4 py-2.5 font-semibold text-night no-underline hover:bg-leaf-2">
            ↓ {t("habillage.download")}
          </a>
          {etat.size_mb !== null && <span className="text-[0.85rem] text-muted tabular-nums">{t("habillage.size_mb", { n: etat.size_mb })}</span>}
        </div>
      )}
    </Card>
  );
}

/** Subtitle tracks: the .srt of the dressed video and the background translation that fills the
 *  language not spoken yet. A track is offered as soon as it has passages, with its count, so a
 *  partial track is never mistaken for a complete one. */
function SousTitres({ etat, kid, onChange }: { etat: Etat; kid: number; onChange: () => void }) {
  const { t } = useTranslation();
  const [envoi, setEnvoi] = useState(false);
  const [note, setNote] = useState("");
  const etats = etat.subtitles_state ?? {};
  // Une langue est partielle tant que tout le sermon n'y est pas traduit : c'est ce qui ouvre le
  // bouton de traduction, avant même que la vidéo habillée existe (la piste du lecteur marche déjà).
  const partielles = Object.entries(etats).filter(([, piste]) => !piste.complete).map(([code]) => code);
  const enCours = etat.translation.status === "en_cours";
  // Rien à dire quand tout est traduit et qu'aucune vidéo habillée n'existe encore : pas de carte vide.
  if (!etat.subtitles.length && !partielles.length && !enCours && etat.translation.status !== "echec") return null;
  const traduire = async () => {
    setEnvoi(true);
    setNote("");
    try {
      await api.traduireSousTitres(kid);
      onChange();
    } catch (e: any) {
      setNote(e?.message ?? t("common.error"));
    } finally {
      setEnvoi(false);
    }
  };
  return (
    <Card className="grid gap-3">

      <div className="grid gap-2">
        <p className="m-0 text-[0.95rem] font-semibold text-deeper">{t("habillage.subtitles.title")}</p>
        <div className="flex flex-wrap items-center gap-2">
          {etat.subtitles.map((code) => {
            const piste = etat.subtitles_state?.[code];
            return (
              <a key={code} href={`/api/v1/sermons/${kid}/habillage/sous-titres/${code}.srt`}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-[9px] border border-line bg-white px-3 py-2 text-[0.9rem] font-semibold text-deep no-underline hover:border-leaf hover:bg-paper-2">
                ↓ {t(`habillage.subtitles.langs.${code}`, { defaultValue: code.toUpperCase() })}
                <span className="text-[0.78rem] font-normal text-muted">.srt</span>
                {piste && !piste.complete && (
                  <span className="rounded-full bg-paper-2 px-2 py-0.5 text-[0.72rem] font-semibold text-muted tabular-nums">
                    {t("habillage.subtitles.partial", { done: piste.done, total: piste.total })}
                  </span>
                )}
              </a>
            );
          })}
        </div>
        {enCours && (
          <div className="grid gap-1.5" role="status">
            <p className="m-0 text-[0.85rem] text-muted">
              {t("habillage.subtitles.translating", { n: Math.round(etat.translation.progress * 100) })}
            </p>
            <div className="h-1.5 w-full max-w-[16rem] overflow-hidden rounded-full bg-paper-2">
              <div className="h-full rounded-full bg-leaf transition-[width] duration-700"
                style={{ width: `${Math.max(3, Math.round(etat.translation.progress * 100))}%` }} />
            </div>
          </div>
        )}
        {etat.translation.status === "echec" && etat.translation.error && (
          <p className="m-0 text-[0.85rem] text-[#7a1f20]">
            {t("habillage.subtitles.failed", { e: etat.translation.error })}
          </p>
        )}
        {partielles.length > 0 && !enCours && etat.can_edit && (
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={traduire} disabled={envoi}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-[9px] border border-leaf bg-white px-3 py-2 text-[0.9rem] font-semibold text-deep hover:bg-paper-2 disabled:opacity-60">
              {envoi ? t("habillage.subtitles.starting") : t("habillage.subtitles.translate")}
            </button>
            {note && <span className="text-[0.85rem] text-[#7a1f20]">{note}</span>}
          </div>
        )}
        <p className="m-0 text-[0.82rem] text-muted">{t("habillage.subtitles.help")}</p>
        {etat.subtitles.length > 0 && (
          <p className="m-0 text-[0.8rem] text-muted">
            {t("habillage.subtitles.original")}{" "}
            {etat.subtitles.map((code, rang) => (
              <span key={code}>
                {rang > 0 && " · "}
                <a href={`/api/v1/sermons/${kid}/habillage/sous-titres/${code}.srt?brut=1`}
                  className="underline decoration-leaf underline-offset-4 hover:text-deep">
                  {t(`habillage.subtitles.langs.${code}`, { defaultValue: code.toUpperCase() })}
                </a>
              </span>
            ))}
          </p>
        )}
      </div>
    </Card>
  );
}

function Interrupteur({ label, aide, valeur, onChange, eteint = false }:
  { label: string; aide: string; valeur: boolean; onChange: (v: boolean) => void; eteint?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="m-0 font-semibold text-deeper">{label}</p>
        <p className="mt-0.5 mb-0 text-[0.86rem] text-muted">{aide}</p>
      </div>
      <button type="button" role="switch" aria-checked={valeur} aria-label={label} disabled={eteint}
        onClick={() => onChange(!valeur)}
        className={`inline-flex min-h-9 shrink-0 cursor-pointer items-center gap-2 rounded-full border py-1 pr-3 pl-1 text-[0.85rem] font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${valeur ? "border-deep bg-deep text-white" : "border-line bg-white text-muted"}`}>
        <span className={`size-6 rounded-full transition-transform ${valeur ? "translate-x-0 bg-leaf" : "bg-paper-2"}`} />
        {valeur ? t("habillage.on") : t("habillage.off")}
      </button>
    </div>
  );
}

function Groupe({ actif, children }: { actif: boolean; children: ReactNode }) {
  return <div className={`grid gap-4 transition-opacity ${actif ? "" : "pointer-events-none opacity-40"}`}>{children}</div>;
}

function Nombre({ label, valeur, min, max, onChange }:
  { label: string; valeur: number; min: number; max: number; onChange: (v: number) => void }) {
  // The text typed is kept as is (an empty field, “1.”) and the number follows the server once saved.
  const [texte, setTexte] = useState(String(valeur));
  const [valeurVue, setValeurVue] = useState(valeur);
  if (valeurVue !== valeur) {
    setValeurVue(valeur);
    setTexte((x) => (Number(x) === valeur && x !== "" ? x : String(valeur)));
  }
  return (
    <Field label={label}>
      <input className={`${inputClass} max-w-[9rem] tabular-nums`} type="number" inputMode="decimal" min={min} max={max} step={0.5}
        value={texte} onChange={(e) => { setTexte(e.target.value); const n = Number(e.target.value); if (e.target.value !== "" && Number.isFinite(n)) onChange(n); }} />
    </Field>
  );
}

function Bandeau({ children, ko = false }: { children: ReactNode; ko?: boolean }) {
  return (
    <p className={`m-0 rounded-[10px] border px-4 py-3 text-[0.92rem] ${ko ? "border-live/30 bg-err-bg text-[#7a1f20]" : "border-line bg-warn-bg text-warn"}`}>
      {children}
    </p>
  );
}
