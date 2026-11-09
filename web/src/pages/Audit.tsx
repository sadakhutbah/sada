import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Audit() {
  const { t } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);
  useEffect(() => { api.audit().then((r) => setItems(r.items)).catch(() => setItems([])); }, []);
  if (!items) return <p className="vide">{t("common.loading")}</p>;
  return (
    <section className="carte">
      <h2>{t("audit.title")}</h2>
      <table>
        <thead><tr><th>{t("audit.when")}</th><th>{t("audit.who")}</th><th>{t("audit.action")}</th><th>{t("audit.ip")}</th></tr></thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td>{String(a.created_at).replace("T", " ").slice(0, 16)}</td>
              <td className="muted">{a.who ?? "—"}</td><td>{a.action}</td><td className="muted">{a.ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
