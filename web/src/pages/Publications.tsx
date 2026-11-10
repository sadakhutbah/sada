import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, Table, Title, inputClass, tdClass } from "../ui";

type Champ = { name: string; label: string; value: string; secret: boolean; placeholder: string };
type Retour = { ok: boolean; texte: string } | null;

/** Formulaire d'un connecteur : un secret enregistré s'affiche masqué, et le laisser vide ne le modifie pas. */
function FormConnecteur({ c, onSaved }: { c: any; onSaved: () => void }) {
  const { t } = useTranslation();
  const [vals, setVals] = useState<Record<string, string>>(
    Object.fromEntries(c.fields.map((f: Champ) => [f.name, f.secret ? "" : f.value])));
  const [actif, setActif] = useState<boolean>(Boolean(c.enabled));
  const [msg, setMsg] = useState<Retour>(null);
  const [occupe, setOccupe] = useState(false);

  const enregistrer = async () => {
    setOccupe(true);
    setMsg(null);
    try {
      await api.connecteur(c.key, actif, vals);
      setMsg({ ok: true, texte: t("publications.saved") });
      onSaved();
    } catch (e: any) {
      setMsg({ ok: false, texte: e.message });
    } finally {
      setOccupe(false);
    }
  };

  return (
    <div className="grid max-w-xl gap-3 pt-3">
      {c.fields.map((f: Champ) => (
        <Field key={f.name} label={f.label} aide={f.secret && f.value ? t("publications.secretKeep") : undefined}>
          <input
            className={inputClass}
            type={f.secret ? "password" : "text"}
            autoComplete="off"
            value={vals[f.name] ?? ""}
            placeholder={f.secret && f.value ? f.value : f.placeholder}
            onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })}
          />
        </Field>
      ))}
      <label className="flex items-center gap-2 font-semibold">
        <input type="checkbox" checked={actif} onChange={(e) => setActif(e.target.checked)} />
        {t("publications.enableIt")}
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <Btn onClick={enregistrer} disabled={occupe}>{occupe ? t("common.saving") : t("common.save")}</Btn>
        {msg && <span className={msg.ok ? "text-deep" : "text-[#b42c23]"} role="status">{msg.texte}</span>}
      </div>
    </div>
  );
}

/** Notifications à l'imam : canal, destination (secrets masqués) et envoi d'un message d'essai. */
function Notifications({ n, onSaved }: { n: any; onSaved: () => void }) {
  const { t } = useTranslation();
  const [v, setV] = useState({ mode: n.mode as string, webhook: "", telegram_token: "", telegram_chat: n.telegram_chat as string });
  const [msg, setMsg] = useState<Retour>(null);
  const [occupe, setOccupe] = useState(false);

  const agir = async (fn: () => Promise<any>) => {
    setOccupe(true);
    setMsg(null);
    try {
      const r = await fn();
      setMsg({ ok: true, texte: r.message });
      onSaved();
    } catch (e: any) {
      setMsg({ ok: false, texte: e.message });
    } finally {
      setOccupe(false);
    }
  };
  const garde = (enregistre: string) => (enregistre ? t("publications.secretKeep") : undefined);

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
          <Field label={t("publications.webhookUrl")} aide={garde(n.webhook)}>
            <input className={inputClass} type="password" autoComplete="off" value={v.webhook} placeholder={n.webhook || "https://"}
              onChange={(e) => setV({ ...v, webhook: e.target.value })} />
          </Field>
        )}
        {v.mode === "telegram" && (
          <>
            <Field label={t("publications.telegramToken")} aide={garde(n.telegram_token)}>
              <input className={inputClass} type="password" autoComplete="off" value={v.telegram_token} placeholder={n.telegram_token}
                onChange={(e) => setV({ ...v, telegram_token: e.target.value })} />
            </Field>
            <Field label={t("publications.telegramChat")}>
              <input className={inputClass} value={v.telegram_chat} onChange={(e) => setV({ ...v, telegram_chat: e.target.value })} />
            </Field>
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Btn disabled={occupe} onClick={() => agir(() => api.notifications(v))}>
            {occupe ? t("common.saving") : t("common.save")}
          </Btn>
          <Btn variante="ghost" disabled={occupe || !n.active} onClick={() => agir(() => api.testerNotifications())}>
            {t("publications.test")}
          </Btn>
          {msg && <span className={msg.ok ? "text-deep" : "text-[#b42c23]"} role="status">{msg.texte}</span>}
        </div>
      </div>
    </Card>
  );
}

export default function Publications() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [ouvert, setOuvert] = useState("");

  const charger = () => api.publications().then(setD).catch(() => setD({ connectors: [], publications: [], ready: [] }));
  useEffect(() => { charger(); }, []);

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("publications.title")}</h1>
      <Card>
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
                    <Btn variante="ghost" onClick={() => setOuvert(ouvert === c.key ? "" : c.key)}>
                      {ouvert === c.key ? t("publications.closeForm") : t("publications.configure")}
                    </Btn>
                  )}
                </div>
                {d.can_configure && ouvert === c.key && <FormConnecteur c={c} onSaved={charger} />}
              </div>
            ))}
          </div>
        )}
        {d && !d.can_configure && <p className="mb-0 mt-3 text-[0.9rem] text-muted">{t("publications.adminOnly")}</p>}
      </Card>

      {d?.notifications && <Notifications key={`${d.notifications.mode}${d.notifications.active}`} n={d.notifications} onSaved={charger} />}

      <Card>
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
      </Card>
    </div>
  );
}
