import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api";
import { liste } from "../format";
import { Bulle, Btn, Card, Dialogue, Field, Tabs, Title, inputClass, toucher, toucherEnLigne } from "../ui";
import { idOnglet, idPanneau } from "../onglets";

type Message = { ok: boolean; texte: string } | null;

/** A live destination as the screen edits it: the key comes back masked, and stays masked. */
type Entree = { mode: "camera" | "push"; url: string; push_rtmp: string };
type Stockage = { endpoint: string; region: string; bucket: string; access_key: string;
                  secret_key: string; prefix: string; enabled: boolean };
type Cible = { id?: number; label: string; url: string; enabled: boolean };
/** Texts of the slates as the screen edits them: {écran: {champ: texte}}; empty = default. */
type Ecrans = Record<string, Record<string, string>>;
/** Imported reference file: presence, same-origin address, size; no machine path. */
type Gabarit = { present: boolean; url: string; name?: string; size?: number; imported_at?: string };
/** What the import took, ignored and left empty — shown as one short sentence. */
type RapportImport = { pris: number; ignores: number; vides: number; liste: string };

const ECRANS = ["avant", "priere", "entracte", "termine", "janaza", "dars_fin"] as const;

// Deux par ligne ; le pied de page occupe la largeur, les autres tiennent à deux.
const CHAMPS_ECRAN = [
  { champ: "eyebrow", large: false },
  { champ: "title", large: true },
  { champ: "sub", large: false },
  { champ: "foot", large: true },
] as const;

const CHAMPS_MOSQUEE = [
  "name", "timezone", "clip_title", "mawaqit_url", "orchestrator_url", "orchestrator_token",
  "state_dir", "output_dir", "publish_dir",
] as const;
const CHAMPS_MODELE = ["enabled", "url", "model", "protocol"] as const;

/** Form values from the server response (all as text, as the API expects). */
function depuisServeur(d: any): Record<string, string> {
  const f: Record<string, string> = {};
  for (const k of CHAMPS_MOSQUEE) f[k] = d.mosque[k] ?? "";
  f.enabled = d.model.enabled ? "1" : "0";
  for (const k of ["url", "model", "protocol"]) f[k] = d.model[k] ?? "";
  f.clips = String(d.processing?.clips ?? 3);
  f.clips_enabled = d.processing?.enabled === false ? "0" : "1";
  return f;
}

