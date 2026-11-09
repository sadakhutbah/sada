import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, Kicker, Title } from "../ui";

export default function Dashboard() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [erreur, setErreur] = useState(false);

  useEffect(() => {
    api.dashboard().then(setD).catch(() => setErreur(true));
  }, []);

  if (erreur) return <p className="py-16 text-center text-muted">{t("common.error")}</p>;
  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;

  const v = d.live ?? {};
  const pastille = !v.ok ? "bg-dot-off" : v.state === "live" ? "bg-live shadow-[0_0_0_4px_rgba(215,55,44,0.25)]" : "bg-leaf";
  const etat = !v.ok ? t("dashboard.unreachable") : v.state_label ?? (v.live ? t("dashboard.liveOn") : t("dashboard.liveOff"));
  const imams = d.recent.some((k: any) => k.imam);
  const enAttente = d.recent.some((k: any) => k.pending > 0);

  return (
    <div className="grid gap-4">
      {/* Le live passe avant les compteurs : c'est ce qu'on vient regarder en premier. */}
      <section className="rounded-[14px] bg-night p-5 text-night-ink">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <Kicker>{t("dashboard.live")}</Kicker>
            <p className="m-0 flex items-center gap-2.5 font-serif text-[1.7rem] leading-none text-white">
              <span className={`inline-block size-3 shrink-0 rounded-full ${pastille}`} />
              {etat}
            </p>
          </div>
          <p className="m-0 font-serif text-[2.4rem] leading-none tabular-nums text-white/90">{v.clock ?? "--:--:--"}</p>
        </div>
        {v.session && <p className="m-0 mt-3 text-[0.92rem] text-night-muted">{v.session}</p>}
        {v.ok === false && v.error && <p className="m-0 mt-2 text-[0.88rem] text-live-off">{v.error}</p>}
      </section>

      <div className="grid gap-4 sm:grid-cols-3">
        {[
          { k: "dashboard.pending", n: d.counts.pending, to: "/clips" },
          { k: "dashboard.ready", n: d.counts.ready, to: "/clips" },
          { k: "dashboard.sermons", n: d.counts.sermons, to: "/sermons" },
        ].map((s) => (
          <Link key={s.k} to={s.to} className="no-underline">
            <Card className="h-full transition-colors hover:border-leaf">
              <Kicker>{t(s.k)}</Kicker>
              <p className="m-0 font-serif text-[2.4rem] leading-none text-deeper">{s.n}</p>
            </Card>
          </Link>
        ))}
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card>
          <Title>{t("dashboard.recent")}</Title>
          {d.recent.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
            <table className="w-full border-collapse text-[0.95rem]">
              <thead>
                <tr>
                  {[t("sermons.date"), t("sermons.label"), ...(imams ? [t("sermons.imam")] : []), ...(enAttente ? [t("sermons.pending")] : [])].map((h) => (
                    <th key={h} className="border-b border-line px-2 py-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.recent.map((k: any, i: number) => (
                  <tr key={k.id} className="hover:bg-paper-2/60">
                    <td className={`border-b border-line px-2 py-2.5 align-middle tabular-nums ${i === d.recent.length - 1 ? "border-b-0" : ""}`}>
                      <Link to={`/sermons/${k.id}`} className="no-underline">{k.date}</Link>
                    </td>
                    <td className={`border-b border-line px-2 py-2.5 align-middle font-semibold ${i === d.recent.length - 1 ? "border-b-0" : ""}`}>{k.label}</td>
                    {imams && <td className={`border-b border-line px-2 py-2.5 align-middle text-muted ${i === d.recent.length - 1 ? "border-b-0" : ""}`}>{k.imam ?? "—"}</td>}
                    {enAttente && <td className={`border-b border-line px-2 py-2.5 align-middle ${i === d.recent.length - 1 ? "border-b-0" : ""}`}>{k.pending > 0 ? k.pending : ""}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card>
          <Title>{t("dashboard.commands")}</Title>
          {d.commands.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
            <ul className="m-0 grid list-none gap-2 p-0 text-[0.92rem]">
              {d.commands.map((c: any) => (
                <li key={c.id} className="flex items-baseline justify-between gap-3 border-b border-line pb-2 last:border-0 last:pb-0">
                  <span className="font-semibold">{c.action.replace("live.", "")}</span>
                  <span className="text-[0.82rem] text-muted">{c.who ?? "—"}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
