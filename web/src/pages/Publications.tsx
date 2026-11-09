import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card } from "../ui";

export default function Publications() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);

  useEffect(() => { api.publications().then(setD).catch(() => setD({ connectors: [] })); }, []);

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("publications.title")}</h1>
      <Card>
      {!d ? <p className="text-muted">{t("common.loading")}</p> : d.connectors.length === 0 ? (
        <p className="text-muted">{t("common.empty")}</p>
      ) : (
        <table className="w-full border-collapse text-[0.95rem]">
          <thead>
            <tr>
              {[t("publications.connector"), t("publications.enabled")].map((h) => (
                <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.connectors.map((c: any) => (
              <tr key={c.key}>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 font-semibold">{c.label}</td>
                <td className="border-b border-line px-2 py-2.5 align-middle [tr:last-child_&]:border-b-0 text-muted">—</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </Card>
    </div>
  );
}
