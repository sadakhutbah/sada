import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Users() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.users().then(setD).catch(() => setD({ items: [], roles: {} })); }, []);
  if (!d) return <p className="vide">{t("common.loading")}</p>;
  return (
    <section className="carte">
      <h2>{t("users.title")}</h2>
      <table>
        <thead><tr><th>{t("users.user")}</th><th>{t("users.name")}</th><th>{t("users.role")}</th><th>{t("users.active")}</th></tr></thead>
        <tbody>
          {d.items.map((u: any) => (
            <tr key={u.id}>
              <td>{u.username}</td><td>{u.display_name}</td>
              <td>{d.roles[u.role] ?? u.role}</td><td>{u.active ? "✓" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