/** Folder picker window: we browse the server's real folders, without typing anything. */
function Parcourir({ depart, onChoix, onFermer }: { depart: string; onChoix: (chemin: string) => void; onFermer: () => void }) {
  const { t } = useTranslation();
  const [vue, setVue] = useState<any>(null);
  const [erreur, setErreur] = useState("");

  const ouvrir = (chemin: string) => {
    setErreur("");
    api.folders(chemin).then(setVue).catch((e) => setErreur(e.message));
  };
  // First listing: the error is still empty, only the answer is awaited.
  useEffect(() => { api.folders(depart).then(setVue).catch((e) => setErreur(e.message)); }, [depart]);

  return (
    // Le focus entre dans la fenêtre, Tab y tourne en rond, Échap la ferme et le
    // focus revient au bouton « Parcourir » qui l'a ouverte.
    <Dialogue label={t("settings.folderTitle")} onFermer={onFermer}
      boite="grid max-h-[85vh] w-full max-w-[640px] grid-rows-[auto_auto_1fr_auto] gap-3 rounded-[10px] border border-line bg-white p-4.5">
      <Title>{t("settings.folderTitle")}</Title>
      {erreur && <p role="alert" className="m-0 text-[0.9rem] text-[#7a1f20]">{erreur}</p>}
      {!vue ? (
        <p className="text-muted">{t("common.loading")}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <select
              className={`${inputClass} w-auto`}
              aria-label={t("settings.folderRoots")}
              value=""
              onChange={(e) => e.target.value && ouvrir(e.target.value)}
            >
              <option value="">{t("settings.folderRoots")}</option>
              {vue.roots.map((r: any) => <option key={r.path} value={r.path}>{r.label}</option>)}
            </select>
            <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]" disabled={!vue.parent} onClick={() => ouvrir(vue.parent)}>
              {t("settings.folderUp")}
            </Btn>
          </div>
          <div className="min-h-0 overflow-auto rounded-lg border border-line">
            <p className="m-0 border-b border-line bg-paper-2 px-3 py-2 font-mono text-[0.85rem] text-muted">
              {t("settings.folderCurrent")} : {vue.path}
            </p>
            {vue.entries.length === 0 ? (
              <p className="m-0 px-3 py-3 text-[0.9rem] text-muted">{t("settings.folderEmpty")}</p>
            ) : (
              <ul className="m-0 list-none p-0">
                {vue.entries.map((e: any) => (
                  <li key={e.path}>
                    <button type="button" onClick={() => ouvrir(e.path)}
                      className="w-full cursor-pointer border-0 border-b border-line bg-transparent px-3 py-2 text-left font-mono text-[0.88rem] hover:bg-paper-2">
                      {e.name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {!vue.writable && <p className="m-0 text-[0.85rem] text-[#7a1f20]">{t("settings.folderNotWritable")}</p>}
        </>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Btn type="button" variante="ghost" onClick={onFermer}>{t("settings.folderCancel")}</Btn>
        <Btn type="button" disabled={!vue || !vue.exists || !vue.writable} onClick={() => vue && onChoix(vue.path)}>
          {t("settings.folderUse")}
        </Btn>
      </div>
    </Dialogue>
  );
}

/** An empty folder field is not a blank box: it is the program's default folder,
 *  and the default path is recalled below in relative plain text (“data/state”). */
function Chemin({ valeur, defaut }: { valeur: string; defaut?: string }) {
  const { t } = useTranslation();
  return (
    <>
      <input className={`${inputClass} font-mono text-[0.88rem]`} value={valeur} placeholder={t("settings.defaultFolder")} readOnly />
      <span className="mt-1 block text-[0.82rem] text-muted">
        {t("settings.byDefault")}{" "}<span className="font-mono">{defaut || t("settings.defaultFolder")}</span>
      </span>
    </>
  );
}

export default function Settings() {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState("");
  const [onglet, setOnglet] = useState("mosque");
  const [sousOnglet, setSousOnglet] = useState("connexion");
  const [enCours, setEnCours] = useState(false);
  const [msg, setMsg] = useState<Message>(null);
  const [parcourir, setParcourir] = useState<string | null>(null);
  const [cibles, setCibles] = useState<Cible[]>([]);
  const [ciblesInitiales, setCiblesInitiales] = useState("");
  const [entree, setEntree] = useState<Entree>({ mode: "camera", url: "", push_rtmp: "" });
  const [entreeInitiale, setEntreeInitiale] = useState("");
  const [essai, setEssai] = useState<{ ok: boolean; detail: string } | null>(null);
  const [stockage, setStockage] = useState<Stockage>({ endpoint: "", region: "auto", bucket: "", access_key: "",
    secret_key: "", prefix: "rediffusions/", enabled: false });
  const [stockageInitial, setStockageInitial] = useState("");
  const [essaiStockage, setEssaiStockage] = useState<{ ok: boolean; detail: string } | null>(null);
  const [test, setTest] = useState<{ etat: "cours" | "ok" | "echec"; texte: string } | null>(null);
  const [ecrans, setEcrans] = useState<Ecrans>({});
  const [ecransInitial, setEcransInitial] = useState("");
  const [ecranVue, setEcranVue] = useState<string>("avant");
  const [apercu, setApercu] = useState("");
  const [gabarit, setGabarit] = useState<Gabarit>({ present: false, url: "" });
  const [rapport, setRapport] = useState<RapportImport | null>(null);
  const [importe, setImporte] = useState(false);
  const [guide, setGuide] = useState({ agent: "", humain: "", docs: "", agentEn: "", humainEn: "", docsEn: "" });
  const [adresse, setAdresse] = useState("");
  const [adresseInitiale, setAdresseInitiale] = useState("");
  const fichier = useRef<HTMLInputElement>(null);
  const gabaritFichier = useRef<HTMLInputElement>(null);

  const charger = () =>
    api.settings().then((r) => {
      const v = depuisServeur(r);
      const c: Cible[] = liste<any>(r.targets).map((t: any) => ({ id: t.id, label: t.label, url: t.url, enabled: !!t.enabled }));
      const e: Entree = { mode: r.input?.mode === "push" ? "push" : "camera", url: r.input?.url ?? "",
                          push_rtmp: r.input?.push_rtmp ?? "" };
      setD(r); setF(v); setInitial(JSON.stringify(v)); setCibles(c); setCiblesInitiales(JSON.stringify(c));
      setGuide({ agent: typeof r.guide?.url === "string" ? r.guide.url : `${window.location.origin}/docs/agent-gabarit-ecrans.md`,
                 humain: typeof r.guide?.human === "string" ? r.guide.human : `${window.location.origin}/docs/ecrans-mode-emploi`,
                 docs: typeof r.guide?.docs === "string" ? r.guide.docs : `${window.location.origin}/docs`,
                 agentEn: typeof r.guide?.url_en === "string" ? r.guide.url_en : `${window.location.origin}/docs/en/screen-template-prompt.md`,
                 humainEn: typeof r.guide?.human_en === "string" ? r.guide.human_en : `${window.location.origin}/docs/en/screens`,
                 docsEn: typeof r.guide?.docs_en === "string" ? r.guide.docs_en : `${window.location.origin}/docs?langue=en` });
      const ad = typeof r.guide?.public_url === "string" ? r.guide.public_url : "";
      setAdresse(ad); setAdresseInitiale(ad);
      setEntree(e); setEntreeInitiale(JSON.stringify(e));
      const st: Stockage = { endpoint: r.storage?.endpoint ?? "", region: r.storage?.region ?? "auto",
        bucket: r.storage?.bucket ?? "", access_key: r.storage?.access_key ?? "",
        secret_key: r.storage?.secret_key ?? "", prefix: r.storage?.prefix ?? "rediffusions/",
        enabled: !!r.storage?.enabled };
      setStockage(st); setStockageInitial(JSON.stringify(st));
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
  const defauts = d.defaults ?? {};
  // Les documents existent en deux langues : on ouvre celle de l'interface, pas celle du code.
  const en = i18n.language === "en";
  const lienHumain = en && guide.humainEn ? guide.humainEn : guide.humain;
  const lienAgent = en && guide.agentEn ? guide.agentEn : guide.agent;
  const entreeModifiee = JSON.stringify(entree) !== entreeInitiale;
  const stockageModifie = JSON.stringify(stockage) !== stockageInitial;
  const ecransModifies = JSON.stringify(ecrans) !== ecransInitial;
  const adresseModifiee = adresse.trim().replace(/\/+$/, "") !== adresseInitiale;
  const modifie = JSON.stringify(f) !== initial || JSON.stringify(cibles) !== ciblesInitiales
    || entreeModifiee || stockageModifie || ecransModifies || adresseModifiee;
  const majCible = (i: number, champ: keyof Cible, valeur: string | boolean) => {
    setCibles((x) => x.map((c, j) => (j === i ? { ...c, [champ]: valeur } : c)));
    setMsg(null);
  };
  const maj = (cle: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setF((x) => ({ ...x, [cle]: e.target.value }));
    setMsg(null);
  };

  /** A single save for the whole screen: the mosque, then the model if it changed. */
  const enregistrer = async (e: React.FormEvent) => {
    e.preventDefault();
    setEnCours(true); setMsg(null);
    try {
      const avant = JSON.parse(initial);
      const mosquee: Record<string, string> = {};
      for (const k of CHAMPS_MOSQUEE) mosquee[k] = f[k];
      await api.reglagesMosquee(mosquee);
      if (f.clips !== avant.clips || f.clips_enabled !== avant.clips_enabled) {
        await api.traitement(f.clips, f.clips_enabled === "1");
      }
      if (CHAMPS_MODELE.some((k) => f[k] !== avant[k])) {
        const modele: Record<string, string> = {};
        for (const k of CHAMPS_MODELE) modele[k] = f[k];
        await api.modele(modele);
      }
      if (JSON.stringify(cibles) !== ciblesInitiales) await api.destinations(cibles);
      if (entreeModifiee) await api.saveInput(entree.mode, entree.url);
      if (stockageModifie) {
        await api.stockage({ ...stockage, enabled: stockage.enabled ? "1" : "0" });
        setEssaiStockage(null);
      }
      if (ecransModifies) await api.ecrans(ecrans);
      if (adresseModifiee) await api.adresseInstance(adresse.trim());
      await charger();
      setTest(null);
      setMsg({ ok: true, texte: t("settings.saved") });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    } finally {
      setEnCours(false);
    }
  };

  const testerStockage = async () => {
    setEssaiStockage(null);
    try {
      const r = await api.testStockage({ ...stockage, enabled: stockage.enabled ? "1" : "0" });
      setEssaiStockage({ ok: r.ok, detail: r.detail });
    } catch (err: any) {
      setEssaiStockage({ ok: false, detail: err.message });
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

  const tester = async () => {
    setTest({ etat: "cours", texte: t("settings.modelTesting") });
    try {
      const r = await api.testerModele();
      setTest({ etat: "ok", texte: r.message });
    } catch (err: any) {
      setTest({ etat: "echec", texte: err.message });
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

  const dossiers = [
    { cle: "state_dir", defaut: defauts.state, label: t("settings.stateDir"), quoi: t("settings.shortState") },
    { cle: "output_dir", defaut: defauts.output, label: t("settings.outputDir"), quoi: t("settings.shortOutput") },
    { cle: "publish_dir", defaut: defauts.publish, label: t("settings.publishDir"), quoi: t("settings.shortPublish") },
  ];
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
        <Field label={t("settings.clipTitle")} aide={t("settings.clipTitleHelp")}>
          <input className={`${inputClass} max-w-[420px]`} value={f.clip_title} onChange={maj("clip_title")} />
        </Field>
        <Field label={t("settings.mawaqit")} aide={t("settings.mawaqitHelp")}>
          <input className={inputClass} value={f.mawaqit_url} onChange={maj("mawaqit_url")} placeholder="https://mawaqit.net/fr/…" inputMode="url" />
        </Field>
        <Field label={t("settings.publicUrl")} aide={t("settings.publicUrlHelp")}>
          <input className={inputClass} value={adresse} placeholder="https://sada.exemple.org"
            onChange={(e) => { setAdresse(e.target.value); setMsg(null); }} inputMode="url" />
        </Field>
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
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("settings.orchestrator")} aide={t("settings.orchestratorHelp")}>
            <input className={inputClass} value={f.orchestrator_url} onChange={maj("orchestrator_url")} placeholder="http://127.0.0.1:8088" />
          </Field>
          <Field label={t("settings.token")} aide={t("settings.tokenHelp")}>
            <input className={inputClass} type="password" autoComplete="off" value={f.orchestrator_token} onChange={maj("orchestrator_token")} />
          </Field>
        </div>
        <p className="m-0 text-[0.82rem] leading-[1.5] text-muted">
          {t("settings.orchDoc")}{" "}
          <a href="/docs/orchestrateur" target="_blank" rel="noreferrer"
            className={`${toucherEnLigne} font-semibold text-deep underline decoration-line decoration-1 underline-offset-4`}>
            {t("settings.orchDocLink")}
          </a>
        </p>

        {/* Video input: the same choice as in "Mise en route", editable here afterwards. */}
        <div className="border-t border-line pt-3.5">
          <p className="m-0 mb-2 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-green">{t("settings.inputKicker")}</p>
          <Tabs items={[{ cle: "camera", label: t("onboarding.cameraTitle") },
                        { cle: "push", label: t("onboarding.pushTitle") }]}
            base="reglages-entree" label={t("settings.inputKicker")}
            valeur={entree.mode}
            onChange={(m) => { setEntree((e) => ({ ...e, mode: m === "push" ? "push" : "camera" })); setEssai(null); setMsg(null); }} />
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
    folders: (
      <div>
        {/* The introduction describes the three fields: it goes before, not under the first one. */}
        <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.foldersHint")}</p>
        <div className="grid gap-2.5">
          {dossiers.map((ch) => (
            /* One folder per line: the path, and its two buttons beside it. */
            <div key={ch.cle} className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
              <Field label={`${ch.label} — ${ch.quoi}`}>
                <Chemin valeur={f[ch.cle]} defaut={ch.defaut} />
              </Field>
              <div className="flex gap-2 sm:pb-0.5">
                <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]" onClick={() => setParcourir(ch.cle)}>
                  {t("common.browse")}
                </Btn>
                <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]" disabled={!f[ch.cle]}
                  onClick={() => { setF((x) => ({ ...x, [ch.cle]: "" })); setMsg(null); }}>
                  {t("settings.folderClear")}
                </Btn>
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
    model: (
      <div className="grid gap-3">
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.modelHint")}</p>
        <label className="flex items-center gap-2 text-[0.95rem] font-semibold">
          <input type="checkbox" checked={f.enabled === "1"}
            onChange={(e) => { setF((x) => ({ ...x, enabled: e.target.checked ? "1" : "0" })); setMsg(null); }} />
          {t("settings.modelEnabled")}
        </label>
        {/* Three fields on two lines rather than one column: the tab fits on a laptop screen. */}
        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field label={t("settings.modelUrl")}>
            <input className={inputClass} value={f.url} onChange={maj("url")} placeholder={d.model.effective?.url} inputMode="url" />
          </Field>
          <Field label={t("settings.modelName")}>
            <input className={inputClass} value={f.model} onChange={maj("model")} placeholder={d.model.effective?.model} />
          </Field>
        </div>
        <Field label={t("settings.modelProtocol")} aide={d.model.has_key ? t("settings.modelKeyKept") : undefined}>
          <select className={`${inputClass} max-w-[260px]`} value={f.protocol} onChange={maj("protocol")}>
            <option value="">{t("settings.modelAuto")}</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </Field>
        <p className="m-0 text-[0.85rem] text-muted">{t("settings.modelNow", { label: d.model.label })}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Bulle texte={t("settings.modelTestSave")} actif={modifie}>
            <Btn type="button" variante="ghost" disabled={modifie || test?.etat === "cours"} onClick={tester}>
              {t("settings.modelTest")}
            </Btn>
          </Bulle>
          {test && (
            <span role="status" className={`text-[0.9rem] ${test.etat === "echec" ? "text-[#7a1f20]" : "text-muted"}`}>{test.texte}</span>
          )}
        </div>
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
    screens: (
      <div className="grid gap-3">
        {/* Un seul formulaire, deux colonnes : champs à gauche, aperçu collé à droite. */}
        <div className="grid items-start gap-x-5 gap-y-3 sm:grid-cols-[minmax(0,1fr)_250px]">
          <div className="grid min-w-0 gap-3">
            <div className="grid gap-1.5">
              <Tabs items={ECRANS.map((c) => ({ cle: c, label: t(`settings.screenNames.${c}`) }))}
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
              {CHAMPS_ECRAN.map(({ champ, large }) => (
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
              ))}
            </div>
            <p className="m-0 max-w-[62ch] text-[0.8rem] leading-[1.5] text-muted">{t("settings.screensVars")}</p>
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
    replays: (
      <div className="grid gap-1.5">
        {/* Where the recordings go after the live: one line per field, the folder beside its switch. */}
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.replayHint")}</p>
        <Field label={t("settings.replayEndpoint")}>
          <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.endpoint} spellCheck={false}
            placeholder="https://<compte>.r2.cloudflarestorage.com"
            onChange={(e) => { setStockage((s) => ({ ...s, endpoint: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
        </Field>
        <div className="grid gap-2.5 sm:grid-cols-[1fr_9rem]">
          <Field label={t("settings.replayBucket")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.bucket} spellCheck={false}
              placeholder="sada-rediffusions"
              onChange={(e) => { setStockage((s) => ({ ...s, bucket: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
          </Field>
          <Field label={t("settings.replayRegion")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.region} spellCheck={false}
              placeholder="auto"
              onChange={(e) => { setStockage((s) => ({ ...s, region: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
          </Field>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Field label={t("settings.replayAccessKey")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.access_key}
              autoComplete="off" spellCheck={false}
              onChange={(e) => { setStockage((s) => ({ ...s, access_key: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
          </Field>
          <Field label={t("settings.replaySecretKey")} aide={stockage.secret_key === "••••••" ? t("settings.replayKeyKept") : undefined}>
            <input className={inputClass} type="password" autoComplete="off" value={stockage.secret_key}
              onChange={(e) => { setStockage((s) => ({ ...s, secret_key: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
          </Field>
        </div>
        <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto]">
          <Field label={t("settings.replayPrefix")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.prefix} spellCheck={false}
              placeholder="rediffusions/"
              onChange={(e) => { setStockage((s) => ({ ...s, prefix: e.target.value })); setEssaiStockage(null); setMsg(null); }} />
          </Field>
          <label className="flex items-center gap-1.5 pb-2.5 text-[0.9rem] font-semibold whitespace-nowrap">
            <input type="checkbox" checked={stockage.enabled}
              onChange={(e) => { setStockage((s) => ({ ...s, enabled: e.target.checked })); setMsg(null); }} />
            {t("settings.replayEnabled")}
          </label>
          <Bulle texte={t("settings.replaySaveFirst")} actif={stockageModifie}>
            <Btn type="button" variante="ghost" className="mb-0.5 whitespace-nowrap" disabled={stockageModifie} onClick={testerStockage}>
              {t("settings.replayTest")}
            </Btn>
          </Bulle>
        </div>
        {essaiStockage && (
          <span role="status" className={`text-[0.88rem] ${essaiStockage.ok ? "text-green" : "text-[#7a1f20]"}`}>{essaiStockage.detail}</span>
        )}
      </div>
    ),
  };

  // Two tabs have nothing to save here: they point to the screen that owns them.
  contenu.connectors = (
    <div className="grid gap-2 max-w-[520px] text-[0.95rem]">
      <p className="m-0 text-muted">{t("settings.connectorsHint")}</p>
      <Link className={`${toucher} w-fit font-semibold text-deep underline`} to="/publications">{t("settings.connectorsGo")}</Link>
    </div>
  );
  contenu.access = (
    <div className="grid gap-2 max-w-[520px] text-[0.95rem]">
      <p className="m-0 text-muted">{t("settings.accessHint")}</p>
      <Link className={`${toucher} w-fit font-semibold text-deep underline`} to="/users">{t("settings.accessGo")}</Link>
    </div>
  );

  // Only the tabs that have content are offered; the others are announced as disabled.
  const onglets = d.sections.map((s: string) => ({
    cle: s,
    label: t(`settings.tabs.${s}`),
    eteint: !contenu[s],
  }));
  const avecEnregistrement = onglet !== "logo" && !!contenu[onglet];

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
      {parcourir && (
        <Parcourir
          depart={f[parcourir]}
          onFermer={() => setParcourir(null)}
          onChoix={(chemin) => { setF((x) => ({ ...x, [parcourir]: chemin })); setMsg(null); setParcourir(null); }}
        />
      )}
    </div>
  );
}
