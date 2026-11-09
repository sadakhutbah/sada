import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const etat = !v.ok ? t("dashboard.unreachable") : v.state_label ?? (v.live ? t("dashboard.liveOn") : t("dashboard.liveOff"));

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        {[
          { k: "dashboard.pending", n: d.counts.pending },
          { k: "dashboard.ready", n: d.counts.ready },
          { k: "dashboard.sermons", n: d.counts.sermons },
        ].map((s) => (
          <Card key={s.k}>
            <Kicker>{t(s.k)}</Kicker>
            <p className="m-0 font-serif text-[2.6rem] leading-none text-deeper">{s.n}</p>
          </Card>
        ))}
      </div>

      <section className="rounded-[14px] bg-night p-5 text-night-ink">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="m-0 mb-1 text-[0.75rem] font-semibold uppercase tracking-[0.14em] text-leaf">
              {t("dashboard.live")}
            </p>
            <p className="m-0 flex items-center gap-2.5 font-serif text-[1.7rem] leading-none text-white">
              <span className={`inline-block size-3 rounded-full ${v.live ? "bg-live shadow-[0_0_0_4px_rgba(215,55,44,0.25)]" : "bg-[#63755f]"}`} />
              {etat}
            </p>
          </div>
          {v.clock && <p className="m-0 font-serif text-[2.4rem] leading-none text-white/90">{v.clock}</p>}
        </div>
        <p className="m-0 mt-3 text-[0.92rem] text-night-muted">{v.session ?? "—"}</p>
        {v.ok === false && v.error && <p className="m-0 mt-2 text-[0.85rem] text-[#f0b8b2]">{v.error}</p>}
      </section>

      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Card>
          <Title>{t("dashboard.recent")}</Title>
          {d.recent.length === 0 ? <p className="text-muted">{t("common.empty")}</p> : (
            <table className="w-full border-collapse text-[0.95rem]">
              <thead>
                <tr>
                  {[t("sermons.date"), t("sermons.label"), t("sermons.imam"), t("sermons.pending")].map((h) => (
                    <th key={h} className="border-b border-line p-2 text-left text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.recent.map((k: any) => (
                  <tr key={k.id}>
                    <td className="border-b border-line p-2.5 align-top whitespace-nowrap">{k.date}</td>
                    <td className="border-b border-line p-2.5 align-top">{k.label}</td>
                    <td className="border-b border-line p-2.5 align-top text-muted">{k.imam ?? "—"}</td>
                    <td className="border-b border-line p-2.5 align-top">{k.pending || ""}</td>
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
                <li key={c.id} className="flex items-baseline justify-between gap-3 border-b border-line pb-2 last:border-0">
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
