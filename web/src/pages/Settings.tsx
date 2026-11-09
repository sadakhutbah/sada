import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { Card, Field, Segmented, inputClass } from "../ui";

/** Un dossier vide n'est pas une case blanche : c'est le dossier par défaut du programme. */
function Chemin({ valeur }: { valeur?: string }) {
  const { t } = useTranslation();
  if (valeur) return <input className={`${inputClass} font-mono text-[0.88rem]`} defaultValue={valeur} readOnly />;
  return (
    <span className="block w-full rounded-lg border border-dashed border-[#bfcbb6] bg-paper-2 px-3 py-2.5 text-[0.92rem] text-muted">
      {t("settings.defaultFolder")}
    </span>
  );
}

export default function Settings() {
  const { t } = useTranslation();
  const [d, setD] = useState<any>(null);
  const [onglet, setOnglet] = useState("mosque");

  useEffect(() => { api.settings().then(setD).catch(() => setD(null)); }, []);

  if (!d) return <p className="py-16 text-center text-muted">{t("common.loading")}</p>;
  const m = d.mosque;

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
      <div className="max-w-2xl">
        {/* L'aide décrit les trois champs : elle se place avant la grille, pas sous le premier. */}
        <p className="mt-0 mb-3 text-[0.86rem] text-muted">{t("settings.foldersHint")}</p>
        <div className="grid gap-3.5">
          <Field label={t("settings.stateDir")}><Chemin valeur={m.state_dir} /></Field>
          <Field label={t("settings.outputDir")}><Chemin valeur={m.output_dir} /></Field>
          <Field label={t("settings.publishDir")}><Chemin valeur={m.publish_dir} /></Field>
        </div>
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
      <Card>
      <Segmented items={onglets} valeur={onglet} onChange={setOnglet} />
      <div key={onglet}>{contenu[onglet] ?? <p className="text-muted">{t("settings.soon")}</p>}</div>
      </Card>
    </div>
  );
}
