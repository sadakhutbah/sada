import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, Table, Tabs, Title, inputClass, tdClass } from "../ui";

type FieldDef = { name: string; label: string; value: string; secret: boolean; placeholder: string };
type Feedback = { ok: boolean; text: string } | null;

/** Connector form: a stored secret is shown masked, and leaving it empty keeps it unchanged. */
function ConnectorForm({ c, onSaved }: { c: any; onSaved: () => void }) {
  const { t } = useTranslation();
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(c.fields.map((f: FieldDef) => [f.name, f.secret ? "" : f.value])));
  const [enabled, setEnabled] = useState<boolean>(Boolean(c.enabled));
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      await api.saveConnector(c.key, enabled, values);
      setFeedback({ ok: true, text: t("publications.saved") });
      onSaved();
    } catch (e: any) {
      setFeedback({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid max-w-xl gap-3 pt-3">
      {c.fields.map((f: FieldDef) => (
        <Field key={f.name} label={f.label} aide={f.secret && f.value ? t("publications.secretKeep") : undefined}>
          <input
            className={inputClass}
            type={f.secret ? "password" : "text"}
            autoComplete="off"
            value={values[f.name] ?? ""}
            placeholder={f.secret && f.value ? f.value : f.placeholder}
            onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
          />
        </Field>
      ))}
      <label className="flex items-center gap-2 font-semibold">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        {t("publications.enableIt")}
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <Btn onClick={save} disabled={busy}>{busy ? t("common.saving") : t("common.save")}</Btn>
        {feedback && <span className={feedback.ok ? "text-deep" : "text-[#7f2122]"} role="status">{feedback.text}</span>}
      </div>
    </div>
  );
}

/** Notifications to the imam: channel, destination (secrets masked) and a test message. */
function Notifications({ n, onSaved }: { n: any; onSaved: () => void }) {
  const { t } = useTranslation();
  const [v, setV] = useState({ mode: n.mode as string, webhook: "", telegram_token: "", telegram_chat: n.telegram_chat as string });
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<any>) => {
    setBusy(true);
    setFeedback(null);
    try {
      const r = await fn();
      setFeedback({ ok: true, text: r.message });
      onSaved();
    } catch (e: any) {
      setFeedback({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };
  const keepHint = (stored: string) => (stored ? t("publications.secretKeep") : undefined);

  return (
    <Card>
      <Title>{t("publications.notifTitle")}</Title>
      <p className="mt-0 text-muted">{t("publications.notifHint")}</p>
      <p className="font-semibold">
        {t("publications.destination")} : {n.active ? t(`publications.${n.destination}`) : t("publications.none")}
      </p>
      <div className="grid max-w-xl gap-3">
        <Field label={t("publications.channel")}>
          <select className={`${inputClass} py-2`} value={v.mode} onChange={(e) => setV({ ...v, mode: e.target.value })}>
            <option value="">{t("publications.none")}</option>
            <option value="webhook">{t("publications.webhook")}</option>
            <option value="telegram">{t("publications.telegram")}</option>
          </select>
        </Field>
        {v.mode === "webhook" && (
          <Field label={t("publications.webhookUrl")} aide={keepHint(n.webhook)}>
            <input className={inputClass} type="password" autoComplete="off" value={v.webhook} placeholder={n.webhook || "https://"}
              onChange={(e) => setV({ ...v, webhook: e.target.value })} />
          </Field>
        )}
        {v.mode === "telegram" && (
          <>
            <Field label={t("publications.telegramToken")} aide={keepHint(n.telegram_token)}>
              <input className={inputClass} type="password" autoComplete="off" value={v.telegram_token} placeholder={n.telegram_token}
                onChange={(e) => setV({ ...v, telegram_token: e.target.value })} />
            </Field>
            <Field label={t("publications.telegramChat")}>
              <input className={inputClass} value={v.telegram_chat} onChange={(e) => setV({ ...v, telegram_chat: e.target.value })} />
            </Field>
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Btn disabled={busy} onClick={() => run(() => api.saveNotifications(v))}>
            {busy ? t("common.saving") : t("common.save")}
          </Btn>
          <Btn variante="ghost" disabled={busy || !n.active} onClick={() => run(() => api.testNotifications())}>
            {t("publications.test")}
          </Btn>
          {feedback && <span className={feedback.ok ? "text-deep" : "text-[#7f2122]"} role="status">{feedback.text}</span>}
        </div>
      </div>
    </Card>
  );
}

export default function Publications() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [open, setOpen] = useState("");
  const [vue, setVue] = useState("connectors");

  const load = () => api.publications().then(setD).catch(() => setD({ connectors: [], publications: [], ready: [] }));
  useEffect(() => { load(); }, []);

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("publications.title")}</h1>
      {/* Three tabs rather than three stacked cards: one thing at a time on screen. */}
      <Tabs items={[
        { cle: "connectors", label: t("publications.tabConnectors"), compte: d?.connectors?.filter((c: any) => c.available).length },
        { cle: "notifications", label: t("publications.tabNotifications") },
        { cle: "history", label: t("publications.tabHistory"), compte: d?.publications?.length },
      ]} valeur={vue} onChange={(c) => setVue(c)} />
      {vue === "connectors" && <Card>
        <Title>{t("publications.connectorsTitle")}</Title>
        {!d ? <p className="text-muted">{t("common.loading")}</p> : d.connectors.length === 0 ? (
          <p className="text-muted">{t("common.empty")}</p>
        ) : (
          <div className="grid gap-3">
            {d.connectors.map((c: any) => (
              <div key={c.key} className="rounded-lg border border-line p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <span className="font-semibold">{c.label}</span>{" "}
                    <span className={`rounded-full px-2.5 py-0.5 text-[0.8rem] font-bold ${c.available ? "bg-deep text-white" : "bg-warn-bg text-warn"}`}>
                      {c.available ? t("publications.available") : t("publications.unavailable")}
                    </span>
                    <p className="m-0 mt-1 text-[0.9rem] text-muted">{c.description}</p>
                    {!c.available && c.why && <p className="m-0 mt-1 text-[0.9rem]">{c.why}</p>}
                    {!c.implemented && <p className="m-0 mt-1 text-[0.9rem] text-muted">{t("publications.notImplemented")}</p>}
                  </div>
                  {d.can_configure && (
                    <Btn variante="ghost" onClick={() => setOpen(open === c.key ? "" : c.key)}>
                      {open === c.key ? t("publications.closeForm") : t("publications.configure")}
                    </Btn>
                  )}
                </div>
                {d.can_configure && open === c.key && <ConnectorForm c={c} onSaved={load} />}
              </div>
            ))}
          </div>
        )}
        {d && !d.can_configure && <p className="mb-0 mt-3 text-[0.9rem] text-muted">{t("publications.adminOnly")}</p>}
      </Card>}

      {vue === "notifications" && d?.notifications &&
        <Notifications key={`${d.notifications.mode}${d.notifications.active}`} n={d.notifications} onSaved={load} />}

      {vue === "notifications" && d && !d.notifications && <Card>
        <p className="m-0 text-muted">{t("publications.notificationsAdminOnly")}</p>
      </Card>}

      {vue === "history" && <Card>
        <Title>{t("publications.history")}</Title>
        {!d ? <p className="text-muted">{t("common.loading")}</p> : (d.publications ?? []).length === 0 ? (
          <p className="text-muted">{t("publications.noHistory")}</p>
        ) : (
          <Table entetes={[t("publications.clip"), t("publications.connector"), t("publications.when"), t("publications.by"), t("publications.result")]}>
            {d.publications.map((p: any) => (
              <tr key={p.id}>
                <td className={tdClass}>{p.sermon} · #{p.idx}</td>
                <td className={tdClass}>{p.connector}</td>
                <td className={`${tdClass} text-muted`}>{(p.created_at || "").slice(0, 16).replace("T", " ")}</td>
                <td className={tdClass}>{p.who || "—"}</td>
                <td className={`${tdClass} text-muted`}>{p.status === "publie" ? p.location : p.message}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>}
    </div>
  );
}
