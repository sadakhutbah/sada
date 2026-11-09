import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card } from "../ui";

export default function Users() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);

  useEffect(() => { api.users().then(setD).catch(() => setD({ items: [], roles: {} })); }, []);

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("users.title")}</h1>
      <Card>
      {!d ? <p className="text-muted">{t("common.loading")}</p> : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("users.name"), t("users.user"), t("users.role"), t("users.active"), t("users.last")].map((h) => (
                <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.items.map((u: any) => (
              <tr key={u.id}>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 font-semibold">{u.display_name}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">{u.username}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">{d.roles[u.role] ?? u.role}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">{u.active ? "✓" : ""}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">{u.last_login_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </Card>
    </div>
  );
}
