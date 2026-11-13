"""Le gabarit importé à l'antenne : c'est lui que la mosquée voit, pas le dessin du moteur.

Le fichier importé est une décoration, pas une figure imposée : la mosquée y met ses couleurs, sa
police, sa composition. Le moteur s'en sert donc comme fond d'écran réel, et ne garde son propre
dessin que si aucun gabarit n'a été importé (ou si le navigateur de rendu n'est pas disponible).

Deux règles tenues ici :

* les six écrans statiques sont rendus une fois par changement d'état, pas à chaque image ; seul
  l'entracte se redessine, une fois par seconde, parce que son décompte change ;
* un écran qui ne change pas n'est jamais redessiné, et une image identique n'est jamais réécrite
  sur le disque : le direct ne clignote pas et le disque ne travaille pas pour rien.
"""

from __future__ import annotations

import logging
import threading
import time

from .i18n import _

log = logging.getLogger("sada.gabarit_antenne")

# Les six écrans que le gabarit doit fournir, dans l'ordre du fichier.
ECRANS = ("avant", "priere", "entracte", "termine", "janaza", "dars_fin")

# Le bandeau du direct (scènes khutba et dars) est optionnel : un gabarit qui ne le fournit pas
# reste valide, et le moteur dessine alors son propre bandeau.
BANDEAUX = ("khutba", "dars")


