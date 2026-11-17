import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type PlatformMosque } from "../api";
import { Bulle, Btn, Card, Dialogue, Field, Segmented, Tabs, Title, inputClass, toucherEnLigne } from "../ui";
import { idOnglet, idPanneau } from "../onglets";

/** Settings of the installation itself, never of a mosque: replays bucket, folders of the machine,
 *  public address, language model, transcription (and, self-hosted, the orchestrator of the single
 *  mosque). Operator only: the server answers 403 to anyone else. One form visible at a time. */

type Message = { ok: boolean; texte: string } | null;
type Essai = { etat: "cours" | "ok" | "echec"; texte: string } | null;
type Stockage = { endpoint: string; region: string; bucket: string; access_key: string;
                  secret_key: string; prefix: string; enabled: boolean };

const DOSSIERS = ["state", "output", "publish"] as const;

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

/** Orchestrator address and token of one mosque: the platform page (self-hosted) and the operator's
 *  mosque list (hosted) both use it. The token comes back masked; the mask sent back keeps it. */
export function OrchestrateurForm({ mosquee, onSaved }: { mosquee: PlatformMosque; onSaved?: (m: PlatformMosque) => void }) {
  const { t } = useTranslation();
  const [url, setUrl] = useState(mosquee.orchestrator_url);
  const [jeton, setJeton] = useState(mosquee.orchestrator_token);
  const [initial, setInitial] = useState(JSON.stringify([mosquee.orchestrator_url, mosquee.orchestrator_token]));
  const [enCours, setEnCours] = useState(false);
  const [msg, setMsg] = useState<Message>(null);
  const modifie = JSON.stringify([url, jeton]) !== initial;

  const enregistrer = async (e: React.FormEvent) => {
    e.preventDefault();
    setEnCours(true); setMsg(null);
    try {
      const r = await api.orchestrateur(mosquee.id, url.trim(), jeton);
      setUrl(r.mosque.orchestrator_url); setJeton(r.mosque.orchestrator_token);
      setInitial(JSON.stringify([r.mosque.orchestrator_url, r.mosque.orchestrator_token]));
      setMsg({ ok: true, texte: r.message });
      onSaved?.(r.mosque);
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    } finally {
      setEnCours(false);
    }
  };

  return (
    <form onSubmit={enregistrer} className="grid gap-3.5">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("settings.orchestrator")} aide={t("settings.orchestratorHelp")}>
          <input className={inputClass} value={url} inputMode="url" placeholder="http://127.0.0.1:8088"
            onChange={(e) => { setUrl(e.target.value); setMsg(null); }} />
        </Field>
        <Field label={t("settings.token")} aide={t("settings.tokenHelp")}>
          <input className={inputClass} type="password" autoComplete="off" value={jeton}
            onChange={(e) => { setJeton(e.target.value); setMsg(null); }} />
        </Field>
      </div>
      <p className="m-0 text-[0.82rem] leading-[1.5] text-muted">
        {t("settings.orchDoc")}{" "}
        <a href="/docs/orchestrateur" target="_blank" rel="noreferrer"
          className={`${toucherEnLigne} font-semibold text-deep underline decoration-line decoration-1 underline-offset-4`}>
          {t("settings.orchDocLink")}
        </a>
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Btn type="submit" disabled={enCours || !modifie}>{enCours ? t("common.saving") : t("common.save")}</Btn>
        {msg && <span role={msg.ok ? "status" : "alert"} className={`text-[0.9rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</span>}
      </div>
    </form>
  );
}

export default function PlatformSettings() {
  const { t, i18n } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [erreur, setErreur] = useState("");
  const [onglet, setOnglet] = useState("replays");
  const [msg, setMsg] = useState<Message>(null);
  const [enCours, setEnCours] = useState(false);
  const [stockage, setStockage] = useState<Stockage>({ endpoint: "", region: "auto", bucket: "", access_key: "",
    secret_key: "", prefix: "rediffusions/", enabled: false });
  const [dossiers, setDossiers] = useState<Record<string, string>>({});
  const [adresse, setAdresse] = useState("");
  const [modele, setModele] = useState<Record<string, string>>({});
  const [tx, setTx] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [parcourir, setParcourir] = useState<string | null>(null);
  const [essaiStockage, setEssaiStockage] = useState<{ ok: boolean; detail: string } | null>(null);
  const [essaiModele, setEssaiModele] = useState<Essai>(null);
  const [essaiTx, setEssaiTx] = useState<Essai>(null);

  const charger = () =>
    api.platform().then((r) => {
      const st: Stockage = { endpoint: r.storage?.endpoint ?? "", region: r.storage?.region ?? "auto",
        bucket: r.storage?.bucket ?? "", access_key: r.storage?.access_key ?? "",
        secret_key: r.storage?.secret_key ?? "", prefix: r.storage?.prefix ?? "rediffusions/",
        enabled: !!r.storage?.enabled };
      const dos: Record<string, string> = {};
      for (const k of DOSSIERS) dos[k] = r.folders?.[k]?.value ?? "";
      const mo = { enabled: r.model?.enabled ? "1" : "0", url: r.model?.url ?? "", model: r.model?.model ?? "",
                   protocol: r.model?.protocol ?? "" };
      // The key never comes back from the API: the field starts empty, and empty means "keep it".
      const tr = { mode: r.transcription?.mode === "distant" ? "distant" : "local",
                   provider: r.transcription?.provider ?? "groq", url: r.transcription?.url ?? "",
                   model: r.transcription?.model ?? "", key: "" };
      setD(r); setStockage(st); setDossiers(dos); setAdresse(r.public_url ?? ""); setModele(mo); setTx(tr);
      setInitial({ replays: JSON.stringify(st), folders: JSON.stringify(dos), public_url: r.public_url ?? "",
                   model: JSON.stringify(mo), transcription: JSON.stringify(tr) });
    }).catch((e) => setErreur(e.message));
  useEffect(() => { charger(); }, []);

  if (erreur) return <p role="alert" className="py-16 text-center text-[#7a1f20]">{erreur}</p>;
  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const courant: Record<string, string> = {
    replays: JSON.stringify(stockage), folders: JSON.stringify(dossiers),
    public_url: adresse.trim().replace(/\/+$/, ""), model: JSON.stringify(modele), transcription: JSON.stringify(tx),
  };
  const modifie = onglet in courant && courant[onglet] !== initial[onglet];
  const avant = <T,>(set: React.Dispatch<React.SetStateAction<T>>) => (v: React.SetStateAction<T>) => { set(v); setMsg(null); };
  const majStockage = avant(setStockage);
  const majModele = (cle: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setModele((x) => ({ ...x, [cle]: e.target.value })); setMsg(null); setEssaiModele(null);
  };
  const majTx = (cle: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setTx((x) => ({ ...x, [cle]: e.target.value })); setMsg(null); setEssaiTx(null);
  };

  /** Each tab saves its own setting: one endpoint per tab, one button per form. */
  const enregistrer = async (e: React.FormEvent) => {
    e.preventDefault();
    setEnCours(true); setMsg(null);
    try {
      let r: any = null;
      if (onglet === "replays") r = await api.stockage({ ...stockage, enabled: stockage.enabled ? "1" : "0" });
      if (onglet === "folders") r = await api.dossiers(dossiers);
      if (onglet === "public_url") r = await api.adresseInstance(adresse.trim());
      if (onglet === "model") r = await api.modele(modele);
      if (onglet === "transcription") r = await api.transcription(tx);
      await charger();
      setEssaiStockage(null);
      setMsg({ ok: true, texte: r?.message || t("settings.saved") });
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
  const tester = async (appel: () => Promise<{ message: string }>, set: (v: Essai) => void, attente: string) => {
    set({ etat: "cours", texte: attente });
    try {
      set({ etat: "ok", texte: (await appel()).message });
    } catch (err: any) {
      set({ etat: "echec", texte: err.message });
    }
  };

  const presetTx = (d.transcription?.providers ?? []).find((p: any) => p.key === tx.provider);
  const prixTx = typeof presetTx?.price_per_hour === "number" ? presetTx.price_per_hour : null;
  const prix = (v: number) =>
    new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", maximumFractionDigits: 3 }).format(v);
  const etiquettes: Record<string, [string, string]> = {
    state: [t("settings.stateDir"), t("settings.shortState")],
    output: [t("settings.outputDir"), t("settings.shortOutput")],
    publish: [t("settings.publishDir"), t("settings.shortPublish")],
  };
  const statut = (e: Essai) => e && (
    <span role="status" className={`text-[0.9rem] ${e.etat === "echec" ? "text-[#7a1f20]" : "text-muted"}`}>{e.texte}</span>
  );

  const contenu: Record<string, React.ReactNode> = {
    replays: (
      <div className="grid gap-1.5">
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.replayHint")}</p>
        <Field label={t("settings.replayEndpoint")}>
          <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.endpoint} spellCheck={false}
            placeholder="https://<compte>.r2.cloudflarestorage.com"
            onChange={(e) => { majStockage((s) => ({ ...s, endpoint: e.target.value })); setEssaiStockage(null); }} />
        </Field>
        <div className="grid gap-2.5 sm:grid-cols-[1fr_9rem]">
          <Field label={t("settings.replayBucket")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.bucket} spellCheck={false}
              placeholder="sada-rediffusions"
              onChange={(e) => { majStockage((s) => ({ ...s, bucket: e.target.value })); setEssaiStockage(null); }} />
          </Field>
          <Field label={t("settings.replayRegion")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.region} spellCheck={false}
              placeholder="auto" onChange={(e) => { majStockage((s) => ({ ...s, region: e.target.value })); setEssaiStockage(null); }} />
          </Field>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2">
          <Field label={t("settings.replayAccessKey")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.access_key} autoComplete="off" spellCheck={false}
              onChange={(e) => { majStockage((s) => ({ ...s, access_key: e.target.value })); setEssaiStockage(null); }} />
          </Field>
          <Field label={t("settings.replaySecretKey")} aide={stockage.secret_key === "••••••" ? t("settings.replayKeyKept") : undefined}>
            <input className={inputClass} type="password" autoComplete="off" value={stockage.secret_key}
              onChange={(e) => { majStockage((s) => ({ ...s, secret_key: e.target.value })); setEssaiStockage(null); }} />
          </Field>
        </div>
        {d.storage?.scope === "platform" && (
          <p className="m-0 max-w-[80ch] text-[0.85rem] leading-[1.5] text-muted">{t("settings.replayPrefixPlatform")}</p>
        )}
        <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto]">
          <Field label={t("settings.replayPrefix")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={stockage.prefix} spellCheck={false}
              placeholder="rediffusions/" onChange={(e) => { majStockage((s) => ({ ...s, prefix: e.target.value })); setEssaiStockage(null); }} />
          </Field>
          <label className="flex items-center gap-1.5 pb-2.5 text-[0.9rem] font-semibold whitespace-nowrap">
            <input type="checkbox" checked={stockage.enabled}
              onChange={(e) => majStockage((s) => ({ ...s, enabled: e.target.checked }))} />
            {t("settings.replayEnabled")}
          </label>
          <Bulle texte={t("settings.replaySaveFirst")} actif={modifie}>
            <Btn type="button" variante="ghost" className="mb-0.5 whitespace-nowrap" disabled={modifie} onClick={testerStockage}>
              {t("settings.replayTest")}
            </Btn>
          </Bulle>
        </div>
        {essaiStockage && (
          <span role="status" className={`text-[0.88rem] ${essaiStockage.ok ? "text-green" : "text-[#7a1f20]"}`}>{essaiStockage.detail}</span>
        )}
      </div>
    ),
    folders: (
      <div>
        <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t(d.hosted ? "platform.foldersHostedHint" : "settings.foldersHint")}</p>
        <div className="grid gap-2.5">
          {DOSSIERS.map((cle) => (
            <div key={cle} className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
              <Field label={`${etiquettes[cle][0]} — ${etiquettes[cle][1]}`}>
                <input className={`${inputClass} font-mono text-[0.88rem]`} value={dossiers[cle] ?? ""}
                  placeholder={t("settings.defaultFolder")} readOnly />
              </Field>
              <div className="flex gap-2 sm:pb-0.5">
                <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]" onClick={() => setParcourir(cle)}>
                  {t("common.browse")}
                </Btn>
                <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]" disabled={!dossiers[cle]}
                  onClick={() => { setDossiers((x) => ({ ...x, [cle]: "" })); setMsg(null); }}>
                  {t("settings.folderClear")}
                </Btn>
              </div>
            </div>
          ))}
        </div>
      </div>
    ),
    public_url: (
      <div className="grid gap-3.5">
        <Field label={t("settings.publicUrl")} aide={t("settings.publicUrlHelp")}>
          <input className={inputClass} value={adresse} placeholder="https://sada.exemple.org" inputMode="url"
            onChange={(e) => { setAdresse(e.target.value); setMsg(null); }} />
        </Field>
      </div>
    ),
    model: (
      <div className="grid gap-3">
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.modelHint")}</p>
        <label className="flex items-center gap-2 text-[0.95rem] font-semibold">
          <input type="checkbox" checked={modele.enabled === "1"}
            onChange={(e) => { setModele((x) => ({ ...x, enabled: e.target.checked ? "1" : "0" })); setMsg(null); }} />
          {t("settings.modelEnabled")}
        </label>
        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field label={t("settings.modelUrl")}>
            <input className={inputClass} value={modele.url} onChange={majModele("url")} placeholder={d.model?.effective?.url} inputMode="url" />
          </Field>
          <Field label={t("settings.modelName")}>
            <input className={inputClass} value={modele.model} onChange={majModele("model")} placeholder={d.model?.effective?.model} />
          </Field>
        </div>
        <Field label={t("settings.modelProtocol")} aide={d.model?.has_key ? t("settings.modelKeyKept") : undefined}>
          <select className={`${inputClass} max-w-[260px]`} value={modele.protocol} onChange={majModele("protocol")}>
            <option value="">{t("settings.modelAuto")}</option>
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
          </select>
        </Field>
        <p className="m-0 text-[0.85rem] text-muted">{t("settings.modelNow", { label: d.model?.label })}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Bulle texte={t("settings.modelTestSave")} actif={modifie}>
            <Btn type="button" variante="ghost" disabled={modifie || essaiModele?.etat === "cours"}
              onClick={() => tester(api.testerModele, setEssaiModele, t("settings.modelTesting"))}>
              {t("settings.modelTest")}
            </Btn>
          </Bulle>
          {statut(essaiModele)}
        </div>
      </div>
    ),
    transcription: (
      <div className="grid gap-3.5">
        <p className="m-0 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.transcriptionHint")}</p>
        <div>
          <span className="mb-1 block text-[0.9rem] font-semibold">{t("settings.transcriptionMode")}</span>
          <Segmented
            items={[{ cle: "local", label: t("settings.transcriptionLocal") }, { cle: "distant", label: t("settings.transcriptionDistant") }]}
            base="plateforme-transcription" label={t("settings.transcriptionMode")} valeur={tx.mode}
            onChange={(c) => { setTx((x) => ({ ...x, mode: c })); setMsg(null); setEssaiTx(null); }} />
          <p className="m-0 max-w-[72ch] text-[0.84rem] leading-[1.5] text-muted">
            {t(tx.mode === "distant" ? "settings.transcriptionDistantHelp" : "settings.transcriptionLocalHelp")}
          </p>
        </div>
        {tx.mode === "distant" && (
          <>
            <div className="grid gap-3.5 sm:grid-cols-2">
              <Field label={t("settings.transcriptionProvider")}>
                <select className={inputClass} value={tx.provider}
                  onChange={(e) => {
                    // Another provider brings its own address and model; the key stays.
                    const p = e.target.value;
                    setTx((x) => ({ ...x, provider: p, url: "", model: "" })); setMsg(null); setEssaiTx(null);
                  }}>
                  {(d.transcription?.providers ?? []).map((p: any) => <option key={p.key} value={p.key}>{p.label}</option>)}
                  <option value="custom">{t("settings.transcriptionCustom")}</option>
                </select>
              </Field>
              <Field label={t("settings.transcriptionModel")}>
                <input className={inputClass} value={tx.model} spellCheck={false}
                  placeholder={presetTx?.model ?? "whisper-1"} onChange={majTx("model")} />
              </Field>
            </div>
            {tx.provider === "custom" ? (
              <Field label={t("settings.transcriptionUrl")} aide={t("settings.transcriptionUrlHelp")}>
                <input className={`${inputClass} font-mono text-[0.88rem]`} value={tx.url}
                  placeholder="https://exemple.org/v1" inputMode="url" spellCheck={false} autoComplete="off" onChange={majTx("url")} />
              </Field>
            ) : (
              <p className="m-0 font-mono text-[0.82rem] text-muted">{presetTx?.url}</p>
            )}
            <Field label={t("settings.transcriptionKey")} aide={t("settings.transcriptionKeyHelp")}>
              <input className={inputClass} type="password" value={tx.key} spellCheck={false}
                placeholder={d.transcription?.has_key ? t("settings.transcriptionKeySet") : "sk-…"}
                autoComplete="new-password" onChange={majTx("key")} />
            </Field>
            <p className="m-0 text-[0.85rem] text-muted">
              {prixTx !== null ? t("settings.transcriptionCost", { price: prix(prixTx) }) : t("settings.transcriptionCostUnknown")}
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Bulle texte={t("settings.transcriptionTestSave")} actif={modifie}>
                <Btn type="button" variante="ghost" disabled={modifie || essaiTx?.etat === "cours"}
                  onClick={() => tester(api.testerTranscription, setEssaiTx, t("settings.transcriptionTesting"))}>
                  {t("settings.transcriptionTest")}
                </Btn>
              </Bulle>
              {statut(essaiTx)}
            </div>
          </>
        )}
        <p className="m-0 text-[0.85rem] text-muted">
          {d.transcription?.mode === "distant"
            ? t("settings.transcriptionNow", { label: d.transcription?.label })
            : t("settings.transcriptionNowLocal")}
        </p>
      </div>
    ),
    orchestrator: d.mosque && <OrchestrateurForm mosquee={d.mosque} />,
  };

  const onglets = (d.sections as string[]).map((s) => ({ cle: s, label: t(`platform.tabs.${s}`) }));

  return (
    <div className="mx-auto grid w-full max-w-[800px] gap-4">
      <div>
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("platform.title")}</h1>
        <p className="mt-1 mb-0 text-[0.9rem] text-muted">{t(d.hosted ? "platform.hintHosted" : "platform.hint")}</p>
      </div>
      <Tabs items={onglets} base="plateforme" label={t("platform.title")}
        valeur={onglet} onChange={(c) => { setOnglet(c); setMsg(null); }} />
      {/* The orchestrator form saves itself: it must not sit inside the page's form. */}
      {onglet === "orchestrator" ? (
        <Card>
          <div role="tabpanel" id={idPanneau("plateforme", onglet)} aria-labelledby={idOnglet("plateforme", onglet)}>
            {contenu.orchestrator}
          </div>
        </Card>
      ) : (
        <form onSubmit={enregistrer}>
          <Card>
            <div key={onglet} role="tabpanel" id={idPanneau("plateforme", onglet)} aria-labelledby={idOnglet("plateforme", onglet)}>
              {contenu[onglet]}
            </div>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <Btn type="submit" disabled={enCours || !modifie}>{enCours ? t("common.saving") : t("settings.save")}</Btn>
              {!msg && <span className="text-[0.88rem] text-muted">{modifie ? t("settings.unsaved") : t("settings.allSaved")}</span>}
            </div>
            {msg && (
              <p role={msg.ok ? "status" : "alert"} className={`mt-3 mb-0 text-[0.92rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</p>
            )}
          </Card>
        </form>
      )}
      {parcourir && (
        <Parcourir depart={dossiers[parcourir] ?? ""} onFermer={() => setParcourir(null)}
          onChoix={(chemin) => { setDossiers((x) => ({ ...x, [parcourir]: chemin })); setMsg(null); setParcourir(null); }} />
      )}
    </div>
  );
}
