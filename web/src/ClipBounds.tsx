import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Btn, Field, inputClass } from "./ui";
import { ecrireInstant, lireInstant } from "./instant";

/** Clip chosen by hand: bounds (in the khutba's time), type and title.
 *  Used to create a clip on the khutba sheet and to move the bounds of a clip (proposed by the model
 *  or not). "Start here / End here" take the current position of the player. The server checks
 *  everything again (bounds, duration, recording); here we only spare a pointless round trip. */

export type ValeursClip = { start: number; end: number; title: string; kind: string };

export default function FormulaireClip({ mode, initial, kinds, min, max, duree, ici, ecouter, occupe, onSubmit }: {
  mode: "creer" | "ajuster";
  initial?: ValeursClip;
  kinds: string[];
  min: number;
  max: number;
  duree?: number | null;
  ici: (() => number) | null;
  ecouter?: (s: number) => void;
  occupe?: boolean;
  onSubmit: (v: ValeursClip) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [debut, setDebut] = useState(initial ? ecrireInstant(initial.start) : "");
  const [fin, setFin] = useState(initial ? ecrireInstant(initial.end) : "");
  const [titre, setTitre] = useState(initial?.title ?? "");
  const [genre, setGenre] = useState(initial?.kind ?? kinds[0] ?? "extrait");
  const a = lireInstant(debut), b = lireInstant(fin);
  const lisible = !Number.isNaN(a) && !Number.isNaN(b);
  const longueur = lisible ? b - a : NaN;
  let probleme = "";
  if (debut && fin && !lisible) probleme = t("clipForm.unreadable");
  else if (lisible && b <= a) probleme = t("clipForm.endBeforeStart");
  else if (lisible && (longueur < min || longueur > max)) probleme = t("clipForm.badLength", { min, max });
  else if (lisible && duree && b > duree + 0.5) probleme = t("clipForm.pastEnd");
  const inchange = !!initial && lisible && a === initial.start && b === initial.end && titre.trim() === initial.title;
  const pret = lisible && !probleme && !inchange;

  const instant = (valeur: string, poser: (v: string) => void, label: string, ciLabel: string) => (
    <Field label={label}>
      <div className="flex min-w-0 items-center gap-1.5">
        <input aria-label={label} placeholder="0:00.0" inputMode="decimal"
               className={`${inputClass} tabular-nums`} value={valeur} onChange={(e) => poser(e.target.value)} />
        {ici && (
          <Btn variante="ghost" className="min-h-10 shrink-0 px-2.5 py-1 text-[0.85rem]" onClick={() => poser(ecrireInstant(ici()))}>
            {ciLabel}
          </Btn>
        )}
      </div>
    </Field>
  );

  return (
    <form
      className="grid gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (pret) onSubmit({ start: a, end: b, title: titre.trim(), kind: genre });
      }}
    >
      <p className="m-0 text-[0.88rem] text-muted">{t(mode === "creer" ? "clipForm.createHelp" : "clipForm.adjustHelp")}</p>
      <div className="grid grid-cols-2 gap-2.5 max-sm:grid-cols-1">
        {instant(debut, setDebut, t("clipForm.start"), t("clipForm.startHere"))}
        {instant(fin, setFin, t("clipForm.end"), t("clipForm.endHere"))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[0.85rem]">
        <span className={probleme ? "text-[#7a1f20]" : "text-muted tabular-nums"}>
          {probleme || (lisible ? t("clipForm.length", { s: longueur.toFixed(1) }) : t("clipForm.limits", { min, max }))}
        </span>
        {ecouter && lisible && !probleme && (
          <Btn variante="ghost" className="min-h-9 px-2.5 py-1 text-[0.82rem]" onClick={() => ecouter(a)}>
            {t("clipForm.listen")}
          </Btn>
        )}
      </div>
      {mode === "creer" && (
        <Field label={t("clipForm.kind")}>
          <select className={inputClass} value={genre} onChange={(e) => setGenre(e.target.value)}>
            {kinds.map((k) => <option key={k} value={k}>{t(`clips.kinds.${k}`)}</option>)}
          </select>
        </Field>
      )}
      <Field label={t("clipForm.title")} aide={t("clipForm.titleHelp")}>
        <input className={inputClass} dir="auto" maxLength={120} value={titre} onChange={(e) => setTitre(e.target.value)} />
      </Field>
      <div>
        <Btn type="submit" disabled={occupe || !pret}>
          {t(mode === "creer" ? "clipForm.create" : "clipForm.save")}
        </Btn>
      </div>
    </form>
  );
}
