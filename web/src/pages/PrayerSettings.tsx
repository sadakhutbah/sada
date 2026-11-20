import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { State } from "../State";
import { Btn, Field, Tabs, inputClass } from "../ui";
import { idOnglet, idPanneau } from "../onglets";
import { Apercu, CalcFields, selectClass } from "../PrayerFields";
import { ADHANS, PRIERES, calcDepuis, calcVersServeur, moisCourant, type Calc } from "../prayerForm";

/** Settings › Prayer times. With Mawaqit there is nothing to enter; without, the mosque calculates
 *  (method, coordinates, offsets, a typed correction for any day) or types its own timetable
 *  (a month grid, or a CSV), and a public read-only page is provided. */

type Message = { ok: boolean; texte: string } | null;
type Jour = { date: string; set: Record<string, string>; calc?: Record<string, string> };
const SOURCES = ["mawaqit", "computed", "manual"] as const;
const CHAMPS = [...PRIERES, ...ADHANS.map((p) => `${p}_iqama`)];
const HEURE = /^\d{1,2}[:h.]\d{2}$/;

const decaler = (mois: string, n: number): string => {
  const [a, m] = mois.split("-").map(Number);
  const d = new Date(a, m - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
const plus = (hhmm: string, minutes: number): string => {
  const m = /^(\d{1,2})[:h.](\d{2})$/.exec(hhmm.trim());
  if (!m) return "";
  const total = (Number(m[1]) * 60 + Number(m[2]) + minutes) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
};

export default function PrayerSettings({ d, onSaved }: { d: any; onSaved: () => void }) {
  const { t, i18n } = useTranslation();
  const [source, setSource] = useState<string>(d.source || "");
  const [url, setUrl] = useState<string>(d.mawaqit_url || "");
  const [jumua, setJumua] = useState<string[]>(["", "", ""]);
  const [publie, setPublie] = useState<boolean>(d.public ?? true);
  const [calc, setCalc] = useState<Calc>(calcDepuis(d.calc));
  const [initial, setInitial] = useState("");
  const [sous, setSous] = useState("source");
  const [msg, setMsg] = useState<Message>(null);
  const [enCours, setEnCours] = useState(false);
  const [deduit, setDeduit] = useState<{ ok: boolean; texte: string } | null>(null);
  const [lecture, setLecture] = useState(false);
  const [copie, setCopie] = useState(false);

  const instantane = (s: string, u: string, j: string[], p: boolean, c: Calc) => JSON.stringify([s, u, j, p, c]);
  // Reloaded settings (after a save) replace the form, during render rather than in an effect.
  const [vu, setVu] = useState<any>(null);
  if (vu !== d) {
    const j = [...(d.jumua ?? []), "", "", ""].slice(0, 3);
    const c = calcDepuis(d.calc);
    setVu(d);
    setSource(d.source || ""); setUrl(d.mawaqit_url || ""); setJumua(j); setPublie(d.public ?? true); setCalc(c);
    setInitial(instantane(d.source || "", d.mawaqit_url || "", j, d.public ?? true, c));
  }

  const modifie = instantane(source, url, jumua, publie, calc) !== initial;
  const enregistre = d.source === source;  // the month grid and the CSV speak of the saved source
  const onglets = useMemo(() => {
    const base = [{ cle: "source", label: t("prayer.tabs.source") }];
    if (source === "computed") {
      return [...base, { cle: "calc", label: t("prayer.tabs.calc") }, { cle: "adjust", label: t("prayer.tabs.adjust") },
              { cle: "times", label: t("prayer.tabs.times"), eteint: !enregistre },
              { cle: "export", label: t("prayer.tabs.export"), eteint: !enregistre }];
    }
    if (source === "manual") {
      return [...base, { cle: "times", label: t("prayer.tabs.times"), eteint: !enregistre },
              { cle: "import", label: t("prayer.tabs.import"), eteint: !enregistre },
              { cle: "export", label: t("prayer.tabs.export"), eteint: !enregistre }];
    }
    return base;
  }, [source, enregistre, t]);
  const actif = onglets.some((o) => o.cle === sous) ? sous : "source";

  const enregistrer = async () => {
    setEnCours(true); setMsg(null);
    try {
      await api.prayerSave({ source, mawaqit_url: url, jumua: jumua.filter((x) => x.trim() !== ""), public: publie, calc: calcVersServeur(calc) });
      setMsg({ ok: true, texte: t("prayer.saved") });
      onSaved();
    } catch (e: any) {
      setMsg({ ok: false, texte: e.message });
    } finally { setEnCours(false); }
  };

  const deduire = async () => {
    if (!url.trim()) { setDeduit({ ok: false, texte: t("prayer.deduceNoUrl") }); return; }
    setLecture(true); setDeduit(null);
    try {
      const r = await api.prayerDeduce(url);
      setCalc(calcDepuis(r.calc));
      setDeduit({ ok: true, texte: t("prayer.deduced", { n: r.worst }) });
    } catch (e: any) {
      setDeduit({ ok: false, texte: e.message });
    } finally { setLecture(false); }
  };

  const adresse = `${window.location.origin}/api/v1/public/mosques/${d.slug}/prayer-times`;
  const pageAdresse = `${adresse}/page`;
  const copier = async () => {
    try { await navigator.clipboard.writeText(adresse); setCopie(true); setTimeout(() => setCopie(false), 2000); } catch { /* copie refusée */ }
  };
  const setOffset = (champ: "offsets" | "iqama", p: string, v: string) => setCalc({ ...calc, [champ]: { ...calc[champ], [p]: v } });
  const setLimite = (p: string, i: 0 | 1, v: string) => {
    const [a, b] = calc.bounds[p] ?? ["", ""];
    setCalc({ ...calc, bounds: { ...calc.bounds, [p]: i === 0 ? [v, b] : [a, v] } });
  };

  const panneauSource = (
    <div className="grid gap-3.5">
      <p className="m-0 max-w-[90ch] text-[0.9rem] text-muted">{t("prayer.intro")}</p>
      <div role="radiogroup" aria-label={t("settings.tabs.prayer")} className="grid gap-2.5 sm:grid-cols-3">
        {SOURCES.map((s) => (
          <button key={s} type="button" role="radio" aria-checked={source === s} onClick={() => { setSource(s); setMsg(null); }}
            className={`cursor-pointer rounded-[9px] border p-3 text-left ${source === s ? "border-leaf bg-leaf/10" : "border-line bg-white hover:bg-paper-2"}`}>
            <span className="block font-semibold">{t(`prayer.sources.${s}`)}</span>
            <span className="mt-0.5 block text-[0.85rem] text-muted">{t(`prayer.sourcesHint.${s}`)}</span>
          </button>
        ))}
      </div>
      {source === "mawaqit" && (
        <Field label={t("prayer.mawaqitUrl")}>
          <input className={inputClass} value={url} onChange={(e) => { setUrl(e.target.value); setMsg(null); }}
            placeholder={t("prayer.mawaqitPlaceholder")} inputMode="url" />
        </Field>
      )}
      {source !== "mawaqit" && (
        <fieldset className="m-0 grid gap-1.5 border-0 p-0">
          <legend className="mb-1 p-0 text-[0.9rem] font-semibold">{t("prayer.jumua")}</legend>
          <div className="grid grid-cols-3 gap-2.5 max-sm:grid-cols-1">
            {jumua.map((v, i) => (
              <input key={i} type="time" className={inputClass} value={v} aria-label={t("prayer.jumuaN", { n: i + 1 })}
                onChange={(e) => { setJumua(jumua.map((x, j) => (j === i ? e.target.value : x))); setMsg(null); }} />
            ))}
          </div>
        </fieldset>
      )}
      {source === "mawaqit" ? (
        <p className="m-0 text-[0.88rem] text-muted">{t("prayer.publicMawaqit")}</p>
      ) : source ? (
        <div className="grid gap-2">
          <label className="flex items-start gap-2.5">
            <input type="checkbox" className="mt-1" checked={publie} onChange={(e) => { setPublie(e.target.checked); setMsg(null); }} />
            <span><strong>{t("prayer.public")}</strong>
              <span className="block text-[0.82rem] leading-snug text-muted">{t("prayer.publicHelp")}</span></span>
          </label>
          {publie && (
            <div className="grid grid-cols-[1fr_auto] gap-2">
              <input className={`${inputClass} font-mono text-[0.82rem]`} value={adresse} readOnly aria-label={t("prayer.publicUrl")} />
              <Btn variante="ghost" onClick={copier}>{copie ? t("prayer.copied") : t("prayer.copy")}</Btn>
            </div>
          )}
          {publie && enregistre && (
            <a href={pageAdresse} target="_blank" rel="noopener" className="text-[0.9rem] text-deep underline underline-offset-4">{t("prayer.openPage")}</a>
          )}
        </div>
      ) : null}
    </div>
  );

  const panneauCalc = (
    <div className="grid gap-3.5">
      <CalcFields calc={calc} setCalc={(c) => { setCalc(c); setMsg(null); }} />
      <Apercu calc={calc} jours={2} />
      <div className="grid gap-2 rounded-[9px] border border-line bg-paper-2 p-3">
        <p className="m-0 text-[0.9rem] font-semibold">{t("prayer.deduceTitle")}</p>
        <p className="m-0 text-[0.82rem] text-muted">{t("prayer.deduceHelp")}</p>
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <input className={inputClass} value={url} onChange={(e) => setUrl(e.target.value)} aria-label={t("prayer.mawaqitUrl")}
            placeholder={t("prayer.mawaqitPlaceholder")} inputMode="url" />
          <Btn variante="ghost" onClick={deduire} disabled={lecture}>{lecture ? t("prayer.deducing") : t("prayer.deduce")}</Btn>
        </div>
        {deduit && <p role={deduit.ok ? "status" : "alert"} className={`m-0 text-[0.86rem] ${deduit.ok ? "text-deep" : "text-[#7a1f20]"}`}>{deduit.texte}</p>}
      </div>
    </div>
  );

  const cellule = "w-[4.4rem] rounded-md border border-[#cfc6b0] bg-white px-1.5 py-1 text-[0.9rem] tabular-nums";
  const panneauAjust = (
    <div className="grid gap-3">
      <p className="m-0 max-w-[90ch] text-[0.9rem] text-muted">{t("prayer.adjustHint")}</p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[0.92rem]">
          <thead>
            <tr>
              {["prayer", "offset", "iqamaDelay", "notBefore", "notAfter"].map((h) => (
                <th key={h} className="px-2 py-1.5 text-left text-[0.75rem] font-semibold uppercase tracking-[0.06em] text-muted">{t(`prayer.${h}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PRIERES.map((p) => (
              <tr key={p} className="border-t border-line">
                <th scope="row" className="px-2 py-1.5 text-left font-semibold">{t(`prayer.names.${p}`)}</th>
                <td className="px-2 py-1.5">
                  <input className={cellule} inputMode="numeric" value={calc.offsets[p] ?? ""} placeholder="0"
                    aria-label={`${t(`prayer.names.${p}`)} ${t("prayer.offset")}`} onChange={(e) => setOffset("offsets", p, e.target.value)} />
                </td>
                <td className="px-2 py-1.5">
                  {p !== "sunrise" && (
                    <input className={cellule} inputMode="numeric" value={calc.iqama[p] ?? ""} placeholder="—"
                      aria-label={`${t(`prayer.names.${p}`)} ${t("prayer.iqamaDelay")}`} onChange={(e) => setOffset("iqama", p, e.target.value)} />
                  )}
                </td>
                {([0, 1] as const).map((i) => (
                  <td key={i} className="px-2 py-1.5">
                    <input className={`${cellule} w-[5.2rem]`} value={calc.bounds[p]?.[i] ?? ""} placeholder="HH:MM"
                      aria-label={`${t(`prayer.names.${p}`)} ${t(i === 0 ? "prayer.notBefore" : "prayer.notAfter")}`}
                      onChange={(e) => setLimite(p, i, e.target.value)} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Apercu calc={calc} jours={2} />
    </div>
  );

  return (
    <div className="grid gap-3">
      <State state={d.state} />
      <Tabs items={onglets} base="reglages-prayer" label={t("settings.tabs.prayer")} valeur={actif}
        onChange={(c) => { setSous(c); setMsg(null); }} />
      <div role="tabpanel" id={idPanneau("reglages-prayer", actif)} aria-labelledby={idOnglet("reglages-prayer", actif)} className="grid gap-3.5">
        {actif === "source" && panneauSource}
        {actif === "calc" && panneauCalc}
        {actif === "adjust" && panneauAjust}
        {actif === "times" && enregistre && (source === "computed" || source === "manual") && (
          <Grille key={`${source}-${i18n.language}`} source={source} />
        )}
        {actif === "import" && enregistre && <Import />}
        {actif === "export" && enregistre && <Exporter pageUrl={d.public ? pageAdresse : ""} />}
      </div>
      {(actif === "source" || actif === "calc" || actif === "adjust") && (
        <div className="flex flex-wrap items-center gap-3">
          <Btn onClick={enregistrer} disabled={enCours || !modifie || !source}>{enCours ? t("prayer.saving") : t("prayer.save")}</Btn>
          {!msg && <span className="text-[0.88rem] text-muted">{modifie ? t("prayer.unsaved") : t("prayer.allSaved")}</span>}
          {msg && <span role={msg.ok ? "status" : "alert"} className={`text-[0.9rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</span>}
        </div>
      )}
    </div>
  );
}

/** One month: what is typed, over the calculation (grey) when the mosque calculates. */
function Grille({ source }: { source: string }) {
  const { t } = useTranslation();
  const [mois, setMois] = useState(moisCourant());
  const [jours, setJours] = useState<Jour[]>([]);
  const [valeurs, setValeurs] = useState<Record<string, Record<string, string>>>({});
  const [initial, setInitial] = useState("");
  const [iqama, setIqama] = useState(false);
  const [delais, setDelais] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Message>(null);
  const [enCours, setEnCours] = useState(false);
  const refs = useRef<Record<string, HTMLInputElement | null>>({});

  const colonnes = iqama ? CHAMPS : PRIERES;
  const charger = (m: string) =>
    api.prayerDays(m).then((r) => {
      const v: Record<string, Record<string, string>> = {};
      for (const j of r.days as Jour[]) v[j.date] = { ...j.set };
      setJours(r.days); setValeurs(v); setInitial(JSON.stringify(v));
    }).catch((e) => setMsg({ ok: false, texte: e.message }));
  useEffect(() => { charger(mois); }, [mois]);

  const modifie = JSON.stringify(valeurs) !== initial;
  const regler = (date: string, champ: string, v: string) => {
    setValeurs((x) => ({ ...x, [date]: { ...x[date], [champ]: v } })); setMsg(null);
  };
  const aller = (i: number, c: number) => refs.current[`${i}-${c}`]?.focus();
  const clavier = (e: React.KeyboardEvent, i: number, c: number) => {
    if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); aller(Math.min(i + 1, jours.length - 1), c); }
    else if (e.key === "ArrowUp") { e.preventDefault(); aller(Math.max(i - 1, 0), c); }
  };
  /** A paste from a spreadsheet (rows by lines, columns by tabs) fills the boxes from the one under the cursor. */
  const coller = (e: React.ClipboardEvent, i: number, c: number) => {
    const texte = e.clipboardData.getData("text");
    if (!/[\n\t]/.test(texte.trim())) return;
    e.preventDefault();
    const lignes = texte.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
    setValeurs((x) => {
      const y = { ...x };
      lignes.forEach((cellules, di) => cellules.forEach((cell, dc) => {
        const j = jours[i + di]; const champ = colonnes[c + dc];
        if (j && champ) y[j.date] = { ...y[j.date], [champ]: cell.trim() };
      }));
      return y;
    });
    setMsg(null);
  };
  const remplirIqama = () => {
    setValeurs((x) => {
      const y = { ...x };
      for (const j of jours) for (const p of ADHANS) {
        const n = Number(delais[p]);
        const adhan = (y[j.date]?.[p] ?? "") || (j.calc?.[p] ?? "");
        if (n > 0 && adhan) y[j.date] = { ...y[j.date], [`${p}_iqama`]: plus(adhan, n) };
      }
      return y;
    });
    setIqama(true); setMsg({ ok: true, texte: t("prayer.iqamaFilled") });
  };
  const enregistrer = async () => {
    setEnCours(true); setMsg(null);
    try {
      const envoi = jours.map((j) => ({ date: j.date, ...valeurs[j.date] })).filter((j) => CHAMPS.some((c) => ((j as any)[c] ?? "").trim() !== ""));
      const r = await api.prayerDaysSave(mois, envoi);
      const v: Record<string, Record<string, string>> = {};
      for (const j of r.days as Jour[]) v[j.date] = { ...j.set };
      setJours(r.days); setValeurs(v); setInitial(JSON.stringify(v));
      setMsg({ ok: true, texte: t("prayer.monthSaved") });
    } catch (e: any) { setMsg({ ok: false, texte: e.message }); } finally { setEnCours(false); }
  };
  const invalide = (v: string) => v.trim() !== "" && !HEURE.test(v.trim());

  return (
    <div className="grid gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Btn variante="ghost" onClick={() => { setMsg(null); setMois(decaler(mois, -1)); }} aria-label={t("prayer.prev")}>←</Btn>
        <input type="month" className={`${inputClass} max-w-[11rem]`} value={mois} aria-label={t("prayer.month")}
          onChange={(e) => { if (e.target.value) { setMsg(null); setMois(e.target.value); } }} />
        <Btn variante="ghost" onClick={() => { setMsg(null); setMois(decaler(mois, 1)); }} aria-label={t("prayer.next")}>→</Btn>
        <label className="ml-2 flex items-center gap-1.5 text-[0.88rem]">
          <input type="checkbox" checked={iqama} onChange={(e) => setIqama(e.target.checked)} /> {t("prayer.showIqama")}
        </label>
      </div>
      {source === "manual" && (
        <div className="flex flex-wrap items-center gap-2 text-[0.85rem]">
          <span className="font-semibold">{t("prayer.iqamaFill")}</span>
          {ADHANS.map((p) => (
            <input key={p} className={`${cellule2} w-[3.6rem]`} inputMode="numeric" placeholder={t(`prayer.names.${p}`)}
              aria-label={`${t(`prayer.names.${p}`)} ${t("prayer.iqamaDelay")}`} value={delais[p] ?? ""}
              onChange={(e) => setDelais({ ...delais, [p]: e.target.value })} />
          ))}
          <Btn variante="ghost" onClick={remplirIqama}>{t("prayer.fill")}</Btn>
        </div>
      )}
      <div className="max-h-[320px] overflow-auto rounded-[9px] border border-line">
        <table className="w-full border-collapse text-[0.9rem] tabular-nums">
          <thead className="sticky top-0 z-10 bg-paper-2">
            <tr>
              <th className="px-2 py-1.5 text-left text-[0.75rem] font-semibold uppercase tracking-[0.06em] text-muted">{t("prayer.date")}</th>
              {colonnes.map((c) => (
                <th key={c} className="px-1 py-1.5 text-left text-[0.72rem] font-semibold uppercase tracking-[0.04em] text-muted">
                  {t(`prayer.names.${c.replace("_iqama", "")}`)}{c.endsWith("_iqama") ? " ⏱" : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {jours.map((j, i) => (
              <tr key={j.date} className="border-t border-line">
                <td className="whitespace-nowrap px-2 py-0.5 text-muted">{j.date.slice(8)} <span className="text-[0.75rem]">{new Date(j.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" })}</span></td>
                {colonnes.map((c, k) => {
                  const v = valeurs[j.date]?.[c] ?? "";
                  return (
                    <td key={c} className="px-0.5 py-0.5">
                      <input ref={(el) => { refs.current[`${i}-${k}`] = el; }}
                        className={`${cellule2} w-[4.3rem] ${invalide(v) ? "border-live bg-err-bg" : ""}`}
                        value={v} placeholder={j.calc?.[c] ?? ""} inputMode="numeric" maxLength={5}
                        aria-label={`${j.date} ${t(`prayer.names.${c.replace("_iqama", "")}`)}${c.endsWith("_iqama") ? " ⏱" : ""}`}
                        onChange={(e) => regler(j.date, c, e.target.value)}
                        onKeyDown={(e) => clavier(e, i, k)} onPaste={(e) => coller(e, i, k)} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="m-0 text-[0.8rem] text-muted">{source === "computed" ? t("prayer.calcHint") : t("prayer.pasteHint")}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Btn onClick={enregistrer} disabled={enCours || !modifie}>{enCours ? t("prayer.saving") : t("prayer.saveMonth")}</Btn>
        {!msg && <span className="text-[0.88rem] text-muted">{modifie ? t("prayer.monthDirty") : t("prayer.monthClean")}</span>}
        {msg && <span role={msg.ok ? "status" : "alert"} className={`text-[0.9rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</span>}
      </div>
    </div>
  );
}

const cellule2 = "rounded-md border border-[#cfc6b0] bg-white px-1.5 py-1 text-[0.9rem] tabular-nums placeholder:text-muted/60";

/** CSV in, CSV out: the quick way to enter a whole year. */
function Import() {
  const { t } = useTranslation();
  const [csv, setCsv] = useState("");
  const [msg, setMsg] = useState<Message>(null);
  const [enCours, setEnCours] = useState(false);
  const fichier = useRef<HTMLInputElement>(null);
  const [annee] = useState(() => new Date().getFullYear());
  const lire = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) { setCsv(await f.text()); setMsg(null); }
  };
  const importer = async () => {
    setEnCours(true); setMsg(null);
    try {
      const r = await api.prayerImport(csv);
      setMsg({ ok: true, texte: t("prayer.imported", { n: r.imported }) });
      setCsv("");
    } catch (e: any) { setMsg({ ok: false, texte: e.message }); } finally { setEnCours(false); }
  };
  return (
    <div className="grid gap-3">
      <p className="m-0 max-w-[90ch] text-[0.88rem] text-muted">{t("prayer.csvHelp")}</p>
      <textarea className={`${inputClass} h-44 font-mono text-[0.82rem]`} value={csv} onChange={(e) => { setCsv(e.target.value); setMsg(null); }}
        aria-label={t("prayer.csv")} placeholder={t("prayer.csvPlaceholder")} spellCheck={false} />
      <div className="flex flex-wrap items-center gap-2">
        <input ref={fichier} type="file" accept=".csv,.txt,text/csv,text/plain" className="hidden" onChange={lire} />
        <Btn variante="ghost" onClick={() => fichier.current?.click()}>{t("prayer.chooseFile")}</Btn>
        <Btn onClick={importer} disabled={enCours || csv.trim() === ""}>{enCours ? t("prayer.importing") : t("prayer.importBtn")}</Btn>
        <a href={api.prayerExportUrl(annee)} download className="text-[0.9rem] text-deep underline underline-offset-4">{t("prayer.exportYear")}</a>
      </div>
      {msg && <p role={msg.ok ? "status" : "alert"} className={`m-0 text-[0.9rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</p>}
    </div>
  );
}

/** PDF calendar or CSV of a month, a period, Ramadan or the year. */
function Exporter({ pageUrl }: { pageUrl: string }) {
  const { t } = useTranslation();
  const [periode, setPeriode] = useState<"month" | "range" | "ramadan" | "year">("month");
  const [mois, setMois] = useState(moisCourant);
  const [debut, setDebut] = useState(() => `${moisCourant()}-01`);
  const [fin, setFin] = useState(() => `${moisCourant()}-01`);
  const [enCours, setEnCours] = useState("");
  const [msg, setMsg] = useState<Message>(null);
  const requete = (format: "pdf" | "csv"): Record<string, string> => {
    if (periode === "month") return { format, month: mois };
    if (periode === "range") return { format, start: debut, end: fin };
    if (periode === "ramadan") return { format, ramadan: "true" };
    return { format, year: String(new Date().getFullYear()) };
  };
  const telecharger = async (format: "pdf" | "csv") => {
    setEnCours(format); setMsg(null);
    try {
      const { blob, nom } = await api.prayerExport(requete(format));
      const lien = document.createElement("a");
      lien.href = URL.createObjectURL(blob);
      lien.download = nom;
      document.body.appendChild(lien);
      lien.click();
      lien.remove();
      setTimeout(() => URL.revokeObjectURL(lien.href), 10000);
      setMsg({ ok: true, texte: t("prayer.exported", { nom }) });
    } catch (e: any) { setMsg({ ok: false, texte: e.message }); } finally { setEnCours(""); }
  };
  const incomplet = (periode === "month" && !mois) || (periode === "range" && (!debut || !fin));
  return (
    <div className="grid gap-3">
      <p className="m-0 max-w-[90ch] text-[0.88rem] text-muted">{t("prayer.exportHelp")}</p>
      <div className="grid max-w-[34rem] gap-2.5 sm:grid-cols-2">
        <Field label={t("prayer.exportPeriod")}>
          <select className={selectClass} value={periode} onChange={(e) => { setPeriode(e.target.value as typeof periode); setMsg(null); }}>
            {(["month", "range", "ramadan", "year"] as const).map((p) => <option key={p} value={p}>{t(`prayer.periods.${p}`)}</option>)}
          </select>
        </Field>
        {periode === "month" && (
          <Field label={t("prayer.periods.month")}>
            <input type="month" className={inputClass} value={mois} onChange={(e) => { setMois(e.target.value); setMsg(null); }} />
          </Field>
        )}
        {periode === "range" && (
          <>
            <Field label={t("prayer.exportFrom")}>
              <input type="date" className={inputClass} value={debut} onChange={(e) => { setDebut(e.target.value); setMsg(null); }} />
            </Field>
            <Field label={t("prayer.exportTo")}>
              <input type="date" className={inputClass} value={fin} onChange={(e) => { setFin(e.target.value); setMsg(null); }} />
            </Field>
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Btn onClick={() => telecharger("pdf")} disabled={enCours !== "" || incomplet}>{enCours === "pdf" ? t("prayer.exporting") : t("prayer.exportPdf")}</Btn>
        <Btn variante="ghost" onClick={() => telecharger("csv")} disabled={enCours !== "" || incomplet}>{enCours === "csv" ? t("prayer.exporting") : t("prayer.exportCsv")}</Btn>
        {pageUrl && <a href={pageUrl} target="_blank" rel="noopener" className="text-[0.9rem] text-deep underline underline-offset-4">{t("prayer.openPage")}</a>}
      </div>
      {msg && <p role={msg.ok ? "status" : "alert"} className={`m-0 text-[0.9rem] ${msg.ok ? "text-deep" : "text-[#7a1f20]"}`}>{msg.texte}</p>}
    </div>
  );
}
