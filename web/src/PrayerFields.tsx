import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import { Btn, Field, inputClass } from "./ui";
import { METHODES, PRIERES, calcVersServeur, moisCourant, type Calc } from "./prayerForm";

/** Shared by Settings › Prayer times and the first-run wizard: the same fields, the same preview. */

// A select is "read-only" for CSS: without this its background went beige, like a disabled field.
export const selectClass = `${inputClass} h-11 cursor-pointer select-chevron read-only:bg-white! read-only:text-ink! read-only:cursor-pointer!`;
const ECOLES = ["standard", "hanafi"] as const;
const LATITUDES = ["nightmiddle", "oneseventh", "anglebased", "none"] as const;

/** Calculation fields: method, school, high latitudes, coordinates (and the angles of a custom method). */
export function CalcFields({ calc, setCalc }: { calc: Calc; setCalc: (c: Calc) => void }) {
  const { t } = useTranslation();
  const [geo, setGeo] = useState("");
  const maj = (cle: keyof Calc) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setCalc({ ...calc, [cle]: e.target.value });
  const position = () => {
    setGeo("");
    if (!navigator.geolocation) { setGeo(t("prayer.geoFail")); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => setCalc({ ...calc, lat: p.coords.latitude.toFixed(4), lon: p.coords.longitude.toFixed(4) }),
      () => setGeo(t("prayer.geoFail")),
      { timeout: 8000 },
    );
  };
  return (
    <div className="grid grid-cols-2 gap-3">
      <Field label={t("prayer.method")} className="col-span-2 sm:col-span-1">
        <select className={selectClass} value={calc.method} onChange={maj("method")}>
          {METHODES.map((m) => <option key={m} value={m}>{t(`prayer.methods.${m}`)}</option>)}
        </select>
      </Field>
      <Field label={t("prayer.asr")}>
        <select className={selectClass} value={calc.asr} onChange={maj("asr")}>
          {ECOLES.map((m) => <option key={m} value={m}>{t(`prayer.asrOptions.${m}`)}</option>)}
        </select>
      </Field>
      <Field label={t("prayer.highLats")}>
        <select className={selectClass} value={calc.high_lats} onChange={maj("high_lats")}>
          {LATITUDES.map((m) => <option key={m} value={m}>{t(`prayer.highLatsOptions.${m}`)}</option>)}
        </select>
      </Field>
      <div className="col-span-2 grid grid-cols-[1fr_1fr_auto] items-end gap-2 sm:col-span-1">
        <Field label={t("prayer.lat")}>
          <input className={inputClass} value={calc.lat} onChange={maj("lat")} inputMode="decimal" placeholder="48.85" />
        </Field>
        <Field label={t("prayer.lon")}>
          <input className={inputClass} value={calc.lon} onChange={maj("lon")} inputMode="decimal" placeholder="2.35" />
        </Field>
        <Btn variante="ghost" onClick={position}>{t("prayer.myPosition")}</Btn>
      </div>
      {geo && <p role="status" className="col-span-2 m-0 text-[0.85rem] text-muted">{geo}</p>}
      {calc.method === "custom" && (
        <div className="col-span-2 grid gap-3 sm:grid-cols-3">
          <Field label={t("prayer.fajrAngle")}>
            <input className={inputClass} value={calc.fajr_angle} onChange={maj("fajr_angle")} inputMode="decimal" placeholder="18" />
          </Field>
          <Field label={t("prayer.ishaAngle")}>
            <input className={inputClass} value={calc.isha_angle} onChange={maj("isha_angle")} inputMode="decimal" placeholder="17" />
          </Field>
          <Field label={t("prayer.ishaDelay")}>
            <input className={inputClass} value={calc.isha_delay_min} onChange={maj("isha_delay_min")} inputMode="numeric" placeholder="90" />
          </Field>
        </div>
      )}
    </div>
  );
}

/** The calculated times for the settings as typed (nothing saved), a few days from today. */
export function Apercu({ calc, jours = 3 }: { calc: Calc; jours?: number }) {
  const { t } = useTranslation();
  const [lignes, setLignes] = useState<any[]>([]);
  const [erreur, setErreur] = useState("");
  const pret = calc.lat.trim() !== "" && calc.lon.trim() !== "";
  const cle = JSON.stringify(calcVersServeur(calc));
  useEffect(() => {
    if (!pret) return;
    let annule = false;
    const minuteur = setTimeout(() => {
      api.prayerPreview(JSON.parse(cle), moisCourant())
        .then((r) => {
          if (annule) return;
          const aujourdhui = new Date();
          const jour = `${moisCourant()}-${String(aujourdhui.getDate()).padStart(2, "0")}`;
          setLignes(r.days.filter((d: any) => d.date >= jour).slice(0, jours)); setErreur("");
        })
        .catch((e) => { if (!annule) { setLignes([]); setErreur(e.message); } });
    }, 350);
    return () => { annule = true; clearTimeout(minuteur); };
  }, [cle, pret, jours]);
  return (
    <div className="grid gap-1.5" aria-live="polite">
      <p className="m-0 text-[0.9rem] font-semibold">{t("prayer.preview")}</p>
      {!pret && <p className="m-0 text-[0.88rem] text-muted">{t("prayer.coordsNeeded")}</p>}
      {pret && erreur && <p role="alert" className="m-0 text-[0.88rem] text-[#7a1f20]">{t("prayer.previewError", { message: erreur })}</p>}
      {pret && !erreur && lignes.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[0.9rem] tabular-nums">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left text-[0.75rem] font-semibold uppercase tracking-[0.06em] text-muted">{t("prayer.date")}</th>
                {PRIERES.map((p) => (
                  <th key={p} className="px-2 py-1 text-left text-[0.75rem] font-semibold uppercase tracking-[0.06em] text-muted">{t(`prayer.names.${p}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lignes.map((d) => (
                <tr key={d.date} className="border-t border-line">
                  <td className="whitespace-nowrap px-2 py-1 text-muted">{d.date.slice(5)}</td>
                  {PRIERES.map((p) => <td key={p} className="px-2 py-1">{d[p] || "—"}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
