import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, inputClass } from "../ui";

const TAILLE = 12;   // une page tient à l'écran avec sa pagination, sans défilement

/** Journal du back-office, page par page : filtre par action, libellés en clair, pagination.
 *  Le serveur compte le total filtré : le front ne devine pas la dernière page. */
export default function Audit() {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [action, setAction] = useState("");
  const [d, setD] = useState<{ items: any[]; total: number; labels: Record<string, string> } | null>(null);
  const [erreur, setErreur] = useState(false);

  useEffect(() => {
    setD(null);
    api.audit(page, TAILLE, action).then(setD).catch(() => setErreur(true));
  }, [page, action]);

  const pages = d ? Math.max(1, Math.ceil(d.total / TAILLE)) : 1;
  const libelles = d?.labels ?? {};

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("audit.title")}</h1>
        {d && (
          <p className="m-0 text-[0.88rem] text-muted">
            {t("audit.count", { n: d.total })}{action ? ` · ${t("audit.filtered")}` : ""}
          </p>
        )}
      </div>

      <div className="max-w-[340px]">
        <Field label={t("audit.filter")}>
          <select
            className={inputClass}
            value={action}
            onChange={(e) => {
              setPage(0);
              setAction(e.target.value);
            }}
          >
            <option value="">{t("audit.all")}</option>
            {Object.entries(libelles).map(([cle, label]) => (
              <option key={cle} value={cle}>{label}</option>
            ))}
          </select>
        </Field>
      </div>

      <Card>
        {erreur ? (
          <p className="m-0 text-muted">{t("common.error")}</p>
        ) : !d ? (
          <p className="m-0 text-muted">{t("common.loading")}</p>
        ) : d.items.length === 0 ? (
          <p className="m-0 text-muted">{t("common.empty")}</p>
        ) : (
          <table className="w-full border-collapse text-[0.93rem]">
            <thead>
              <tr>
                {[t("audit.when"), t("audit.who"), t("audit.action"), t("audit.object"), t("audit.ip")].map((h) => (
                  <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.72rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.items.map((a) => (
                <tr key={a.id} className="[&:last-child>td]:border-b-0">
                  <td className="whitespace-nowrap border-b border-line px-2 py-2 align-top tabular-nums text-muted">
                    {String(a.created_at).replace("T", " ").slice(0, 16)}
                  </td>
                  <td className="border-b border-line px-2 py-2 align-top">{a.who ?? "—"}</td>
                  <td className="border-b border-line px-2 py-2 align-top font-semibold">
                    {libelles[a.action] ?? a.action}
                    {a.details && a.details !== "{}" && (
                      <span className="block text-[0.82rem] font-normal text-muted">{a.details}</span>
                    )}
                  </td>
                  <td className="border-b border-line px-2 py-2 align-top text-muted">
                    {a.object_type
                      ? `${t(`audit.objects.${a.object_type}`, a.object_type)}${a.object_id ? ` #${a.object_id}` : ""}`
                      : ""}
                  </td>
                  <td className="whitespace-nowrap border-b border-line px-2 py-2 align-top tabular-nums text-muted">{a.ip ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {d && d.total > TAILLE && (
        <div className="flex items-center justify-between gap-3">
          <Btn variante="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>{t("audit.previous")}</Btn>
          <span className="text-[0.88rem] tabular-nums text-muted">{t("audit.page", { n: page + 1, total: pages })}</span>
          <Btn variante="ghost" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>{t("audit.next")}</Btn>
        </div>
      )}
    </div>
  );
}
