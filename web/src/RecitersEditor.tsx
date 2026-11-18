import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Btn, inputClass } from "./ui";

/** Compact editor of the tarawih reciters: the ordered roster (up, remove, add) and, below, the
 *  nights that have their own list. Values are plain arrays; the parent keeps them as JSON. */
export function RecitersEditor({ roster, nights, max, start, end, onChange }: {
  roster: string[];
  nights: Record<string, string[]>;
  max: number;
  start: string;
  end: string;
  onChange: (roster: string[], nights: Record<string, string[]>) => void;
}) {
  const { t } = useTranslation();
  const [nom, setNom] = useState("");
  const [nuit, setNuit] = useState("");
  const [noms, setNoms] = useState("");
  const petit = "min-h-7! px-2 py-0.5 text-[0.8rem]";

  const ajouter = () => {
    const n = nom.trim().replace(/\s+/g, " ");
    if (!n || roster.some((x) => x.toLowerCase() === n.toLowerCase()) || roster.length >= max) return;
    onChange([...roster, n], nights);
    setNom("");
  };
  const monter = (i: number) => {
    const r = [...roster];
    [r[i - 1], r[i]] = [r[i], r[i - 1]];
    onChange(r, nights);
  };
  const ajouterNuit = () => {
    const liste = noms.split(",").map((x) => x.trim()).filter(Boolean);
    if (!nuit || liste.length === 0) return;
    onChange(roster, { ...nights, [nuit]: liste });
    setNuit("");
    setNoms("");
  };
  const retirerNuit = (jour: string) => {
    const n = { ...nights };
    delete n[jour];
    onChange(roster, n);
  };

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="grid content-start gap-1.5">
        <span className="text-[0.9rem] font-semibold">{t("settings.tarawihReciters")}</span>
        <span className="text-[0.8rem] leading-snug text-muted">{t("settings.tarawihRecitersHelp")}</span>
        {roster.length > 0 && (
          <ol className="m-0 grid gap-1 p-0">
            {roster.map((n, i) => (
              <li key={n} className="flex items-center gap-1.5 rounded border border-line px-2 py-0.5 text-[0.88rem]">
                <span className="w-4 text-muted">{i + 1}.</span>
                <span className="min-w-0 flex-1 truncate">{n}</span>
                <Btn variante="ghost" className={petit} disabled={i === 0} aria-label={t("settings.tarawihUp", { name: n })}
                     onClick={() => monter(i)}>↑</Btn>
                <Btn variante="ghost" className={petit} aria-label={t("settings.tarawihRemove", { name: n })}
                     onClick={() => onChange(roster.filter((_, j) => j !== i), nights)}>✕</Btn>
              </li>
            ))}
          </ol>
        )}
        <div className="flex gap-1.5">
          <input className={`${inputClass} min-w-0 flex-1`} maxLength={80} value={nom} placeholder={t("settings.tarawihReciterName")}
                 aria-label={t("settings.tarawihReciterName")} disabled={roster.length >= max}
                 onChange={(e) => setNom(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); ajouter(); } }} />
          <Btn variante="ghost" disabled={!nom.trim() || roster.length >= max} onClick={ajouter}>{t("settings.tarawihAdd")}</Btn>
        </div>
      </div>
      <div className="grid content-start gap-1.5">
        <span className="text-[0.9rem] font-semibold">{t("settings.tarawihNights")}</span>
        <span className="text-[0.8rem] leading-snug text-muted">{t("settings.tarawihNightsHelp")}</span>
        {Object.keys(nights).sort().map((jour) => (
          <div key={jour} className="flex items-center gap-1.5 rounded border border-line px-2 py-0.5 text-[0.88rem]">
            <span className="text-muted">{jour}</span>
            <span className="min-w-0 flex-1 truncate">{nights[jour].join(", ")}</span>
            <Btn variante="ghost" className={petit} aria-label={t("settings.tarawihRemove", { name: jour })}
                 onClick={() => retirerNuit(jour)}>✕</Btn>
          </div>
        ))}
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-1.5 sm:grid-cols-[10rem_minmax(0,1fr)_auto]">
          <input className={`${inputClass} col-span-2 sm:col-span-1`} type="date" value={nuit} min={start || undefined} max={end || undefined}
                 aria-label={t("settings.tarawihNightDate")} disabled={!start || !end} onChange={(e) => setNuit(e.target.value)} />
          <input className={inputClass} value={noms} placeholder={t("settings.tarawihNightNames")}
                 aria-label={t("settings.tarawihNightNames")} disabled={!start || !end} onChange={(e) => setNoms(e.target.value)} />
          <Btn variante="ghost" disabled={!nuit || !noms.trim()} onClick={ajouterNuit}>{t("settings.tarawihAdd")}</Btn>
        </div>
      </div>
    </div>
  );
}
