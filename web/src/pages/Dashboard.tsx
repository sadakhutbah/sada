import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";

export default function Dashboard() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  useEffect(() => { api.dashboard().then(setD).catch(() => setD({ erreur: true })); }, []);
  if (!d) return <p className="vide">{t("common.loading")}</p>;
  if (d.erreur) return <p className="vide err">{t("common.error")}</p>;

  return (
    <>
      <div className="stats">
        <div className="stat"><span className="kicker">{t("dashboard.pending")}</span><b>{d.counts.pending}</b></div>
        <div className="stat"><span className="kicker">{t("dashboard.ready")}</span><b>{d.counts.ready}</b></div>
        <div className="stat"><span className="kicker">{t("dashboard.sermons")}</span><b>{d.counts.sermons}</b></div>
        <div className="stat">
          <span className="kicker">{t("dashboard.live")}</span>
          <b>{d.live.ok ? (d.live.live ? t("dashboard.liveOn") : t("dashboard.liveOff")) : t("dashboard.unreachable")}</b>
        </div>
      </div>
      <section className="carte">
        <h2>{t("dashboard.recent")}</h2>
        <table><tbody>
          {d.recent.map((k: any) => (
            <tr key={k.id}>
              <td>{k.date}</td><td>{k.label}</td><td className="muted">{k.imam ?? "—"}</td>
            </tr>
          ))}
        </tbody></table>
      </section>
    </>
  );
}
