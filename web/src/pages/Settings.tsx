import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { liste } from "../format";
import { Btn, Card, Field, Tabs, Title, inputClass, toucher } from "../ui";
import { idOnglet, idPanneau } from "../onglets";
import { RecitersEditor } from "../RecitersEditor";
import PrayerSettings from "./PrayerSettings";

type Message = { ok: boolean; texte: string } | null;

/** A live destination as the screen edits it: the key comes back masked, and stays masked. */
type Entree = { mode: "camera" | "push"; url: string; push_rtmp: string; has_key?: boolean;
                modes?: ("camera" | "push")[] };
type Cible = { id?: number; label: string; url: string; enabled: boolean };
/** Texts of the slates as the screen edits them: {écran: {champ: texte}}; empty = default. */
type Ecrans = Record<string, Record<string, string>>;
/** Imported reference file: presence, same-origin address, size; no machine path. */
type Gabarit = { present: boolean; url: string; name?: string; size?: number; imported_at?: string };
/** What the import took, ignored and left empty — shown as one short sentence. */
type RapportImport = { pris: number; ignores: number; vides: number; liste: string };

const ECRANS = ["avant", "priere", "entracte", "termine", "janaza", "dars_fin"] as const;

// Le bandeau du direct (khutba, dars) n'a pas de champs : ses textes viennent des réglages et de
// la séance en cours. On l'affiche dans la même liste, mais son panneau montre seulement une note.
const BANDEAUX = ["khutba", "dars"] as const;
const ONGLETS_ECRANS = [...ECRANS, ...BANDEAUX] as const;
const EST_BANDEAU = (c: string): boolean => (BANDEAUX as readonly string[]).includes(c);

// Deux par ligne ; le pied de page occupe la largeur, les autres tiennent à deux.
const CHAMPS_ECRAN = [
  { champ: "eyebrow", large: false },
  { champ: "title", large: true },
  { champ: "sub", large: false },
  { champ: "foot", large: true },
] as const;

// The Mawaqit address moved to the Prayer times tab (which saves it with the source).
const CHAMPS_MOSQUEE = ["name", "timezone"] as const;
const CHAMPS_TARAWIH = [
  "tw_mode", "tw_start", "tw_end", "tw_offset", "tw_max", "tw_confirm",
  "tw_after_isha", "tw_isha", "tw_silence", "tw_reciters", "tw_nights",
] as const;
const MODES_TARAWIH = ["off", "audio", "video"] as const;
const SOUS_TARAWIH = ["broadcast", "times", "reciters"] as const;

/** Form values from the server response (all as text, as the API expects). */
function depuisServeur(d: any): Record<string, string> {
  const f: Record<string, string> = {};
  for (const k of CHAMPS_MOSQUEE) f[k] = d.mosque[k] ?? "";
  f.clips = String(d.processing?.clips ?? 3);
  f.clips_enabled = d.processing?.enabled === false ? "0" : "1";
  const tw = d.tarawih ?? {};
  f.tw_mode = tw.mode ?? "audio";
  f.tw_start = tw.start ?? "";
  f.tw_end = tw.end ?? "";
  f.tw_offset = String(tw.offset_min ?? 20);
  f.tw_max = String(tw.max_min ?? 180);
  f.tw_after_isha = tw.after_isha ? "1" : "0";
  f.tw_isha = String(tw.isha_min ?? 10);
  f.tw_silence = String(tw.silence_end_min ?? 6);
  f.tw_reciters = JSON.stringify(tw.reciters ?? []);
  f.tw_nights = JSON.stringify(tw.nights ?? {});
  f.tw_confirm = tw.mode === "video" ? "1" : "0";
  return f;
}

/** Settings of one mosque: the same tabs for every role. What belongs to the installation
 *  (replays bucket, folders, public address, model, transcription, orchestrator) lives on the
 *  operator's platform page, never here. */
