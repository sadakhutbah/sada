import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Btn, Card, Field, Tabs, Title, inputClass } from "../ui";

/** Un dossier vide n'est pas une case blanche : c'est le dossier par défaut du programme,
 *  et le chemin par défaut est rappelé dessous en clair relatif (« data/state »). */
function Chemin({ valeur, defaut }: { valeur?: string; defaut?: string }) {
  const { t } = useTranslation();
  return (
    <>
      <input
        className={`${inputClass} font-mono text-[0.88rem]`}
        defaultValue={valeur ?? ""}
        placeholder={t("settings.defaultFolder")}
        readOnly
      />
      <span className="mt-1 block text-[0.82rem] text-muted">
        {t("settings.byDefault")}{" "}<span className="font-mono">{defaut || t("settings.defaultFolder")}</span>
      </span>
    </>
  );
}

export default function Settings() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [onglet, setOnglet] = useState("folders");

  useEffect(() => { api.settings().then(setD).catch(() => setD(null)); }, []);

  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  const m = d.mosque;
  const defauts = d.defaults ?? {};

  const contenu: Record<string, React.ReactNode> = {
    mosque: (
      <div className="grid max-w-[520px] gap-3.5">
        <Field label={t("settings.name")}><input className={inputClass} defaultValue={m.name} readOnly /></Field>
        <Field label={t("settings.timezone")}><input className={inputClass} defaultValue={m.timezone} readOnly /></Field>
        <Field label={t("settings.clipTitle")}><input className={inputClass} defaultValue={m.clip_title} readOnly /></Field>
      </div>
    ),
    live: (
      <div className="grid max-w-[520px] gap-3.5">
        <Field label={t("settings.orchestrator")}><input className={inputClass} defaultValue={m.orchestrator_url ?? ""} readOnly /></Field>
      </div>
    ),
    folders: (
      <div>
        <Title>{t("settings.foldersTitle")}</Title>
        {/* L'introduction décrit les trois champs : elle se place avant, pas sous le premier. */}
        <p className="mt-0 mb-4 max-w-[80ch] text-[0.9rem] text-muted">{t("settings.foldersHint")}</p>
        <div className="grid gap-4">
          {[
            { cle: "state_dir", defaut: defauts.state, label: t("settings.stateDir"), quoi: t("settings.shortState") },
            { cle: "output_dir", defaut: defauts.output, label: t("settings.outputDir"), quoi: t("settings.shortOutput") },
            { cle: "publish_dir", defaut: defauts.publish, label: t("settings.publishDir"), quoi: t("settings.shortPublish") },
          ].map((ch) => (
            <div key={ch.cle}>
              <Field label={`${ch.label} — ${ch.quoi}`}>
                <Chemin valeur={m[ch.cle]} defaut={ch.defaut} />
              </Field>
              <Btn variante="ghost" className="mt-2 min-h-9 px-3 py-1.5 text-[0.9rem]" disabled title={t("settings.soon")}>
                {t("common.browse")}
              </Btn>
            </div>
          ))}
        </div>
        <Btn className="mt-5" disabled title={t("settings.soon")}>{t("common.save")}</Btn>
      </div>
    ),
  };

  // Seuls les onglets qui ont un contenu sont proposés ; les autres sont annoncés éteints.
  const onglets = d.sections.map((s: string) => ({
    cle: s,
    label: t(`settings.tabs.${s}`),
    eteint: !contenu[s],
  }));

  return (
    <div className="grid gap-4">
      <h1 className="m-0 font-serif text-[2.1rem] font-normal leading-[1.1] text-deeper">{t("settings.title")}</h1>
      <div>
        <Tabs items={onglets} valeur={onglet} onChange={setOnglet} />
        <Card>
          <div key={onglet}>{contenu[onglet] ?? <p className="text-muted">{t("settings.soon")}</p>}</div>
        </Card>
      </div>
    </div>
  );
}
