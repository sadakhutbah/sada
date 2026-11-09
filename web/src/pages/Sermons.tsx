import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Sermons() {
  const { t } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);
  useEffect(() => { api.sermons().then((r) => setItems(r.items)).catch(() => setItems([])); }, []);
  if (!items) return <p className="vide">{t("common.loading")}</p>;
  return (
    <section className="carte">
      <h2>{t("sermons.title")}</h2>
      {items.length === 0 ? <p className="muted">{t("common.empty")}</p> : (
        <table>
          <thead><tr>
            <th>{t("sermons.date")}</th><th>{t("sermons.label")}</th><th>{t("sermons.imam")}</th>
            <th>{t("sermons.clips")}</th><th>{t("sermons.pending")}</th>
          </tr></thead>
          <tbody>
            {items.map((k) => (
              <tr key={k.id}>
                <td>{k.date}</td><td>{k.label}</td><td className="muted">{k.imam ?? "—"}</td>
                <td>{k.clips}</td><td>{k.pending || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
