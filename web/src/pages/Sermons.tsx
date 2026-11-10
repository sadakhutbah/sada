import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card } from "../ui";

export default function Sermons() {
  const { t } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);

  useEffect(() => {
    api.sermons().then((r) => setItems(r.items)).catch(() => setItems([]));
  }, []);

  if (!items) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("sermons.title")}</h1>
      <Card>
      {items.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("sermons.date"), t("sermons.label"), t("sermons.imam"), t("sermons.theme"), t("sermons.clips"), t("sermons.pending")].map((h) => (
                <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((k) => (
              // La ligne entière ouvre la fiche : une liste qui ne mène nulle part ne sert à rien.
              <tr key={k.id} className="cursor-pointer hover:bg-paper-2/60">
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 whitespace-nowrap">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline">{k.date}</Link>
                </td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 font-semibold">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline">{k.label}</Link>
                </td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline">{k.imam ?? "—"}</Link>
                </td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline">{k.theme ?? ""}</Link>
                </td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline tabular-nums">{k.clips}</Link>
                </td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0">
                  <Link to={`/khutbas/${k.id}`} className="block text-inherit no-underline tabular-nums">{k.pending || "—"}</Link>
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
