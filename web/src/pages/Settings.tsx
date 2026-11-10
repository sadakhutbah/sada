import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Btn, Card, Field, Tabs, Title, inputClass } from "../ui";

type Message = { ok: boolean; texte: string } | null;

/** A live destination as the screen edits it: the key comes back masked, and stays masked. */
type Cible = { id?: number; label: string; url: string; enabled: boolean };

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
  const [enCours, setEnCours] = useState(false);
  const [msg, setMsg] = useState<Message>(null);
  const [parcourir, setParcourir] = useState<string | null>(null);
  const [cibles, setCibles] = useState<Cible[]>([]);
  const [ciblesInitiales, setCiblesInitiales] = useState("");
  const [test, setTest] = useState<{ etat: "cours" | "ok" | "echec"; texte: string } | null>(null);
  const fichier = useRef<HTMLInputElement>(null);

  const charger = () =>
    api.settings().then((r) => {
      const v = depuisServeur(r);
      const c: Cible[] = (r.targets ?? []).map((t: any) => ({ id: t.id, label: t.label, url: t.url, enabled: !!t.enabled }));
      setD(r); setF(v); setInitial(JSON.stringify(v)); setCibles(c); setCiblesInitiales(JSON.stringify(c));
    }).catch(() => setD(null));
  useEffect(() => { charger(); }, []);

  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  const defauts = d.defaults ?? {};
  const modifie = JSON.stringify(f) !== initial || JSON.stringify(cibles) !== ciblesInitiales;
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
      await charger();
      setTest(null);
      setMsg({ ok: true, texte: t("settings.saved") });
    } catch (err: any) {
      setMsg({ ok: false, texte: err.message });
    } finally {
      setEnCours(false);
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
      <div className="grid max-w-[520px] gap-3.5">
        <Field label={t("settings.name")}><input className={inputClass} value={f.name} onChange={maj("name")} required /></Field>
        <Field label={t("settings.timezone")} aide={t("settings.timezoneHelp")}>
          <input className={inputClass} value={f.timezone} onChange={maj("timezone")} list="fuseaux" placeholder="Europe/Paris" />
          <datalist id="fuseaux">{fuseaux.map((z) => <option key={z} value={z} />)}</datalist>
        </Field>
        <Field label={t("settings.clipTitle")} aide={t("settings.clipTitleHelp")}>
          <input className={inputClass} value={f.clip_title} onChange={maj("clip_title")} />
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
      <div className="grid max-w-[560px] gap-3.5">
        <Field label={t("settings.orchestrator")} aide={t("settings.orchestratorHelp")}>
          <input className={inputClass} value={f.orchestrator_url} onChange={maj("orchestrator_url")} placeholder="http://127.0.0.1:8089" inputMode="url" />
        </Field>
        <Field label={t("settings.token")} aide={t("settings.tokenHelp")}>
          <input className={inputClass} type="password" autoComplete="off" value={f.orchestrator_token} onChange={maj("orchestrator_token")} />
        </Field>

        <div className="mt-2 border-t border-line pt-3.5">
          <Title>{t("settings.destTitle")}</Title>
          <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.destHint")}</p>
          {cibles.length === 0 ? (
            <p className="m-0 mb-3 text-[0.9rem] text-muted">{t("settings.destNone")}</p>
          ) : (
            <ul className="m-0 mb-3 grid list-none gap-3 p-0">
              {cibles.map((c, i) => (
                <li key={c.id ?? `n${i}`} className="grid gap-2 rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="grid flex-1 gap-1 text-[0.85rem] font-semibold">
                      {t("settings.destLabel")}
                      <input className={`${inputClass} text-[0.9rem] font-normal`} value={c.label}
                        placeholder={t("settings.destLabelPlaceholder")} maxLength={60}
                        onChange={(e) => majCible(i, "label", e.target.value)} />
                    </label>
                    <label className="flex items-center gap-2 pb-2 text-[0.9rem] font-semibold">
                      <input type="checkbox" checked={c.enabled} onChange={(e) => majCible(i, "enabled", e.target.checked)} />
                      {t("settings.destEnabled")}
                    </label>
                    <Btn type="button" variante="dangerGhost" className="min-h-9 px-3 py-1.5 text-[0.9rem]"
                      onClick={() => { setCibles((x) => x.filter((_, j) => j !== i)); setMsg(null); }}>
                      {t("settings.destRemove")}
                    </Btn>
                  </div>
                  <label className="grid gap-1 text-[0.85rem] font-semibold">
                    {t("settings.destUrl")}
                    <input className={`${inputClass} font-mono text-[0.85rem] font-normal`} value={c.url}
                      placeholder={t("settings.destExample")} spellCheck={false} autoComplete="off"
                      onChange={(e) => majCible(i, "url", e.target.value)} />
                  </label>
                </li>
              ))}
            </ul>
          )}
          <Btn type="button" variante="ghost" className="min-h-9 px-3 py-1.5 text-[0.9rem]"
            disabled={cibles.length >= 6}
            onClick={() => { setCibles((x) => [...x, { label: "", url: "", enabled: true }]); setMsg(null); }}>
            {t("settings.destAdd")}
          </Btn>
        </div>
      </div>
    ),
    folders: (
      <div>
        <Title>{t("settings.foldersTitle")}</Title>
        {/* The introduction describes the three fields: it goes before, not under the first one. */}
        <p className="mt-0 mb-4 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.foldersHint")}</p>
        <div className="grid gap-4">
          {dossiers.map((ch) => (
            <div key={ch.cle}>
              <Field label={`${ch.label} — ${ch.quoi}`}>
                <Chemin valeur={f[ch.cle]} defaut={ch.defaut} />
              </Field>
              <div className="mt-2 flex flex-wrap gap-2">
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
      <div className="grid max-w-[520px] gap-3.5">
        <Title>{t("settings.modelTitle")}</Title>
        <p className="m-0 text-[0.9rem] text-muted">{t("settings.modelHint")}</p>
        <label className="flex items-center gap-2 text-[0.95rem] font-semibold">
          <input type="checkbox" checked={f.enabled === "1"}
            onChange={(e) => { setF((x) => ({ ...x, enabled: e.target.checked ? "1" : "0" })); setMsg(null); }} />
          {t("settings.modelEnabled")}
        </label>
        <Field label={t("settings.modelUrl")}>
          <input className={inputClass} value={f.url} onChange={maj("url")} placeholder={d.model.effective?.url} inputMode="url" />
        </Field>
        <Field label={t("settings.modelName")}>
          <input className={inputClass} value={f.model} onChange={maj("model")} placeholder={d.model.effective?.model} />
        </Field>
        <Field label={t("settings.modelProtocol")} aide={d.model.has_key ? t("settings.modelKeyKept") : undefined}>
          <select className={inputClass} value={f.protocol} onChange={maj("protocol")}>
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
