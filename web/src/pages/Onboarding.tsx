import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api";
import { Btn, Card, Field, Kicker, Title, inputClass } from "../ui";

/** Mise en route : quatre écrans, un seul formulaire à la fois.
 *  1. d'où vient l'image (caméra de la mosquée, ou un ordinateur qui pousse),
 *  2. où le direct est publié,
 *  3. où partent les rediffusions (option, passable),
 *  4. récapitulatif. Rien d'autre : les réglages fins restent dans Réglages. */

type Cible = { id?: number; label: string; url: string; enabled: boolean };
type Entree = { mode: "camera" | "push"; url: string; push_rtmp: string; configured: boolean };
type Depot = { endpoint: string; region: string; bucket: string; access_key: string;
               secret_key: string; prefix: string; enabled: boolean };

const ETAPES = ["entree", "destinations", "rediffusions", "fin"] as const;
const DEPOT_VIDE: Depot = { endpoint: "", region: "auto", bucket: "", access_key: "", secret_key: "",
                            prefix: "rediffusions/", enabled: false };

export default function Onboarding() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [etape, setEtape] = useState(0);
  const [mode, setMode] = useState<"camera" | "push">("camera");
  const [adresse, setAdresse] = useState("");
  const [obs, setObs] = useState("");
  const [cibles, setCibles] = useState<Cible[]>([]);
  const [essai, setEssai] = useState<{ ok: boolean; detail: string } | null>(null);
  const [test, setTest] = useState(false);
  const [envoi, setEnvoi] = useState(false);
  const [erreur, setErreur] = useState("");
  const [copie, setCopie] = useState(false);
  const [depot, setDepot] = useState<Depot>(DEPOT_VIDE);
  const [essaiDepot, setEssaiDepot] = useState<{ ok: boolean; detail: string } | null>(null);
  const [testDepot, setTestDepot] = useState(false);

  useEffect(() => {
    api.settings().then((r) => {
      const e: Entree = r.input;
      setMode(e.mode === "push" ? "push" : "camera");
      setAdresse(e.url);
      setObs(e.push_rtmp);
      setCibles(r.targets ?? []);
      const s = r.storage ?? {};
      setDepot({ endpoint: s.endpoint ?? "", region: s.region ?? "auto", bucket: s.bucket ?? "",
                 access_key: s.access_key ?? "", secret_key: s.secret_key ?? "",
                 prefix: s.prefix ?? "rediffusions/", enabled: !!s.enabled });
    }).catch((e) => setErreur(e instanceof ApiError ? e.message : t("common.error")));
  }, [t]);

  const tester = async () => {
    setTest(true); setEssai(null); setErreur("");
    try {
      const r = await api.testInput(mode, adresse);
      setEssai({ ok: r.ok, detail: r.detail });
    } catch (e) {
      setErreur(e instanceof ApiError ? e.message : t("common.error"));
    } finally { setTest(false); }
  };

  const enregistrerEntree = async () => {
    const r = await api.saveInput(mode, adresse);
    setObs(r.input.push_rtmp);
    setAdresse(r.input.url);
  };

  /** Facultatif : un compartiment vide ne s'enregistre pas, on passe simplement au récapitulatif. */
  const depotRempli = !!(depot.endpoint || depot.bucket || depot.access_key);
  const enregistrerDepot = async () => {
    if (!depotRempli) return;
    await api.stockage({ ...depot, enabled: depot.enabled ? "1" : "0" });
  };

  const testerDepot = async () => {
    setTestDepot(true); setEssaiDepot(null); setErreur("");
    try {
      const r = await api.testStockage({ ...depot, enabled: depot.enabled ? "1" : "0" });
      setEssaiDepot(r);
    } catch (e) {
      setErreur(e instanceof ApiError ? e.message : t("common.error"));
    } finally { setTestDepot(false); }
  };

  const suivant = async () => {
    setEnvoi(true); setErreur("");
    try {
      if (etape === 0) await enregistrerEntree();
      if (etape === 1) await api.destinations(cibles.map(({ id, label, url, enabled }) => ({ id, label, url, enabled })));
      if (etape === 2) await enregistrerDepot();
      setEtape((n) => Math.min(n + 1, ETAPES.length - 1));
    } catch (e) {
      setErreur(e instanceof ApiError ? e.message : t("common.error"));
    } finally { setEnvoi(false); }
  };

  /** Terminée ou passée : le back-office ne la repropose plus, tout reste dans Réglages. */
  const terminer = async () => {
    try { await api.onboardingDone(); } catch { /* sans importance : on sort quand même */ }
    nav("/");
  };

  const copier = async () => {
    try { await navigator.clipboard.writeText(obs); setCopie(true); setTimeout(() => setCopie(false), 2000); } catch { /* clipboard refusé */ }
  };

  const maj = (i: number, champ: keyof Cible, valeur: string | boolean) =>
    setCibles((liste) => liste.map((c, j) => (j === i ? { ...c, [champ]: valeur } : c)));

  const entree = (
    <Card>
      <Kicker>{t("onboarding.inputKicker")}</Kicker>
      <Title>{t("onboarding.inputTitle")}</Title>
      <div className="mb-4 grid gap-2.5 sm:grid-cols-2">
        {(["camera", "push"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); setEssai(null); }}
            className={`cursor-pointer rounded-[9px] border p-3 text-left ${mode === m ? "border-leaf bg-leaf/10" : "border-line bg-white hover:bg-paper-2"}`}
          >
            <span className="block font-semibold">{t(`onboarding.${m}Title`)}</span>
            <span className="mt-0.5 block text-[0.85rem] text-muted">{t(`onboarding.${m}Hint`)}</span>
          </button>
        ))}
      </div>

      {mode === "camera" ? (
        <div className="grid gap-3">
          <Field label={t("onboarding.cameraUrl")} aide={t("onboarding.cameraUrlHelp")}>
            <input className={`${inputClass} font-mono text-[0.88rem]`} value={adresse}
                   onChange={(e) => { setAdresse(e.target.value); setEssai(null); }} placeholder="rtsp://…" />
          </Field>
          <div className="flex items-center gap-2.5">
            <Btn variante="ghost" onClick={tester} disabled={test}>{test ? t("onboarding.testing") : t("onboarding.test")}</Btn>
            {essai && <span className={`text-[0.88rem] ${essai.ok ? "text-green" : "text-muted"}`}>{essai.detail}</span>}
          </div>
        </div>
      ) : (
        <div className="grid gap-3">
          <Field label={t("onboarding.pushServer")} aide={t("onboarding.pushNote")}>
            <div className="flex gap-2">
              <input className={`${inputClass} font-mono text-[0.88rem]`} value={obs} readOnly />
              <Btn variante="ghost" onClick={copier}>{copie ? t("onboarding.copied") : t("onboarding.copy")}</Btn>
            </div>
          </Field>
          <div className="flex items-center gap-2.5">
            <Btn variante="ghost" onClick={tester} disabled={test}>{test ? t("onboarding.testing") : t("onboarding.test")}</Btn>
            {essai && <span className={`text-[0.88rem] ${essai.ok ? "text-green" : "text-muted"}`}>{essai.detail}</span>}
          </div>
        </div>
      )}
    </Card>
  );

  const destinations = (
    <Card>
      <Kicker>{t("onboarding.destKicker")}</Kicker>
      <Title>{t("onboarding.destTitle")}</Title>
      <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t("onboarding.destHint")}</p>
      {cibles.length === 0 && <p className="m-0 mb-3 text-[0.9rem] text-muted">{t("onboarding.destNone")}</p>}
      <div className="grid gap-2.5">
        {cibles.map((c, i) => (
          <div key={i} className="grid items-center gap-2 sm:grid-cols-[minmax(6rem,9rem)_1fr_auto_auto]">
            <input className={inputClass} value={c.label} placeholder={t("onboarding.destLabel")}
                   onChange={(e) => maj(i, "label", e.target.value)} aria-label={t("onboarding.destLabel")} />
            <input className={`${inputClass} font-mono text-[0.82rem]`} value={c.url} placeholder="rtmps://…"
                   onChange={(e) => maj(i, "url", e.target.value)} aria-label={t("onboarding.destUrl")} />
            <label className="flex items-center gap-1.5 text-[0.88rem] whitespace-nowrap">
              <input type="checkbox" checked={c.enabled} onChange={(e) => maj(i, "enabled", e.target.checked)} />
              {t("onboarding.destEnabled")}
            </label>
            <Btn variante="dangerGhost" onClick={() => setCibles((l) => l.filter((_, j) => j !== i))}>
              {t("onboarding.destRemove")}
            </Btn>
          </div>
        ))}
      </div>
      <div className="mt-3">
        <Btn variante="ghost" onClick={() => setCibles((l) => [...l, { label: "", url: "", enabled: true }])}>
          + {t("onboarding.destAdd")}
        </Btn>
      </div>
    </Card>
  );

  const rediffusions = (
    <Card>
      <Kicker>{t("onboarding.replayKicker")}</Kicker>
      <Title>{t("onboarding.replayTitle")}</Title>
      <p className="mt-0 mb-3 max-w-[80ch] text-[0.9rem] text-muted">{t("onboarding.replayHint")}</p>
      <div className="grid gap-2">
        <Field label={t("settings.replayEndpoint")}>
          <input className={`${inputClass} font-mono text-[0.85rem]`} value={depot.endpoint} spellCheck={false}
            placeholder="https://<compte>.r2.cloudflarestorage.com"
            onChange={(e) => { setDepot((d) => ({ ...d, endpoint: e.target.value })); setEssaiDepot(null); }} />
        </Field>
        <div className="grid gap-2 sm:grid-cols-[1fr_9rem]">
          <Field label={t("settings.replayBucket")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={depot.bucket} spellCheck={false}
              placeholder="minbar-rediffusions"
              onChange={(e) => { setDepot((d) => ({ ...d, bucket: e.target.value })); setEssaiDepot(null); }} />
          </Field>
          <Field label={t("settings.replayRegion")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={depot.region} spellCheck={false}
              placeholder="auto"
              onChange={(e) => { setDepot((d) => ({ ...d, region: e.target.value })); }} />
          </Field>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label={t("settings.replayAccessKey")}>
            <input className={`${inputClass} font-mono text-[0.85rem]`} value={depot.access_key}
              autoComplete="off" spellCheck={false}
              onChange={(e) => { setDepot((d) => ({ ...d, access_key: e.target.value })); setEssaiDepot(null); }} />
          </Field>
          <Field label={t("settings.replaySecretKey")}>
            <input className={inputClass} type="password" autoComplete="off" value={depot.secret_key}
              onChange={(e) => { setDepot((d) => ({ ...d, secret_key: e.target.value })); setEssaiDepot(null); }} />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-[0.9rem] font-semibold whitespace-nowrap">
            <input type="checkbox" checked={depot.enabled}
              onChange={(e) => { setDepot((d) => ({ ...d, enabled: e.target.checked })); }} />
            {t("settings.replayEnabled")}
          </label>
          <Btn type="button" variante="ghost" disabled={testDepot || !depotRempli} onClick={testerDepot}>
            {testDepot ? t("onboarding.testing") : t("settings.replayTest")}
          </Btn>
          {essaiDepot && <span className={`text-[0.88rem] ${essaiDepot.ok ? "text-green" : "text-muted"}`}>{essaiDepot.detail}</span>}
        </div>
      </div>
    </Card>
  );

  const fin = (
    <Card>
      <Kicker>{t("onboarding.doneKicker")}</Kicker>
      <Title>{t("onboarding.doneTitle")}</Title>
      <p className="mt-0 mb-3 max-w-[80ch] text-[0.92rem]">{t("onboarding.doneBody")}</p>
      <p className="m-0 mb-1 font-mono text-[0.85rem] text-muted">
        {t("onboarding.summary", { entree: mode === "push" ? obs : adresse, n: cibles.filter((c) => c.enabled).length })}
      </p>
      <p className="m-0 mb-4 font-mono text-[0.85rem] text-muted">
        {depot.enabled && depot.bucket
          ? t("onboarding.summaryReplays", { bucket: depot.bucket, prefix: depot.prefix })
          : t("onboarding.replaySkipNote")}
      </p>
      <Btn variante="leaf" onClick={terminer}>{t("onboarding.finish")}</Btn>
    </Card>
  );

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-1.5">
        {ETAPES.map((cle, i) => (
          <span key={cle} className={`rounded-full px-2.5 py-1 text-[0.82rem] font-semibold ${
            i === etape ? "bg-leaf text-night" : i < etape ? "bg-leaf/15 text-green" : "bg-paper-2 text-muted"}`}>
            {i + 1}. {t(`onboarding.step_${cle}`)}
          </span>
        ))}
      </div>

      {etape === 0 && entree}
      {etape === 1 && destinations}
      {etape === 2 && rediffusions}
      {etape === 3 && fin}

      {erreur && <p className="m-0 rounded-lg bg-err-bg px-3 py-2 text-[0.9rem] text-[#8a2219]">{erreur}</p>}

      {etape < 3 && (
        <div className="flex items-center gap-2">
          {etape > 0 && <Btn variante="ghost" onClick={() => { setEtape(etape - 1); setErreur(""); }}>← {t("onboarding.back")}</Btn>}
          <Btn onClick={suivant} disabled={envoi}>{envoi ? t("common.saving") : t("onboarding.next")}</Btn>
          {etape === 2 && (
            <span className="text-[0.88rem] text-muted">{t("onboarding.replaySkipNote")}</span>
          )}
          <button type="button" onClick={terminer} className="ml-2 cursor-pointer border-0 bg-transparent text-[0.88rem] text-muted underline underline-offset-4">
            {t("onboarding.skip")}
          </button>
        </div>
      )}
    </div>
  );
}
