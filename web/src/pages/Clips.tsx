import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Pill, Segmented, Title } from "../ui";

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

  return (
    <Card>
      <Title grand>{t("clips.title")}</Title>
      <Segmented items={pastilles} valeur={statut} onChange={setStatut} />
      {!data ? <p className="text-muted">{t("common.loading")}</p> : data.items.length === 0 ? (
        <p className="text-muted">{t("common.empty")}</p>
      ) : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("clips.sermon"), t("clips.kind"), t("clips.duration"), t("clips.status"), t("clips.why")].map((h) => (
                <th key={h} className="border-b border-line p-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((c: any) => (
              <tr key={c.id} className="hover:bg-paper-2/60">
                <td className="border-b border-line p-2.5 align-top font-semibold">{c.sermon}</td>
                <td className="border-b border-line p-2.5 align-top text-muted">{c.kind}</td>
                <td className="border-b border-line p-2.5 align-top whitespace-nowrap">{`${Math.floor((c.end - c.start) / 60)}:${String(Math.round((c.end - c.start) % 60)).padStart(2, "0")}`}</td>
                <td className="border-b border-line p-2.5 align-top"><Pill statut={c.status} label={libelles[c.status] ?? c.status} /></td>
                <td className="border-b border-line p-2.5 align-top text-[0.88rem] text-muted">{c.why ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
