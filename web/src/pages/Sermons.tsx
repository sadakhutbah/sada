import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Title } from "../ui";

export default function Sermons() {
  const { t } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);

  useEffect(() => {
    api.sermons().then((r) => setItems(r.items)).catch(() => setItems([]));
  }, []);

  if (!items) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  return (
    <Card>
      <Title grand>{t("sermons.title")}</Title>
      {items.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("sermons.date"), t("sermons.label"), t("sermons.imam"), t("sermons.theme"), t("sermons.clips"), t("sermons.pending")].map((h) => (
                <th key={h} className="border-b border-line p-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((k) => (
              <tr key={k.id} className="hover:bg-paper-2/60">
                <td className="border-b border-line p-2.5 align-top whitespace-nowrap">{k.date}</td>
                <td className="border-b border-line p-2.5 align-top font-semibold">{k.label}</td>
                <td className="border-b border-line p-2.5 align-top text-muted">{k.imam ?? "—"}</td>
                <td className="border-b border-line p-2.5 align-top">{k.theme ?? ""}</td>
                <td className="border-b border-line p-2.5 align-top">{k.clips}</td>
                <td className="border-b border-line p-2.5 align-top">{k.pending || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
