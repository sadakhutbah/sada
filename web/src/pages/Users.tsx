import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, Segmented, Table, inputClass, tdClass } from "../ui";

type Compte = {
  id: number; username: string; display_name: string; role: string; active: number;
  last_login_at: string | null; permissions: string[]; custom: boolean;
};
type Donnees = {
  items: Compte[]; roles: Record<string, string>; perms: Record<string, string>;
  role_perms: Record<string, string[]>; me: number; min_password: number;
};
type Message = { ok: boolean; texte: string } | null;

/** One account's form: identity, role, permissions (the role's by default, or chosen by hand). */
type Formulaire = {
  username: string; display_name: string; role: string; active: boolean;
  custom: boolean; permissions: string[]; password: string;
};

const vide = (d: Donnees): Formulaire => ({
  username: "", display_name: "", role: "imam", active: true, custom: false,
  permissions: d.role_perms.imam ?? [], password: "",
});

export default function Users() {
  const { t } = useTranslation();
  const [d, setD] = useState<Donnees | null>(null);
  const [choisi, setChoisi] = useState<number | "nouveau" | null>(null);
  const [f, setF] = useState<Formulaire | null>(null);
  const [onglet, setOnglet] = useState("profil");
  const [msg, setMsg] = useState<Message>(null);
  const [enCours, setEnCours] = useState(false);

  const charger = () => api.users().then((r: Donnees) => { setD(r); return r; });
  useEffect(() => { charger().catch(() => setD(null)); }, []);

  const ouvrir = (cible: number | "nouveau", donnees = d) => {
    if (!donnees) return;
    setChoisi(cible); setOnglet("profil"); setMsg(null);
    if (cible === "nouveau") { setF(vide(donnees)); return; }
    const u = donnees.items.find((x) => x.id === cible);
    if (!u) return;
    setF({ username: u.username, display_name: u.display_name, role: u.role, active: !!u.active,
           custom: u.custom, permissions: u.permissions, password: "" });
  };

  if (!d) return <p className="text-muted">{t("common.loading")}</p>;

  const nouveau = choisi === "nouveau";
  const soi = choisi === d.me;
  const effectives = f ? (f.custom ? f.permissions : d.role_perms[f.role] ?? []) : [];
  const basculer = (p: string) => f && setF({ ...f, permissions:
    f.permissions.includes(p) ? f.permissions.filter((x) => x !== p) : [...f.permissions, p] });

  const enregistrer = async () => {
    if (!f) return;
    setEnCours(true); setMsg(null);
    const perms = f.custom ? JSON.stringify(f.permissions) : "";
    try {
      let r: any;
      if (nouveau) {
        r = await api.creerCompte({ username: f.username, display_name: f.display_name, role: f.role,
                                    password: f.password, permissions: perms });
      } else if (onglet === "motdepasse") {
        r = await api.mdpCompte(choisi as number, f.password);
      } else {
        r = await api.majCompte(choisi as number, { display_name: f.display_name, role: f.role,
                                                    active: f.active ? "1" : "0", permissions: perms });
      }
      const frais = await charger();
      ouvrir(nouveau ? r.id : (choisi as number), frais);
      setMsg({ ok: true, texte: r.message });
    } catch (e: any) {
      setMsg({ ok: false, texte: e.message });
    } finally {
      setEnCours(false);
    }
  };

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("users.title")}</h1>
        <Btn variante="ghost" onClick={() => ouvrir("nouveau")}>{t("users.create")}</Btn>
      </div>

      <Card>
        <Table entetes={[t("users.name"), t("users.user"), t("users.role"), t("users.rights"), t("users.active"), t("users.last")]}>
          {d.items.map((u) => (
            <tr key={u.id} onClick={() => ouvrir(u.id)}
                className={`cursor-pointer hover:bg-paper-2 ${choisi === u.id ? "bg-paper-2" : ""}`}>
              <td className={tdClass}>
                <button type="button" className="cursor-pointer border-0 bg-transparent p-0 text-left font-semibold text-deep">
                  {u.display_name}
                </button>
              </td>
              <td className={`${tdClass} text-muted`}>{u.username}</td>
              <td className={tdClass}>{d.roles[u.role] ?? u.role}</td>
              <td className={`${tdClass} text-muted`}>{u.custom ? t("users.customCount", { count: u.permissions.length }) : t("users.fromRole")}</td>
              <td className={tdClass}>{u.active ? "✓" : "—"}</td>
              <td className={`${tdClass} text-muted`}>{u.last_login_at ?? "—"}</td>
            </tr>
          ))}
        </Table>
      </Card>

      {f && (
        <Card>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="m-0 font-serif text-[1.45rem] font-normal text-deeper">
              {nouveau ? t("users.create") : f.display_name || f.username}
            </h2>
            <button type="button" onClick={() => { setChoisi(null); setF(null); }}
                    className="cursor-pointer border-0 bg-transparent text-muted hover:text-ink">{t("users.close")}</button>
          </div>
          <Segmented valeur={onglet} onChange={(o) => { setOnglet(o); setMsg(null); }} items={[
            { cle: "profil", label: t("users.tabProfile") },
            { cle: "droits", label: t("users.tabRights") },
            ...(nouveau || soi ? [] : [{ cle: "motdepasse", label: t("users.tabPassword") }]),
          ]} />

          <form className="grid max-w-[560px] gap-3.5" onSubmit={(e) => { e.preventDefault(); enregistrer(); }}>
            {onglet === "profil" && (
              <>
                <Field label={t("users.user")} aide={nouveau ? t("users.userHelp") : undefined}>
                  <input className={inputClass} value={f.username} readOnly={!nouveau} required autoComplete="off"
                         onChange={(e) => setF({ ...f, username: e.target.value })} />
                </Field>
                <Field label={t("users.name")}>
                  <input className={inputClass} value={f.display_name}
                         onChange={(e) => setF({ ...f, display_name: e.target.value })} />
                </Field>
                <Field label={t("users.role")} aide={t("users.roleHelp")}>
                  <select className={inputClass} value={f.role} disabled={soi}
                          onChange={(e) => setF({ ...f, role: e.target.value })}>
                    {Object.entries(d.roles).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                {nouveau ? (
                  <Field label={t("users.password")} aide={t("users.passwordHelp", { n: d.min_password })}>
                    <input className={inputClass} type="password" value={f.password} required minLength={d.min_password}
                           autoComplete="new-password" onChange={(e) => setF({ ...f, password: e.target.value })} />
                  </Field>
                ) : (
                  <label className="flex items-center gap-2 font-semibold">
                    <input type="checkbox" className="size-4" checked={f.active} disabled={soi}
                           onChange={(e) => setF({ ...f, active: e.target.checked })} />
                    {t("users.activeLabel")}
                  </label>
                )}
              </>
            )}

            {onglet === "droits" && (
              <>
                <label className="flex items-center gap-2 font-semibold">
                  <input type="checkbox" className="size-4" checked={f.custom} disabled={soi}
                         onChange={(e) => setF({ ...f, custom: e.target.checked,
                           permissions: d.role_perms[f.role] ?? [] })} />
                  {t("users.customLabel")}
                </label>
                <p className="m-0 text-[0.88rem] text-muted">
                  {f.custom ? t("users.customHelp") : t("users.fromRoleHelp", { role: d.roles[f.role] ?? f.role })}
                </p>
                <div className="grid gap-1.5">
                  {Object.entries(d.perms).map(([p, label]) => (
                    <label key={p} className={`flex items-center gap-2 ${f.custom && !soi ? "" : "text-muted"}`}>
                      <input type="checkbox" className="size-4" checked={effectives.includes(p)}
                             disabled={!f.custom || soi} onChange={() => basculer(p)} />
                      {label}
                    </label>
                  ))}
                </div>
              </>
            )}

            {onglet === "motdepasse" && (
              <Field label={t("users.newPassword")} aide={t("users.resetHelp", { n: d.min_password })}>
                <input className={inputClass} type="password" value={f.password} required minLength={d.min_password}
                       autoComplete="new-password" onChange={(e) => setF({ ...f, password: e.target.value })} />
              </Field>
            )}

            {soi && onglet !== "motdepasse" && <p className="m-0 text-[0.88rem] text-muted">{t("users.selfHelp")}</p>}
            <div className="flex flex-wrap items-center gap-3">
              <Btn type="submit" disabled={enCours}>
                {nouveau ? t("users.createSubmit") : onglet === "motdepasse" ? t("users.resetSubmit") : t("users.save")}
              </Btn>
              {msg && <span role="status" className={`text-[0.9rem] ${msg.ok ? "text-green" : "text-[#7a1f20]"}`}>{msg.texte}</span>}
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}
