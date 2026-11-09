import i18n from "i18next";
import { initReactI18next } from "react-i18next";

/** Textes de l'interface. Les adresses et les données restent identiques dans toutes les langues ;
 *  seuls les libellés changent. `ar` est prévu pour les mosquées arabophones (sens de lecture à droite). */
const fr = {
  app: { name: "Minbar Live", office: "back-office" },
  nav: {
    dashboard: "Tableau de bord", sermons: "Khutbas", clips: "Clips", publications: "Publication",
    settings: "Réglages", users: "Comptes", audit: "Journal", logout: "Quitter",
  },
  common: { loading: "Chargement…", empty: "Rien à afficher.", save: "Enregistrer", close: "Fermer", error: "Erreur" },
  login: {
    title: "Connexion", user: "Identifiant", password: "Mot de passe", submit: "Entrer",
    mosque: "Choisir la mosquée", bad: "Identifiant ou mot de passe incorrect.", locked: "Trop de tentatives. Réessayez dans quelques minutes.",
  },
  dashboard: {
    pending: "Clips à valider", ready: "Prêts à publier", sermons: "Khutbas importées",
    live: "Orchestrateur du live", recent: "Dernières khutbas", commands: "Dernières commandes",
    liveOn: "En direct", liveOff: "Arrêté", unreachable: "Injoignable",
  },
  sermons: {
    title: "Khutbas", date: "Date", label: "Passage", imam: "Imam", clips: "Clips", pending: "À valider",
    theme: "Thème", open: "Ouvrir",
  },
  clips: {
    title: "Clips", sermon: "Khutba", kind: "Type", status: "Statut", duration: "Durée", why: "Raison",
    all: "Tous",
  },
  publications: { title: "Publication", connector: "Connecteur", enabled: "Activé", configured: "Réglé" },
  settings: {
    title: "Réglages",
    tabs: { mosque: "Mosquée", logo: "Logo", live: "Live", folders: "Dossiers", model: "Modèle", connectors: "Publication", access: "Accès" },
    name: "Nom de la mosquée", timezone: "Fuseau horaire", clipTitle: "Titre des clips", orchestrator: "Adresse de l'orchestrateur",
    stateDir: "Dossier d'état", outputDir: "Dossier des vidéos", publishDir: "Dossier de publication",
    foldersHint: "Les dossiers de travail du programme.", defaultFolder: "Dossier par défaut du programme", soon: "Cet onglet arrive avec la suite du passage.",
    readonly: "Lecture seule pour l'instant : les formulaires d'écriture arrivent avec la suite du passage React.",
  },
  users: { title: "Comptes", user: "Identifiant", name: "Nom affiché", role: "Rôle", active: "Actif", last: "Dernière connexion" },
  audit: { title: "Journal", when: "Quand", who: "Qui", action: "Action", object: "Objet", ip: "Adresse" },
};

const en: typeof fr = {
  app: { name: "Minbar Live", office: "back office" },
  nav: {
    dashboard: "Dashboard", sermons: "Sermons", clips: "Clips", publications: "Publishing",
    settings: "Settings", users: "Users", audit: "Log", logout: "Sign out",
  },
  common: { loading: "Loading…", empty: "Nothing to show.", save: "Save", close: "Close", error: "Error" },
  login: {
    title: "Sign in", user: "Username", password: "Password", submit: "Sign in",
    mosque: "Choose the mosque", bad: "Wrong username or password.", locked: "Too many attempts. Try again in a few minutes.",
  },
  dashboard: {
    pending: "Clips to review", ready: "Ready to publish", sermons: "Imported sermons",
    live: "Live orchestrator", recent: "Latest sermons", commands: "Latest commands",
    liveOn: "Live", liveOff: "Stopped", unreachable: "Unreachable",
  },
  sermons: {
    title: "Sermons", date: "Date", label: "Slot", imam: "Imam", clips: "Clips", pending: "To review",
    theme: "Theme", open: "Open",
  },
  clips: { title: "Clips", sermon: "Sermon", kind: "Kind", status: "Status", duration: "Length", why: "Reason", all: "All" },
  publications: { title: "Publishing", connector: "Connector", enabled: "Enabled", configured: "Configured" },
  settings: {
    title: "Settings",
    tabs: { mosque: "Mosque", logo: "Logo", live: "Live", folders: "Folders", model: "Model", connectors: "Publishing", access: "Access" },
    name: "Mosque name", timezone: "Time zone", clipTitle: "Clip title", orchestrator: "Orchestrator address",
    stateDir: "State folder", outputDir: "Video folder", publishDir: "Publishing folder",
    foldersHint: "The program's working folders.", defaultFolder: "Program's default folder", soon: "This tab arrives with the rest of the migration.",
    readonly: "Read-only for now: the write forms arrive with the rest of the React migration.",
  },
  users: { title: "Users", user: "Username", name: "Display name", role: "Role", active: "Active", last: "Last sign-in" },
  audit: { title: "Log", when: "When", who: "Who", action: "Action", object: "Object", ip: "Address" },
};

export const LANGUAGES = [
  { code: "fr", label: "Français", dir: "ltr" },
  { code: "en", label: "English", dir: "ltr" },
];

const choisi = typeof localStorage !== "undefined" ? localStorage.getItem("minbar.lang") : null;

i18n.use(initReactI18next).init({
  resources: { fr: { translation: fr }, en: { translation: en } },
  lng: choisi ?? "fr",
  fallbackLng: "fr",
  interpolation: { escapeValue: false },
});

export function setLanguage(code: string) {
  i18n.changeLanguage(code);
  localStorage.setItem("minbar.lang", code);
  const lang = LANGUAGES.find((l) => l.code === code);
  document.documentElement.lang = code;
  document.documentElement.dir = lang?.dir ?? "ltr";
}

export default i18n;
