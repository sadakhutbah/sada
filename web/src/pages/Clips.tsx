import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Clips() {
  const { t } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [statut, setStatut] = useState("");
  useEffect(() => { api.clips(statut).then(setData).catch(() => setData({ items: [], status_labels: {} })); }, [statut]);

  return (
    <section className="carte">
      <h2>{t("clips.title")}</h2>
      <div className="segments">
        <button className={statut === "" ? "on" : ""} onClick={() => setStatut("")}>{t("clips.all")}</button>
        {data && Object.entries(data.status_labels).map(([code, label]) => (
          <button key={code} className={statut === code ? "on" : ""} onClick={() => setStatut(code)}>{String(label)}</button>
        ))}
      </div>
      {!data ? <p className="vide">{t("common.loading")}</p> : data.items.length === 0 ? <p className="muted">{t("common.empty")}</p> : (
        <table>
          <thead><tr><th>{t("clips.sermon")}</th><th>{t("clips.kind")}</th><th>{t("clips.duration")}</th><th>{t("clips.status")}</th></tr></thead>
          <tbody>
            {data.items.map((c: any) => (
              <tr key={c.id}>
                <td>{c.sermon}</td><td>{c.kind}</td>
                <td>{Math.round((c.end - c.start) / 60)} min</td>
                <td>{data.status_labels[c.status] ?? c.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
