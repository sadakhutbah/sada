import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { dateFr, mmss } from "../format";
import Player from "../Player";
import { Card, Kicker, Title } from "../ui";

/** Tarawih nights as replays, for the imams to hear their recitation again. Listed apart (kind
 *  filter): never among the khutbas, and without transcript, clips nor translation. */

type Segment = { name: string; start: number; end: number | null };
type Night = {
  night: { id: number; label: string; date: string | null; start_clock: string | null; end_clock: string | null;
    duration_s: number | null; session_no: number | null };
  mode: string; reciters: string[]; segments: Segment[]; has_video: boolean;
};

export default function Tarawih() {
  const { t, i18n } = useTranslation();
  const [items, setItems] = useState<any[] | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    api.sermons({ kind: "tarawih", limit: 100 }).then((r) => setItems(r.items)).catch((e) => setErreur(e?.message ?? t("common.error")));
  }, [t]);

  return (
    <div className="grid gap-4">
      <header>
        <Kicker>{t("tarawihReplay.kicker")}</Kicker>
        <Title grand>{t("tarawihReplay.title")}</Title>
        <p className="m-0 max-w-[70ch] text-[0.92rem] text-muted">{t("tarawihReplay.hint")}</p>
      </header>
      {erreur && <p className="m-0 text-err">{erreur}</p>}
      <Card className="p-0!">
        {items === null ? (
          <p className="m-0 px-5 py-4 text-muted">{t("common.loading")}</p>
        ) : items.length === 0 ? (
          <p className="m-0 px-5 py-4 text-muted">{t("tarawihReplay.empty")}</p>
        ) : (
          <ul className="m-0 list-none divide-y divide-line/80 p-0">
            {items.map((k) => (
              <li key={k.id}>
                <Link to={`/tarawih/${k.id}`}
                  className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 px-5 py-3 text-inherit no-underline hover:bg-paper-2/50">
                  <span className="min-w-0">
                    <span className="font-semibold text-deep">
                      {k.session_no ? t("tarawihReplay.night", { n: k.session_no }) : "Tarawih"}
                    </span>
                    <span className="text-[0.85rem] text-muted"> · {dateFr(k.date, i18n.language)}</span>
                    <span className="block truncate text-[0.9rem] text-ink/85">{k.speaker || t("tarawihReplay.noReciter")}</span>
                  </span>
                  <span className="text-[0.85rem] tabular-nums text-muted">{mmss(k.duration_s)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/** One night: the player and its chapters (one per reciter, when the control room switched them). */
export function TarawihNight() {
  const { t, i18n } = useTranslation();
  const { id } = useParams();
  const video = useRef<HTMLVideoElement>(null);
  const [d, setD] = useState<Night | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [tps, setTps] = useState(0);

  useEffect(() => {
    api.tarawihNight(Number(id)).then(setD).catch((e) => setErreur(e?.message ?? t("common.error")));
  }, [id, t]);

  if (erreur) return <p className="text-err">{erreur}</p>;
  if (!d) return <p className="text-muted">{t("common.loading")}</p>;
  const n = d.night;
  const courant = d.segments.findIndex((s) => tps >= s.start && (s.end === null || tps < s.end));
  const aller = (s: number) => { if (video.current) { video.current.currentTime = s; void video.current.play(); } };

  return (
    <div className="grid gap-4">
      <header className="grid gap-2">
        <Link to="/tarawih" className="justify-self-start text-[0.85rem] text-deep underline decoration-leaf underline-offset-4">
          ← {t("nav.tarawih")}
        </Link>
        <Kicker>{n.session_no ? t("tarawihReplay.night", { n: n.session_no }) : "Tarawih"}</Kicker>
        <h1 className="m-0 font-serif text-[2.2rem] font-normal leading-[1.05] text-deeper first-letter:uppercase">
          {dateFr(n.date, i18n.language)}
        </h1>
        <p className="m-0 text-[0.9rem] text-muted">
          <span className="tabular-nums">{n.start_clock ?? "?"}–{n.end_clock ?? "?"} · {mmss(n.duration_s)}</span>
          {" · "}{t(`tarawihReplay.mode.${d.mode === "video" ? "video" : "audio"}`)}
        </p>
      </header>
      <div className="grid items-start gap-4 min-[1000px]:grid-cols-[minmax(0,1.6fr)_minmax(300px,1fr)]">
        <div className="overflow-hidden rounded-[10px] bg-night">
          {d.has_video ? (
            <Player ref={video} preload="metadata" src={`/media/sermon/${n.id}.mp4`}
              onTimeUpdate={(e) => setTps(e.currentTarget.currentTime)}
              className="block max-h-[60vh] w-full bg-night object-contain" />
          ) : (
            <div className="grid h-[30vh] min-h-[200px] place-items-center px-6 text-center">
              <p className="m-0 text-[0.95rem] text-night-muted">{t("sermons.detail.noVideo")}</p>
            </div>
          )}
        </div>
        <Card>
          <h2 className="m-0 mb-2 text-[1rem] font-semibold text-deep">
            {d.segments.length ? t("tarawihReplay.chapters") : t("tarawihReplay.reciters")}
          </h2>
          {d.segments.length > 0 ? (
            <ol className="m-0 grid list-none gap-1 p-0">
              {d.segments.map((s, i) => (
                <li key={`${s.start}-${s.name}`}>
                  <button type="button" onClick={() => aller(s.start)} disabled={!d.has_video}
                    aria-current={i === courant ? "true" : undefined}
                    className={`flex w-full items-baseline gap-3 rounded-md border px-3 py-2 text-left ${
                      i === courant ? "border-leaf bg-leaf/15" : "border-line bg-white hover:bg-paper-2"} cursor-pointer disabled:cursor-default`}>
                    <span className="tabular-nums text-[0.85rem] text-muted">{mmss(s.start)}</span>
                    <span className="min-w-0 flex-1 truncate font-semibold">{s.name}</span>
                    {s.end !== null && <span className="tabular-nums text-[0.8rem] text-muted">{mmss(s.end - s.start)}</span>}
                  </button>
                </li>
              ))}
            </ol>
          ) : d.reciters.length > 0 ? (
            <>
              <ul className="m-0 grid gap-1 pl-5">{d.reciters.map((r) => <li key={r}>{r}</li>)}</ul>
              <p className="mt-2 mb-0 text-[0.8rem] text-muted">{t("tarawihReplay.noSegments")}</p>
            </>
          ) : (
            <p className="m-0 text-muted">{t("tarawihReplay.noReciter")}</p>
          )}
        </Card>
      </div>
    </div>
  );
}
