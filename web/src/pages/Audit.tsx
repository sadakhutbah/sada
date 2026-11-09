import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card } from "../ui";

export default function Audit() {
  const { t } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);

  useEffect(() => { api.audit().then((r) => setItems(r.items)).catch(() => setItems([])); }, []);

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("audit.title")}</h1>
      <Card>
      {!items ? <p className="text-muted">{t("common.loading")}</p> : items.length === 0 ? (
        <p className="text-muted">{t("common.empty")}</p>
      ) : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("audit.when"), t("audit.who"), t("audit.action"), t("audit.object"), t("audit.ip")].map((h) => (
                <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.id}>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 whitespace-nowrap tabular-nums text-muted">{String(a.created_at).replace("T", " ").slice(0, 16)}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">{a.who ?? "—"}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 font-semibold">{a.action}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">{a.object_type ? `${a.object_type}${a.object_id ? ` #${a.object_id}` : ""}` : ""}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">{a.ip ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </Card>
    </div>
  );
}
