import { useTranslation } from "react-i18next";
import { Btn } from "./ui";

/** State of an entity, as built by the server (`state` on the page payload). The server sends
 *  stable keys only: `states.<code>` and `states.actions.<key>` are translated here. */
export type EntityState = {
  level: "ok" | "running" | "attention" | "failed";
  code: string;
  since: string | null;
  stuck: boolean;
  detail: string;
  actions: { key: string; url: string }[];
  children?: Record<string, Record<string, number>>;
};

const TONE: Record<string, string> = {
  running: "border-line bg-warn-bg text-warn",
  attention: "border-live/30 bg-err-bg text-[#7a1f20]",
  failed: "border-live/30 bg-err-bg text-[#7a1f20]",
};

/** Elapsed time since an ISO timestamp, in plain minutes/hours ("12 min", "3 h 05"). */
function elapsed(since: string | null): string {
  if (!since) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(since).getTime()) / 60000));
  if (Number.isNaN(minutes)) return "";
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

/** Shown on every tab of the page the entity belongs to; nothing when all is well. */
export function State({ state, busy = false, onAction }: {
  state?: EntityState | null;
  busy?: boolean;
  onAction?: (action: { key: string; url: string }) => void;
}) {
  const { t } = useTranslation();
  if (!state || state.level === "ok") return null;
  const time = state.level === "running" ? elapsed(state.since) : "";
  return (
    <div role={state.level === "failed" ? "alert" : "status"} className="grid gap-2">
      <p className={`m-0 rounded-[10px] border px-4 py-3 text-[0.92rem] ${TONE[state.level]}`}>
        {t(`states.${state.code}`)}
        {time && <span className="text-muted"> {t("states.since", { d: time })}</span>}
        {state.stuck && <strong> {t("states.stuck")}</strong>}
        {state.detail && !/[\\/]/.test(state.detail) ? ` : ${state.detail}` : ""}
      </p>
      {onAction && state.level !== "running" && state.actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {state.actions.map((a) => (
            <Btn key={a.key} variante="leaf" disabled={busy} onClick={() => onAction(a)}>
              {t(`states.actions.${a.key}`)}
            </Btn>
          ))}
        </div>
      )}
    </div>
  );
}