export default function Settings() {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState("");
  const [onglet, setOnglet] = useState("mosque");
  const [sousOnglet, setSousOnglet] = useState("connexion");
  const [sousTarawih, setSousTarawih] = useState<string>("broadcast");
  const [enCours, setEnCours] = useState(false);
  const [msg, setMsg] = useState<Message>(null);
  const [cibles, setCibles] = useState<Cible[]>([]);
  const [ciblesInitiales, setCiblesInitiales] = useState("");
  const [entree, setEntree] = useState<Entree>({ mode: "camera", url: "", push_rtmp: "" });
  const [entreeInitiale, setEntreeInitiale] = useState("");
  const [essai, setEssai] = useState<{ ok: boolean; detail: string } | null>(null);
  const [ecrans, setEcrans] = useState<Ecrans>({});
  const [ecransInitial, setEcransInitial] = useState("");
  const [ecranVue, setEcranVue] = useState<string>("avant");
  const [apercu, setApercu] = useState("");
  const [gabarit, setGabarit] = useState<Gabarit>({ present: false, url: "" });
  const [rapport, setRapport] = useState<RapportImport | null>(null);
  const [importe, setImporte] = useState(false);
  const [guide, setGuide] = useState({ agent: "", humain: "", docs: "", agentEn: "", humainEn: "", docsEn: "" });
  const [cle, setCle] = useState<{ obs_server: string; obs_key: string; obs_srt?: string } | null>(null);
  const fichier = useRef<HTMLInputElement>(null);
  const gabaritFichier = useRef<HTMLInputElement>(null);

  const charger = () =>
    api.settings().then((r) => {
      const v = depuisServeur(r);
      const c: Cible[] = liste<any>(r.targets).map((t: any) => ({ id: t.id, label: t.label, url: t.url, enabled: !!t.enabled }));
      const e: Entree = { has_key: !!r.input?.has_key, mode: r.input?.mode === "push" ? "push" : "camera", url: r.input?.url ?? "",
                          push_rtmp: r.input?.push_rtmp ?? "", modes: r.input?.modes };
      setD(r); setF(v); setInitial(JSON.stringify(v)); setCibles(c); setCiblesInitiales(JSON.stringify(c));
      setGuide({ agent: typeof r.guide?.url === "string" ? r.guide.url : `${window.location.origin}/docs/agent-gabarit-ecrans.md`,
                 humain: typeof r.guide?.human === "string" ? r.guide.human : `${window.location.origin}/docs/ecrans-mode-emploi`,
                 docs: typeof r.guide?.docs === "string" ? r.guide.docs : `${window.location.origin}/docs`,
                 agentEn: typeof r.guide?.url_en === "string" ? r.guide.url_en : `${window.location.origin}/docs/en/screen-template-prompt.md`,
                 humainEn: typeof r.guide?.human_en === "string" ? r.guide.human_en : `${window.location.origin}/docs/en/screens`,
                 docsEn: typeof r.guide?.docs_en === "string" ? r.guide.docs_en : `${window.location.origin}/docs?langue=en` });
      setEntree(e); setEntreeInitiale(JSON.stringify(e));
      const ec: Ecrans = {};
      for (const [nom, champs] of Object.entries<any>(r.screens ?? {})) {
        ec[nom] = {};
        for (const [champ, v] of Object.entries<any>(champs)) ec[nom][champ] = v?.value ?? "";
      }
      setEcrans(ec); setEcransInitial(JSON.stringify(ec));
      setGabarit(r.gabarit && typeof r.gabarit.present === "boolean" ? r.gabarit : { present: false, url: "" });
    }).catch(() => setD(null));
  useEffect(() => { charger(); }, []);

  /** Aperçu du moteur, redessiné peu après la dernière frappe : il montre le texte tapé, pas seulement l'enregistré. */
  useEffect(() => {
    if (!d) return;
    let annule = false;
    const minuteur = setTimeout(() => {
      api.apercuEcran(ecranVue, ecrans)
        .then((url) => {
          if (annule) { URL.revokeObjectURL(url); return; }
          setApercu((avant) => { if (avant) URL.revokeObjectURL(avant); return url; });
        })
        .catch(() => { /* champ refusé : l'aperçu précédent reste affiché */ });
    }, 400);
    return () => { annule = true; clearTimeout(minuteur); };
  }, [ecrans, ecranVue, d]);

  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  // Les documents existent en deux langues : on ouvre celle de l'interface, pas celle du code.
  const en = i18n.language === "en";
  const lienHumain = en && guide.humainEn ? guide.humainEn : guide.humain;
  const lienAgent = en && guide.agentEn ? guide.agentEn : guide.agent;
  const entreeModifiee = JSON.stringify(entree) !== entreeInitiale;
  const ecransModifies = JSON.stringify(ecrans) !== ecransInitial;
  const modifie = JSON.stringify(f) !== initial || JSON.stringify(cibles) !== ciblesInitiales
    || entreeModifiee || ecransModifies;
  const majCible = (i: number, champ: keyof Cible, valeur: string | boolean) => {
    setCibles((x) => x.map((c, j) => (j === i ? { ...c, [champ]: valeur } : c)));
    setMsg(null);
  };
  const maj = (cle: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setF((x) => ({ ...x, [cle]: e.target.value }));
    setMsg(null);
  };

  /** A single save for the whole screen: each part is sent only if it changed. */
  const enregistrer = async (e: React.FormEvent) => {
    e.preventDefault();
    // The video confirmation sits on the Broadcast sub-tab: saving from another one shows it again.
    if (f.tw_mode === "video" && f.tw_confirm !== "1") {
      setOnglet("tarawih"); setSousTarawih("broadcast");
      setMsg({ ok: false, texte: t("settings.tarawihVideoConfirmMissing") });
      return;
    }
    setEnCours(true); setMsg(null);
    try {
      const avant = JSON.parse(initial);
      const mosquee: Record<string, string> = {};
      for (const k of CHAMPS_MOSQUEE) mosquee[k] = f[k];
      await api.reglagesMosquee(mosquee);
      if (f.clips !== avant.clips || f.clips_enabled !== avant.clips_enabled) {
        await api.traitement(f.clips, f.clips_enabled === "1");
      }
      if (CHAMPS_TARAWIH.some((k) => f[k] !== avant[k])) {
        await api.tarawih({ mode: f.tw_mode, start: f.tw_start, end: f.tw_end, offset_min: f.tw_offset,
          max_min: f.tw_max, confirm_video: f.tw_confirm, after_isha: f.tw_after_isha === "1",
          isha_min: f.tw_isha, silence_end_min: f.tw_silence,
          reciters: JSON.parse(f.tw_reciters || "[]"), nights: JSON.parse(f.tw_nights || "{}") });
      }
      if (JSON.stringify(cibles) !== ciblesInitiales) await api.destinations(cibles);
      if (entreeModifiee) await api.saveInput(entree.mode, entree.url);
      if (ecransModifies) await api.ecrans(ecrans);
      await charger();
      setMsg({ ok: true, texte: t("settings.saved") });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    } finally {
      setEnCours(false);
    }
  };

  const nouvelleCle = async () => {
    if (entree.has_key && !window.confirm(t("settings.keyConfirm"))) return;
    try {
      const r = await api.newStreamKey();
      setCle({ obs_server: r.obs_server, obs_key: r.obs_key, obs_srt: r.obs_srt });
      setEntree((e) => ({ ...e, has_key: true }));
    } catch (err: any) {
      setEssai({ ok: false, detail: err.message });
    }
  };
  const testerEntree = async () => {
    setEssai(null);
    try {
      const r = await api.testInput(entree.mode, entree.url);
      setEssai({ ok: r.ok, detail: r.detail });
    } catch (err: any) {
      setEssai({ ok: false, detail: err.message });
    }
  };

  const envoyerLogo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fic = e.target.files?.[0];
    e.target.value = "";
    if (!fic) return;
    try {
      await api.envoyerLogo(fic);
      await api.settings().then((r) => setD((x: any) => ({ ...x, mosque: { ...x.mosque, logo: r.mosque.logo, has_logo: r.mosque.has_logo } })));
      setMsg({ ok: true, texte: t("settings.logoSent") });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    }
  };
  const retirerLogo = async () => {
    try {
      await api.retirerLogo();
      await api.settings().then((r) => setD((x: any) => ({ ...x, mosque: { ...x.mosque, logo: r.mosque.logo, has_logo: r.mosque.has_logo } })));
      setMsg({ ok: true, texte: t("settings.logoRemoved") });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    }
  };

  /** Import d'un gabarit HTML : le serveur vérifie, conserve la référence et applique les textes.
   *  On réaffiche ensuite l'état enregistré, pas une copie locale : vides et champs absents restent. */
  const importerGabarit = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fic = e.target.files?.[0];
    e.target.value = "";
    if (!fic) return;
    setImporte(true); setMsg(null); setRapport(null);
    try {
      const r = await api.importerGabarit(fic);
      const ec: Ecrans = {};
      for (const [nom, champs] of Object.entries<any>(r.screens ?? {})) {
        ec[nom] = {};
        for (const [champ, v] of Object.entries<any>(champs)) ec[nom][champ] = v?.value ?? "";
      }
      setEcrans(ec); setEcransInitial(JSON.stringify(ec));
      setD((x: any) => ({ ...x, screens: r.screens ?? x.screens }));
      setGabarit(r.gabarit ?? { present: false, url: "" });
      const pris = Object.values<any>(r.taken ?? {}).reduce((n: number, s: any) => n + Object.keys(s).length, 0);
      const ignores: any[] = r.ignored ?? [];
      setRapport({
        pris, ignores: ignores.length, vides: (r.empty ?? []).length,
        liste: ignores.slice(0, 4).map((i) => [i.screen, i.slot].filter(Boolean).join(".")).join(", ")
               + (ignores.length > 4 ? "…" : ""),
      });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    } finally {
      setImporte(false);
    }
  };

  const fuseaux: string[] = typeof (Intl as any).supportedValuesOf === "function" ? (Intl as any).supportedValuesOf("timeZone") : [];
  const contenu: Record<string, React.ReactNode> = {
    mosque: (
      <div className="grid gap-3.5">
        {/* Two fields per line on a wide screen: the tab fits without scrolling. */}
        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field label={t("settings.name")}><input className={inputClass} value={f.name} onChange={maj("name")} required /></Field>
          <Field label={t("settings.timezone")} aide={t("settings.timezoneHelp")}>
            <input className={inputClass} value={f.timezone} onChange={maj("timezone")} list="fuseaux" placeholder="Europe/Paris" />
            <datalist id="fuseaux">{fuseaux.map((z) => <option key={z} value={z} />)}</datalist>
          </Field>
        </div>
      </div>
    ),
    // Its own editor (a source, a calculation, a month grid): saved by its own buttons, never by the form's.
    prayer: (
      <div onKeyDown={(e) => { if (e.key === "Enter" && (e.target as HTMLElement).tagName === "INPUT") e.preventDefault(); }}>
        <PrayerSettings d={d.prayer} onSaved={charger} />
      </div>
    ),
    logo: (
      <div className="grid max-w-[520px] gap-3.5">
        <Title>{t("settings.logoTitle")}</Title>
        <p className="m-0 text-[0.9rem] text-muted">{t("settings.logoHint")}</p>
        <img src={d.mosque.logo} alt={t("settings.logoAlt")} className="size-24 rounded-lg border border-line bg-paper-2 object-contain p-2" />
        {!d.mosque.has_logo && <p className="m-0 text-[0.85rem] text-muted">{t("settings.logoNone")}</p>}
        <div className="flex flex-wrap gap-2">
          <input ref={fichier} type="file" accept=".png,.jpg,.jpeg,.webp,.svg,image/*" className="hidden" onChange={envoyerLogo} />
          <Btn type="button" variante="ghost" onClick={() => fichier.current?.click()}>{t("settings.logoChoose")}</Btn>
          <Btn type="button" variante="dangerGhost" disabled={!d.mosque.has_logo} onClick={retirerLogo}>{t("settings.logoRemove")}</Btn>
        </div>
      </div>
    ),
    live: (
      <div className="grid max-w-[720px] gap-3.5">
        {/* Two sub-tabs: the connection to the live program, then where the stream goes. */}
        <Tabs items={[{ cle: "connexion", label: t("settings.liveConnection") },
                       { cle: "destinations", label: t("settings.destTitle") }]}
          base="reglages-live" label={t("settings.tabs.live")}
          valeur={sousOnglet} onChange={(c) => { setSousOnglet(c); setMsg(null); }} />
        {sousOnglet === "connexion" && <div className="grid max-w-[520px] gap-3.5"
          role="tabpanel" id={idPanneau("reglages-live", "connexion")} aria-labelledby={idOnglet("reglages-live", "connexion")}>
        {/* Video input: the same choice as in "Mise en route", editable here afterwards. */}
        <div className="border-t border-line pt-3.5">
          <p className="m-0 mb-2 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-green">{t("settings.inputKicker")}</p>
          {/* A hosted platform offers the push only: no tabs when there is nothing to choose. */}
          {(entree.modes ?? ["camera", "push"]).length > 1 && <Tabs items={[{ cle: "camera", label: t("onboarding.cameraTitle") },
                        { cle: "push", label: t("onboarding.pushTitle") }]}
            base="reglages-entree" label={t("settings.inputKicker")}
            valeur={entree.mode}
            onChange={(m) => { setEntree((e) => ({ ...e, mode: m === "push" ? "push" : "camera" })); setEssai(null); setMsg(null); }} />}
          <div className="mt-3 grid gap-1.5"
            role="tabpanel" id={idPanneau("reglages-entree", entree.mode)} aria-labelledby={idOnglet("reglages-entree", entree.mode)}>
            {entree.mode === "camera" ? (
              <Field label={t("onboarding.cameraUrl")} aide={t("onboarding.cameraUrlHelp")}>
                <div className="flex gap-2">
                  <input className={`${inputClass} font-mono text-[0.85rem]`} value={entree.url} spellCheck={false}
                    placeholder="rtsp://…" onChange={(e) => { setEntree((x) => ({ ...x, url: e.target.value })); setEssai(null); setMsg(null); }} />
                  <Btn variante="ghost" onClick={testerEntree}>{t("onboarding.test")}</Btn>
                </div>
              </Field>
            ) : (
              <Field label={t("onboarding.pushServer")} aide={t("onboarding.pushNote")}>
                <div className="flex gap-2">
                  <input className={`${inputClass} font-mono text-[0.85rem]`} value={entree.push_rtmp} readOnly />
                  <Btn variante="ghost" onClick={testerEntree}>{t("onboarding.test")}</Btn>
                </div>
              </Field>
            )}
            {entree.mode === "push" && (
              <Field label={t("settings.keyLabel")} aide={cle ? t("settings.keyOnce") : entree.has_key ? t("settings.keyHidden") : t("settings.keyNone")}>
                {cle ? (
                  <div className="grid gap-1.5">
                    <input className={`${inputClass} font-mono text-[0.85rem]`} value={cle.obs_server} readOnly aria-label={t("onboarding.pushServer")} />
                    <input className={`${inputClass} font-mono text-[0.85rem]`} value={cle.obs_key} readOnly aria-label={t("settings.keyLabel")}
                      onFocus={(e) => e.target.select()} />
                    {cle.obs_srt && (
                      <input className={`${inputClass} font-mono text-[0.85rem]`} value={cle.obs_srt} readOnly aria-label="SRT"
                        onFocus={(e) => e.target.select()} />
                    )}
                  </div>
                ) : (
                  <div><Btn variante="ghost" onClick={nouvelleCle}>{entree.has_key ? t("settings.keyReset") : t("settings.keyCreate")}</Btn></div>
                )}
              </Field>
            )}
            {essai && <span className={`text-[0.86rem] ${essai.ok ? "text-green" : "text-muted"}`}>{essai.detail}</span>}
          </div>
        </div>

        </div>}
        {sousOnglet === "destinations" && <div>
          <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.destHint")}</p>
          {cibles.length === 0 ? (
            <p className="m-0 mb-3 text-[0.9rem] text-muted">{t("settings.destNone")}</p>
          ) : (
            <ul className="m-0 mb-3 grid list-none gap-2 p-0">
              {cibles.map((c, i) => (
                /* One line per destination: name, address, active, remove. Nothing stacked. */
                <li key={c.id ?? `n${i}`}
                  className="grid gap-2 rounded-lg border border-line bg-white p-2 sm:grid-cols-[minmax(6.5rem,9rem)_1fr_auto_auto] sm:items-center">
                  <input className={`${inputClass} min-h-9 text-[0.9rem]`} value={c.label}
                    aria-label={t("settings.destLabel")} placeholder={t("settings.destLabelPlaceholder")}
                    maxLength={60} onChange={(e) => majCible(i, "label", e.target.value)} />
                  <input className={`${inputClass} min-h-9 font-mono text-[0.8rem]`} value={c.url}
                    aria-label={t("settings.destUrl")} placeholder={t("settings.destExample")}
                    spellCheck={false} autoComplete="off" onChange={(e) => majCible(i, "url", e.target.value)} />
                  <label className="flex items-center gap-1.5 whitespace-nowrap text-[0.88rem] font-semibold">
                    <input type="checkbox" checked={c.enabled} onChange={(e) => majCible(i, "enabled", e.target.checked)} />
                    {t("settings.destEnabled")}
                  </label>
                  <Btn type="button" variante="dangerGhost" className="min-h-9 px-3 py-1.5 text-[0.88rem]"
                    onClick={() => { setCibles((x) => x.filter((_, j) => j !== i)); setMsg(null); }}>
                    {t("settings.destRemove")}
                  </Btn>
                </li>
              ))}
            </ul>
          )}
          <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]"
            disabled={cibles.length >= 6}
            onClick={() => { setCibles((x) => [...x, { label: "", url: "", enabled: true }]); setMsg(null); }}>
            {t("settings.destAdd")}
          </Btn>
        </div>}
      </div>
    ),
    processing: (
      <div className="grid gap-3">
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.processingHint")}</p>
        <label className="flex w-fit items-center gap-1.5 text-[0.9rem] font-semibold">
          <input type="checkbox" checked={f.clips_enabled === "1"}
            onChange={(e) => { setF((x) => ({ ...x, clips_enabled: e.target.checked ? "1" : "0" })); setMsg(null); }} />
          {t("settings.clipsEnabled")}
        </label>
        <Field label={t("settings.clipsCount")}
          aide={t("settings.clipsCountHelp", { min: d.processing?.min ?? 1, max: d.processing?.max ?? 10 })}>
          <input className={`${inputClass} max-w-[9rem]`} type="number" inputMode="numeric"
            min={d.processing?.min ?? 1} max={d.processing?.max ?? 10} disabled={f.clips_enabled !== "1"}
            value={f.clips} onChange={maj("clips")} />
        </Field>
        <p className="m-0 text-[0.85rem] text-muted">
          {t(f.clips_enabled === "1" ? "settings.clipsNow" : "settings.clipsOff", {
            n: d.processing?.effective ?? d.processing?.clips ?? 3 })}
        </p>
      </div>
    ),
    tarawih: (
      <div className="grid gap-3">
        {/* Three sub-tabs, one subject each: what is broadcast and when, the timing, the reciters. */}
        <Tabs items={SOUS_TARAWIH.map((c) => ({ cle: c, label: t(`settings.tarawihTabs.${c}`) }))}
          base="reglages-tarawih" label={t("settings.tabs.tarawih")}
          valeur={sousTarawih} onChange={(c) => { setSousTarawih(c); setMsg(null); }} />
        <div className="grid min-w-0 gap-3"
          role="tabpanel" id={idPanneau("reglages-tarawih", sousTarawih)} aria-labelledby={idOnglet("reglages-tarawih", sousTarawih)}>
          {sousTarawih === "broadcast" && <>
            <p className="m-0 max-w-[90ch] text-[0.9rem] text-muted">{t("settings.tarawihHint")}</p>
            <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0 sm:grid-cols-3">
              <legend className="mb-1.5 p-0 text-[0.9rem] font-semibold">{t("settings.tarawihMode")}</legend>
              {MODES_TARAWIH.map((m) => (
                <label key={m} className={`flex items-start gap-2 rounded-md border p-2.5 text-[0.9rem] ${f.tw_mode === m ? "border-deep bg-white" : "border-line"}`}>
                  <input type="radio" name="tw_mode" value={m} checked={f.tw_mode === m} className="mt-1"
                    onChange={() => { setF((x) => ({ ...x, tw_mode: m, tw_confirm: "0" })); setMsg(null); }} />
                  <span><strong>{t(`settings.tarawihModes.${m}`)}</strong>
                    <span className="block text-[0.82rem] leading-snug text-muted">{t(`settings.tarawihModesHelp.${m}`)}</span></span>
                </label>
              ))}
            </fieldset>
            {f.tw_mode === "video" && (
              <div role="alert" className="grid gap-1.5 rounded-md border border-[#c99a2e] bg-[#fbf3df] p-2.5 text-[0.9rem] text-[#5a3d00]">
                <p className="m-0 font-semibold">{t("settings.tarawihVideoWarning")}</p>
                <label className="flex items-start gap-2">
                  <input type="checkbox" required checked={f.tw_confirm === "1"} className="mt-1"
                    onChange={(e) => { setF((x) => ({ ...x, tw_confirm: e.target.checked ? "1" : "0" })); setMsg(null); }} />
                  {t("settings.tarawihVideoConfirm")}
                </label>
              </div>
            )}
            <div className="grid gap-x-3 gap-y-2 sm:grid-cols-2">
              <Field label={t("settings.tarawihStart")}>
                <input className={inputClass} type="date" value={f.tw_start} onChange={maj("tw_start")} />
              </Field>
              <Field label={t("settings.tarawihEnd")}>
                <input className={inputClass} type="date" value={f.tw_end} min={f.tw_start || undefined} onChange={maj("tw_end")} />
              </Field>
            </div>
          </>}
          {sousTarawih === "times" && <>
            <p className="m-0 max-w-[90ch] text-[0.9rem] text-muted">{t("settings.tarawihTimesHint")}</p>
            <label className="flex items-start gap-2 text-[0.9rem]">
              <input type="checkbox" className="mt-1" checked={f.tw_after_isha === "1"}
                onChange={(e) => { setF((x) => ({ ...x, tw_after_isha: e.target.checked ? "1" : "0" })); setMsg(null); }} />
              <span><strong>{t("settings.tarawihAfterIsha")}</strong>
                <span className="block text-[0.82rem] leading-snug text-muted">{t("settings.tarawihAfterIshaHelp")}</span></span>
            </label>
            <div className="grid gap-x-3 gap-y-2 sm:grid-cols-2">
              <Field label={t("settings.tarawihIsha")}>
                <input className={inputClass} type="number" inputMode="numeric"
                  min={d.tarawih?.bounds?.isha_min?.[0] ?? 5} max={d.tarawih?.bounds?.isha_min?.[1] ?? 30}
                  value={f.tw_isha} onChange={maj("tw_isha")} />
              </Field>
              <Field label={t("settings.tarawihOffset")}>
                <input className={inputClass} type="number" inputMode="numeric"
                  min={d.tarawih?.bounds?.offset_min?.[0] ?? 5} max={d.tarawih?.bounds?.offset_min?.[1] ?? 120}
                  value={f.tw_offset} onChange={maj("tw_offset")} />
              </Field>
              <Field label={t("settings.tarawihSilence")}>
                <input className={inputClass} type="number" inputMode="numeric"
                  min={d.tarawih?.bounds?.silence_end_min?.[0] ?? 3} max={d.tarawih?.bounds?.silence_end_min?.[1] ?? 30}
                  value={f.tw_silence} onChange={maj("tw_silence")} />
              </Field>
              <Field label={t("settings.tarawihMax")}>
                <input className={inputClass} type="number" inputMode="numeric"
                  min={d.tarawih?.bounds?.max_min?.[0] ?? 30} max={d.tarawih?.bounds?.max_min?.[1] ?? 300}
                  value={f.tw_max} onChange={maj("tw_max")} />
              </Field>
            </div>
          </>}
          {sousTarawih === "reciters" && (
            <RecitersEditor
              roster={JSON.parse(f.tw_reciters || "[]")}
              nights={JSON.parse(f.tw_nights || "{}")}
              max={d.tarawih?.bounds?.reciters ?? 8}
              start={f.tw_start}
              end={f.tw_end}
              onChange={(r, n) => { setF((x) => ({ ...x, tw_reciters: JSON.stringify(r), tw_nights: JSON.stringify(n) })); setMsg(null); }}
            />
          )}
        </div>
      </div>
    ),
    screens: (
      <div className="grid gap-3">
        {/* Un seul formulaire, deux colonnes : champs à gauche, aperçu collé à droite. */}
        <div className="grid items-start gap-x-5 gap-y-3 sm:grid-cols-[minmax(0,1fr)_250px]">
          <div className="grid min-w-0 gap-3">
            <div className="grid gap-1.5">
              <Tabs items={ONGLETS_ECRANS.map((c) => ({ cle: c, label: t(`settings.screenNames.${c}`) }))}
                base="reglages-ecrans" label={t("settings.tabs.screens")}
                valeur={ecranVue} onChange={(c) => { setEcranVue(c); setMsg(null); }} />
              {/* Pas de panneau déplié : le mode d'emploi s'ouvre dans un onglet, la page ne bouge pas. */}
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <div className="flex max-w-[68ch] flex-wrap items-baseline gap-x-2 gap-y-1 text-[0.84rem] leading-[1.5] text-muted">
                  <span>{t("settings.screensHint")}</span>
                  <a href={lienHumain} target="_blank" rel="noreferrer"
                    className={`${toucher} font-semibold text-deep underline decoration-line decoration-1 underline-offset-4`}>
                    {t("settings.screensHelpOpen")}
                  </a>
                </div>
                {/* Discret, à côté du mode d'emploi : le fichier rendu par l'assistant s'importe ici. */}
                <button type="button" disabled={importe}
                  className={`${toucher} shrink-0 border-0 bg-transparent p-0 text-[0.78rem] font-semibold leading-[1.5] text-deep underline decoration-line decoration-1 underline-offset-4 hover:text-green disabled:cursor-not-allowed disabled:text-muted disabled:no-underline`}
                  onClick={() => gabaritFichier.current?.click()}>
                  {importe ? t("settings.screensImporting") : t("settings.screensImport")}
                </button>
                <input ref={gabaritFichier} type="file" accept=".html,text/html" className="hidden" onChange={importerGabarit} />
              </div>
              {/* Le guide pour un assistant est un fichier servi par cette instance : on donne son adresse. */}
              <div className="flex max-w-[68ch] flex-wrap items-center gap-x-2 gap-y-1 rounded-[8px] border border-line bg-paper px-3 py-2">
                <p className="m-0 text-[0.82rem] text-muted">{t("settings.screensGuide")}</p>
                {lienAgent && (
                  <a href={lienAgent} target="_blank" rel="noreferrer"
                    className={`${toucher} break-all font-mono text-[0.78rem] text-deep underline decoration-line decoration-1 underline-offset-4`}>
                    {lienAgent}
                  </a>
                )}
                <button type="button"
                  className={`${toucher} ml-auto shrink-0 rounded-[6px] border border-line bg-white px-2 py-1 text-[0.78rem] font-semibold text-deep hover:bg-paper-2`}
                  onClick={() => { navigator.clipboard.writeText(lienAgent).then(() => setMsg({ ok: true, texte: t("settings.screensGuideCopied") })).catch(() => setMsg({ ok: false, texte: t("settings.screensGuideCopyFail") })); }}>
                  {t("settings.screensGuideCopy")}
                </button>
              </div>
            </div>
            <div className="grid gap-3"
              role="tabpanel" id={idPanneau("reglages-ecrans", ecranVue)} aria-labelledby={idOnglet("reglages-ecrans", ecranVue)}>
              {EST_BANDEAU(ecranVue) ? (
                <p className="m-0 max-w-[58ch] rounded-[8px] border border-line bg-paper px-3 py-2 text-[0.84rem] leading-[1.5] text-muted">
                  {t("settings.screensBannerHint")}
                </p>
              ) : (
                CHAMPS_ECRAN.map(({ champ, large }) => (
                  <Field key={champ} label={t(`settings.screenFields.${champ}`)}
                    className={large ? undefined : "max-w-[22rem]"}>
                    <input className={inputClass}
                      placeholder={d.screens[ecranVue]?.[champ]?.default ?? ""}
                      value={ecrans[ecranVue]?.[champ] ?? ""}
                      onChange={(e) => {
                        setEcrans((x) => ({ ...x, [ecranVue]: { ...(x[ecranVue] ?? {}), [champ]: e.target.value } }));
                        setMsg(null);
                      }} />
                  </Field>
                ))
              )}
            </div>
            {!EST_BANDEAU(ecranVue) && (
              <p className="m-0 max-w-[62ch] text-[0.8rem] leading-[1.5] text-muted">{t("settings.screensVars")}</p>
            )}
          </div>
          <figure className="m-0 grid gap-1.5 sm:sticky sm:top-3">
            {/* Un seul aperçu : le fichier importé, ou à défaut le dessin du moteur. Deux cadres
                montreraient deux images pour un même écran, donc une question sans réponse. */}
            {gabarit.present ? (
              <iframe src={`${gabarit.url}&ecran=${ecranVue}`} title={t("settings.screensImportPreview")} sandbox="allow-same-origin"
                className="aspect-video w-full rounded-[8px] border border-line bg-paper-2" />
            ) : apercu
              ? <img src={apercu} alt="" className="aspect-video w-full rounded-[8px] border border-line object-cover" />
              : <div className="aspect-video w-full rounded-[8px] border border-line bg-paper-2" />}
            <figcaption className="text-[0.76rem] leading-[1.4] text-muted">
              {t(gabarit.present ? "settings.screensImportCaption" : "settings.screensPreview")}
            </figcaption>
            {rapport && (
              <div className="grid gap-0.5">
                <p role="status" className="m-0 text-[0.72rem] leading-[1.4] text-deep">
                  {t("settings.screensImportDone", { pris: rapport.pris, ignores: rapport.ignores, vides: rapport.vides })}
                </p>
                {rapport.ignores > 0 && (
                  <p className="m-0 text-[0.7rem] leading-[1.35] text-muted">
                    {t("settings.screensImportIgnored", { liste: rapport.liste })}
                  </p>
                )}
              </div>
            )}
          </figure>
        </div>
      </div>
    ),
  };

  // Only the tabs that have content are offered; the others are announced as disabled.
  const onglets = d.sections.map((s: string) => ({
    cle: s,
    label: t(`settings.tabs.${s}`),
    eteint: !contenu[s],
  }));
  const avecEnregistrement = onglet !== "logo" && onglet !== "prayer" && !!contenu[onglet];

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("settings.title")}</h1>
      {/* A single form for the whole screen: the tabs split it, a single button saves it. */}
      <form onSubmit={enregistrer}>
        <Tabs items={onglets} base="reglages" label={t("settings.title")}
          valeur={onglet} onChange={(c) => { setOnglet(c); setMsg(null); }} />
        <Card>
          <div key={onglet} role="tabpanel" id={idPanneau("reglages", onglet)} aria-labelledby={idOnglet("reglages", onglet)}>
            {contenu[onglet] ?? <p className="text-muted">{t("settings.soon")}</p>}
          </div>
          {avecEnregistrement && (
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <Btn type="submit" disabled={enCours || !modifie}>{enCours ? t("common.saving") : t("settings.save")}</Btn>
              {!msg && <span className="text-[0.88rem] text-muted">{modifie ? t("settings.unsaved") : t("settings.allSaved")}</span>}
            </div>
          )}
          {msg && (
            <p role={msg.ok ? "status" : "alert"} className={`mt-3 mb-0 text-[0.92rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</p>
          )}
        </Card>
      </form>
    </div>
  );
}
