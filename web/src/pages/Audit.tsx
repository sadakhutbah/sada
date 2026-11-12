import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { auditActionKey } from "../i18n";
import { liste } from "../format";
import { Btn, Card, Field, inputClass } from "../ui";

const TAILLE = 8;   // one page fits on screen with its pagination, without scrolling

/** Back-office log, page by page: filter by action, plain labels, pagination.
 *  The server counts the filtered total: the front end does not guess the last page. */
export default function Audit() {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [action, setAction] = useState("");
  const [d, setD] = useState<{ items: any[]; total: number; actions: string[] } | null>(null);
  const [erreur, setErreur] = useState(false);

  // New page or filter: the previous result is dropped at once (no stale rows while loading).
  const cle = `${page}|${action}`;
  const [cleVue, setCleVue] = useState(cle);
  if (cleVue !== cle) { setCleVue(cle); setD(null); }

  useEffect(() => {
    api.audit(page, TAILLE, action).then(setD).catch(() => setErreur(true));
  }, [page, action]);

  const pages = d ? Math.max(1, Math.ceil(d.total / TAILLE)) : 1;
  /** Readable action name: the server sends the stable key, the label lives in i18n. */
  const nomAction = (cle: string) => t(auditActionKey(cle), { defaultValue: cle });
  /** Readable details: the server attaches a JSON object (command, value, uid…) — only its values
   *  are shown, and protocol keys (command, choice) are translated like the rest. */
  const details = (brut?: string | null) => {
    if (!brut || brut === "{}") return "";
    try {
      const o = JSON.parse(brut);
      if (o && typeof o === "object" && !Array.isArray(o)) {
        return Object.entries(o)
          .filter(([cle, valeur]) => cle !== "uid" && valeur !== null && valeur !== "")
          .map(([cle, valeur]) => {
            if (cle === "commande") return t(`dashboard.cmd.${valeur}`, { defaultValue: String(valeur) });
            if (cle === "valeur") return t(`dashboard.choice.${valeur}`, { defaultValue: String(valeur) });
            return String(valeur);
          })
          .filter(Boolean)
          .join(" · ");
      }
    } catch { /* free text: displayed as-is */ }
    return brut;
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("audit.title")}</h1>
        {d && (
          <p className="m-0 text-[0.88rem] text-muted">
            {t("audit.count", { count: d.total })}{action ? ` · ${t("audit.filtered")}` : ""}
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
            {liste<string>(d?.actions).map((cle: string) => (
              <option key={cle} value={cle}>{nomAction(cle)}</option>
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
          // Téléphone : le tableau défile dans la carte (l'adresse IP, peu utile, est masquée).
          <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[0.93rem] max-sm:[&_tr>*:nth-child(5)]:hidden">
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
                    {nomAction(a.action)}
                    {details(a.details) && (
                      <span className="block text-[0.82rem] font-normal text-muted">{details(a.details)}</span>
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
          </div>
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
