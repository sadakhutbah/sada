import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Pill, Segmented, tdClass, thClass } from "../ui";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

export default function Clips() {
  const { t } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [statut, setStatut] = useState("");

  useEffect(() => {
    api.clips(statut).then(setData).catch(() => setData({ items: [], status_labels: {} }));
  }, [statut]);

  const libelles = data?.status_labels ?? {};
  const pastilles = [
    { cle: "", label: t("clips.all") },
    ...Object.entries(libelles).map(([cle, label]) => ({ cle, label: String(label) })),
  ];
  const dernier = (data?.items?.length ?? 0) - 1;

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("clips.title")}</h1>
      <Card>
      <Segmented items={pastilles} valeur={statut} onChange={setStatut} />
      {!data ? <p className="text-muted">{t("common.loading")}</p> : data.items.length === 0 ? (
        <p className="text-muted">{t("common.empty")}</p>
      ) : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("clips.kind"), t("clips.sermon"), t("clips.duration"), t("clips.status")].map((h, i) => (
                <th key={h} className={`${thClass} ${i === 2 ? "text-right" : ""}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((c: any, i: number) => (
              <tr key={c.id} className="hover:bg-paper-2/60">
                {/* Le type porte la raison dessous : deux colonnes qui disaient la même chose n'en font qu'une. */}
                <td className={`${tdClass} ${i === dernier ? "border-b-0" : ""}`}>
                  <span className="font-semibold">{t(`clips.kinds.${c.kind}`)}</span>
                  {c.why && <span className="block text-[0.85rem] text-muted">{c.why}</span>}
                </td>
                <td className={`${tdClass} ${i === dernier ? "border-b-0" : ""}`}>
                  <span className="tabular-nums">{c.date}</span>
                  <span className="block text-[0.85rem] text-muted">{c.sermon}</span>
                </td>
                <td className={`${tdClass} text-right tabular-nums ${i === dernier ? "border-b-0" : ""}`}>
                  {mmss(c.start)}–{mmss(c.end)}
                </td>
                <td className={`${tdClass} ${i === dernier ? "border-b-0" : ""}`}>
                  <Pill statut={c.status} label={libelles[c.status] ?? c.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </Card>
    </div>
  );
}
