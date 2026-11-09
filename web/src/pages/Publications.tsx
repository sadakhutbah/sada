import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Publications() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.publications().then(setD).catch(() => setD({ connectors: [] })); }, []);
  if (!d) return <p className="vide">{t("common.loading")}</p>;
  return (
    <section className="carte">
      <h2>{t("publications.title")}</h2>
      <table>
        <thead><tr><th>{t("publications.connector")}</th><th>{t("publications.enabled")}</th></tr></thead>
        <tbody>
          {d.connectors.map((c: any) => (
            <tr key={c.key}><td>{c.label}</td><td className="muted">—</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
