import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api";
import { liste } from "../format";
import { Btn, Card, Field, Tabs, Title, inputClass } from "../ui";

type Message = { ok: boolean; texte: string } | null;

/** A live destination as the screen edits it: the key comes back masked, and stays masked. */
type Entree = { mode: "camera" | "push"; url: string; push_rtmp: string };
type Stockage = { endpoint: string; region: string; bucket: string; access_key: string;
                  secret_key: string; prefix: string; enabled: boolean };
type Cible = { id?: number; label: string; url: string; enabled: boolean };
/** Texts of the slates as the screen edits them: {écran: {champ: texte}}; empty = default. */
type Ecrans = Record<string, Record<string, string>>;

const ECRANS = ["avant", "priere", "entracte", "termine", "janaza", "dars_fin"] as const;

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
  useEffect(() => { ouvrir(depart); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-night/50 p-4" role="dialog" aria-modal="true" aria-label={t("settings.folderTitle")}>
      <div className="grid max-h-[85vh] w-full max-w-[640px] grid-rows-[auto_auto_1fr_auto] gap-3 rounded-[10px] border border-line bg-white p-4.5">
        <Title>{t("settings.folderTitle")}</Title>
        {erreur && <p role="alert" className="m-0 text-[0.9rem] text-[#8a2219]">{erreur}</p>}
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
            {!vue.writable && <p className="m-0 text-[0.85rem] text-[#8a2219]">{t("settings.folderNotWritable")}</p>}
          </>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Btn type="button" variante="ghost" onClick={onFermer}>{t("settings.folderCancel")}</Btn>
          <Btn type="button" disabled={!vue || !vue.exists || !vue.writable} onClick={() => vue && onChoix(vue.path)}>
            {t("settings.folderUse")}
          </Btn>
        </div>
      </div>
    </div>
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
  const { t } = useTranslation();
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
  const [aideScreens, setAideScreens] = useState(false);
  const fichier = useRef<HTMLInputElement>(null);

  const charger = () =>
    api.settings().then((r) => {
      const v = depuisServeur(r);
      const c: Cible[] = liste<any>(r.targets).map((t: any) => ({ id: t.id, label: t.label, url: t.url, enabled: !!t.enabled }));
      const e: Entree = { mode: r.input?.mode === "push" ? "push" : "camera", url: r.input?.url ?? "",
                          push_rtmp: r.input?.push_rtmp ?? "" };
      setD(r); setF(v); setInitial(JSON.stringify(v)); setCibles(c); setCiblesInitiales(JSON.stringify(c));
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
  const entreeModifiee = JSON.stringify(entree) !== entreeInitiale;
  const stockageModifie = JSON.stringify(stockage) !== stockageInitial;
  const ecransModifies = JSON.stringify(ecrans) !== ecransInitial;
  const modifie = JSON.stringify(f) !== initial || JSON.stringify(cibles) !== ciblesInitiales
    || entreeModifiee || stockageModifie || ecransModifies;
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
          valeur={sousOnglet} onChange={(c) => { setSousOnglet(c); setMsg(null); }} />
        {sousOnglet === "connexion" && <div className="grid max-w-[520px] gap-3.5">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("settings.orchestrator")} aide={t("settings.orchestratorHelp")}>
            <input className={inputClass} value={f.orchestrator_url} onChange={maj("orchestrator_url")} placeholder="http://127.0.0.1:8088" />
          </Field>
          <Field label={t("settings.token")} aide={t("settings.tokenHelp")}>
            <input className={inputClass} type="password" autoComplete="off" value={f.orchestrator_token} onChange={maj("orchestrator_token")} />
          </Field>
        </div>

        {/* Video input: the same choice as in "Mise en route", editable here afterwards. */}
        <div className="border-t border-line pt-3.5">
          <p className="m-0 mb-2 text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-green">{t("settings.inputKicker")}</p>
          <Tabs items={[{ cle: "camera", label: t("onboarding.cameraTitle") },
                        { cle: "push", label: t("onboarding.pushTitle") }]}
            valeur={entree.mode}
            onChange={(m) => { setEntree((e) => ({ ...e, mode: m === "push" ? "push" : "camera" })); setEssai(null); setMsg(null); }} />
          <div className="mt-3 grid gap-1.5">
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
          <Btn type="button" variante="ghost" disabled={modifie || test?.etat === "cours"} onClick={tester}
            title={modifie ? t("settings.modelTestSave") : undefined}>
            {t("settings.modelTest")}
          </Btn>
          {test && (
            <span role="status" className={`text-[0.9rem] ${test.etat === "echec" ? "text-[#8a2219]" : "text-muted"}`}>{test.texte}</span>
          )}
        </div>
      </div>
    ),
    screens: (
      <div className="grid gap-2.5">
        {/* Un écran à la fois : les sous-onglets évitent une page qui n'en finit pas. */}
        {/* L'aide existe mais se déplie d'elle-même, sans bouton rond posé à côté du texte. */}
        <details className="group max-w-[80ch] rounded-[10px] border border-line bg-paper px-3.5 py-2.5" open={aideScreens}
          onToggle={(e) => setAideScreens((e.currentTarget as HTMLDetailsElement).open)}>
          <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
            <p className="m-0 text-[0.9rem] text-muted">{t("settings.screensHint")}</p>
            <span className="ml-auto shrink-0 text-[0.82rem] font-semibold text-deep underline decoration-line decoration-1 underline-offset-4 group-open:hidden">
              {t("settings.screensHelpOpen")}
            </span>
            <span className="ml-auto hidden shrink-0 text-[0.82rem] font-semibold text-deep underline decoration-line decoration-1 underline-offset-4 group-open:inline">
              {t("settings.screensHelpClose")}
            </span>
          </summary>
          <div className="mt-3 grid gap-3 border-t border-line pt-3 text-[0.88rem] leading-[1.65] text-ink">
            <p className="m-0">{t("settings.screensHelpWhat")}</p>
            <p className="m-0 text-muted">{t("settings.screensHelpEmpty")}</p>
            <p className="m-0 text-muted">{t("settings.screensHelpWhen")}</p>
            <div className="mt-0.5 rounded-[6px] bg-paper-2 px-3 py-2">
              <p className="m-0 text-deep">{t("settings.screensHelpSteps")}</p>
            </div>
          </div>
        </details>
        <Tabs items={ECRANS.map((c) => ({ cle: c, label: t(`settings.screenNames.${c}`) }))}
          valeur={ecranVue} onChange={(c) => { setEcranVue(c); setMsg(null); }} />
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_262px]">
          {/* Deux colonnes de champs : cinq lignes tiennent sans allonger la page ; le pied de page prend la largeur. */}
          <div className="grid content-start gap-2 sm:grid-cols-2">
            {Object.entries<any>(d.screens?.[ecranVue] ?? {}).map(([champ, v]) => (
              <Field key={champ} label={t(`settings.screenFields.${champ}`)}
                className={champ === "foot" ? "sm:col-span-2" : ""}>
                <input className={inputClass} value={ecrans[ecranVue]?.[champ] ?? ""} placeholder={v?.default ?? ""} spellCheck
                  onChange={(e) => {
                    setEcrans((x) => ({ ...x, [ecranVue]: { ...(x[ecranVue] ?? {}), [champ]: e.target.value } }));
                    setMsg(null);
                  }} />
              </Field>
            ))}
          </div>
          <figure className="m-0 grid content-start gap-1">
            {apercu
              ? <img src={apercu} alt="" className="w-full rounded-[6px] border border-line" />
              : <div className="aspect-video rounded-[6px] border border-line bg-deeper/5" />}
            <figcaption className="text-[0.78rem] text-muted">{t("settings.screensPreview")}</figcaption>
          </figure>
        </div>
        <p className="m-0 text-[0.8rem] text-muted">{t("settings.screensVars")}</p>
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
              placeholder="minbar-rediffusions"
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
          <Btn type="button" variante="ghost" className="mb-0.5 whitespace-nowrap" disabled={stockageModifie} onClick={testerStockage}
            title={stockageModifie ? t("settings.replaySaveFirst") : undefined}>
            {t("settings.replayTest")}
          </Btn>
        </div>
        {essaiStockage && (
          <span role="status" className={`text-[0.88rem] ${essaiStockage.ok ? "text-green" : "text-[#8a2219]"}`}>{essaiStockage.detail}</span>
        )}
      </div>
    ),
  };

  // Two tabs have nothing to save here: they point to the screen that owns them.
  contenu.connectors = (
    <div className="grid gap-2 max-w-[520px] text-[0.95rem]">
      <p className="m-0 text-muted">{t("settings.connectorsHint")}</p>
      <Link className="w-fit font-semibold text-deep underline" to="/publications">{t("settings.connectorsGo")}</Link>
    </div>
  );
  contenu.access = (
    <div className="grid gap-2 max-w-[520px] text-[0.95rem]">
      <p className="m-0 text-muted">{t("settings.accessHint")}</p>
      <Link className="w-fit font-semibold text-deep underline" to="/users">{t("settings.accessGo")}</Link>
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
        <Tabs items={onglets} valeur={onglet} onChange={(c) => { setOnglet(c); setMsg(null); }} />
        <Card>
          <div key={onglet}>{contenu[onglet] ?? <p className="text-muted">{t("settings.soon")}</p>}</div>
          {avecEnregistrement && (
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <Btn type="submit" disabled={enCours || !modifie}>{enCours ? t("common.saving") : t("settings.save")}</Btn>
              {modifie && !msg && <span className="text-[0.88rem] text-muted">{t("settings.unsaved")}</span>}
            </div>
          )}
          {msg && (
            <p role={msg.ok ? "status" : "alert"} className={`mt-3 mb-0 text-[0.92rem] ${msg.ok ? "text-deep" : "text-[#8a2219]"}`}>{msg.texte}</p>
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