class AntenneGabarit:
    """Rend les écrans du direct avec le fichier importé, quand la mosquée en a un.

    ``rendu`` est ce qui sait produire une image (une classe avec ``demarrer``, ``fermer`` et
    ``image(ecran, variables) -> bytes``). Il est construit paresseusement : un moteur sans gabarit
    n'ouvre jamais de navigateur.
    """

    def __init__(self, cfg, rendu_factory, fetch=None, log_fn=None):
        self.cfg = cfg
        self._rendu_factory = rendu_factory
        self._rendu = None
        self._fetch = fetch
        self.log = log_fn or (lambda _m: None)
        self.page: dict[str, str] | None = None  # une page par écran, décorée par le back-office
        self._variables: dict[str, str] = {}
        self._lock = threading.Lock()
        self._dernieres: dict[str, tuple[str, bytes]] = {}
        self._disponible = True  # faux après un échec : on ne s'acharne pas
        self._echecs = 0

    # ------------------------------------------------------------------ lecture
    @property
    def actif(self) -> bool:
        """Un gabarit existe et le rendu n'a pas encore échoué définitivement."""
        return bool(self.page) and self._disponible

    def charger(self, backoffice_url: str, api_port: int, fetch=None) -> bool:
        """Lit auprès du back-office le fichier importé et les textes de la mosquée.

        Rend ``True`` quand un gabarit s'applique. Un back-office muet n'est pas une erreur : le
        moteur garde son propre dessin et le dit une fois dans le journal.
        """
        if not backoffice_url:
            return False
        from urllib.parse import urlencode

        from .backoffice.internal import token
        from .screens import _http_get

        lire = fetch or self._fetch or _http_get
        url = backoffice_url.rstrip("/") + "/api/v1/internal/gabarit?" + urlencode({"port": api_port or 0})
        try:
            data = lire(url, token()) or {}
        except Exception as e:  # noqa: BLE001 — le direct n'attend pas le réseau
            self.log(_("Template: back office unreachable ({error}) — keeping the engine drawing", error=e))
            return False
        pages = data.get("pages") or {}
        if not pages:
            self.log(_("Template: no file imported — keeping the engine drawing"))
            self.page = None
            return False
        self.page = pages
        self._variables = data.get("variables") or {}
        self.log(_("Template: {name} — this file will be shown on air", name=data.get("nom") or "imported"))
        banners = [e for e in BANDEAUX if pages.get(e)]
        if banners:
            self.log(_("Template: the file also provides the live banner ({screens})", screens=", ".join(banners)))
        return True

    # ------------------------------------------------------------------- rendu
    # quel texte arabe saisir pour tel écran du gabarit
    ARABE = {
        "avant": "arabe_khutba",
        "priere": "arabe_priere",
        "janaza": "arabe_janaza",
        "dars_fin": "arabe_dars",
        "termine": "",
        "entracte": "",
        "khutba": "arabe_khutba",
        "dars": "arabe_dars",
    }

    def valeurs(self, ecran: str, moment=None) -> dict[str, str]:
        """Les valeurs à poser dans la page : heure, nom de la mosquée, ligne arabe de cet écran.

        Aucune ligne arabe n'est inventée : si la mosquée n'en a pas saisi, la place reste vide.
        Les textes fixes de l'écran (titre, sous-titre, pied) viennent du fichier lui-même.
        Pour le bandeau (khutba, dars), les textes de la séance en cours sont aussi fournis :
        titre, deuxième ligne, orateur, série et séance.
        """
        valeurs = {
            "mosquee": self._variables.get("nom") or getattr(self.cfg, "mosque_name", "") or "",
            "logo": self._variables.get("logo", ""),
            "ligne2": "",
            "orateur": "",
            "serie": "",
            "seance": "",
        }
        nxt = getattr(moment, "next_event", None) if moment is not None else None
        planned = getattr(moment, "planned", None) if moment is not None else None
        valeurs["heure"] = f"{planned:%H} h {planned:%M}" if planned else ""
        valeurs["cours"] = getattr(nxt, "title", "") or ""
        valeurs["titre"] = getattr(self.cfg, "title", "") or ""
        slot = self.ARABE.get(ecran, "")
        valeurs["arabe"] = self._variables.get(slot, "") if slot else ""
        if ecran in BANDEAUX:
            ev = getattr(moment, "event", None) if moment is not None else None
            if ecran == "khutba":
                valeurs["titre"] = getattr(self.cfg, "title", "") or ""
                valeurs["ligne2"] = valeurs["mosquee"]
            else:  # dars
                if ev is None:
                    valeurs["titre"] = "Dars"
                    valeurs["ligne2"] = valeurs["mosquee"]
                else:
                    valeurs["titre"] = ev.get("title") or "Dars"
                    bits = [valeurs["mosquee"]] if valeurs["mosquee"] else []
                    if ev.get("speaker"):
                        valeurs["orateur"] = ev["speaker"]
                        bits.append(ev["speaker"])
                    no = ev.get("session_no")
                    if ev.get("series"):
                        valeurs["serie"] = ev["series"]
                        valeurs["seance"] = str(no) if no else ""
                        bits.append(ev["series"] + (f" · séance {no}" if no else ""))
                    valeurs["ligne2"] = "  ·  ".join(bits)
        return valeurs

    def _rendre(self, ecran: str, moment=None) -> bytes:
        if self._rendu is None:
            self._rendu = self._rendu_factory(self.cfg)
            self._rendu.start()
        page = (self.page or {}).get(ecran, "")
        return self._rendu.image(page, self.valeurs(ecran, moment), omit_background=ecran in BANDEAUX)

    def image(self, ecran: str, moment=None) -> bytes | None:
        """Image 1280×720 de l'écran, ou ``None`` : l'appelant affiche alors le dessin du moteur.

        Une image déjà rendue pour cet écran est réutilisée telle quelle : le direct ne redessine
        pas ce qui n'a pas changé. Le bandeau (khutba, dars) reflète la séance en cours : un
        événement différent le redessine.
        """
        if not self.actif or (ecran not in ECRANS and ecran not in BANDEAUX):
            return None
        if not (self.page or {}).get(ecran):
            return None  # écran absent du fichier : on ne devine pas
        valeurs = self.valeurs(ecran, moment)
        with self._lock:
            signature = self.variable_de_l_instant(ecran)
            if ecran in BANDEAUX:
                signature += "|" + repr(sorted(valeurs.items()))
            deja = self._dernieres.get(ecran)
            if deja and deja[0] == signature:
                return deja[1]
        debut = time.monotonic()
        try:
            png = self._rendre(ecran, moment)
        except Exception as e:  # noqa: BLE001 — un écran raté ne coupe pas le direct
            self._echecs += 1
            self.log(_("Template: cannot render screen {screen} ({error})", screen=ecran, error=e))
            if self._echecs >= 3:
                self._disponible = False
                self.log(_("Template: three failures in a row — back to the engine drawing for this live stream"))
            return None
        self._echecs = 0
        duree = (time.monotonic() - debut) * 1000
        if duree > 600:
            self.log(
                _("Template: screen {screen} rendered in {duration:.0f} ms (over budget)", screen=ecran, duration=duree)
            )
        with self._lock:
            self._dernieres[ecran] = (signature, png)
        return png

    def variable_de_l_instant(self, ecran: str) -> str:
        """Ce qui, dans les variables, doit provoquer un nouveau rendu pour cet écran."""
        if ecran == "entracte":
            return time.strftime("%H:%M:%S")
        return ""

    def fermer(self) -> None:
        if self._rendu is not None:
            try:
                self._rendu.close()
            except Exception:  # noqa: BLE001 — on ferme, coûte que coûte
                pass
            self._rendu = None


def depuis_backoffice(cfg, backoffice_url: str, api_port: int, log_fn=None):
    """L'antenne du gabarit quand la mosquée en a importé un, sinon ``None``.

    Import tardif pour ne pas charger le module de rendu (ni son navigateur) dans un moteur qui
    n'affiche que le dessin par défaut.
    """
    try:
        from .backoffice.template_render import TemplateRender
    except Exception as e:  # noqa: BLE001 — pas de navigateur : on dessine
        log_fn = log_fn or (lambda _m: None)
        log_fn(_("Template: browser rendering unavailable ({error}) — keeping the engine drawing", error=e))
        return None
    antenne = AntenneGabarit(cfg, lambda _cfg: TemplateRender(), log_fn=log_fn)
    if antenne.charger(backoffice_url, api_port):
        return antenne
    return None
