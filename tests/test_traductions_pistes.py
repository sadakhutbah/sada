"""Pistes bilingues complètes : une piste de langue X couvre TOUT le sermon dit en X.

La piste `fr` = paroles françaises telles quelles + tout l'arabe traduit ; la piste `ar` = paroles
arabes telles quelles + tout le français traduit. Les versets gardent le texte coranique exact et la
traduction Hamidullah, jamais un modèle. Les caches sur disque évitent toute retraduction.
"""

from __future__ import annotations

import json
import os

import pytest

from sada.pipeline import subtitles as s
from sada.pipeline import translate

FR1 = "Chers frères, la patience est une lumière."
FR2 = "Qu'Allah nous accorde la patience."
AR = "ولا حول ولا قوة إلا بالله"
VERSE = "ومن يتوكل على الله فهو حسبه"
VERSE_FR = "Et quiconque place sa confiance en Allah, Il lui suffit."


def _segments() -> list[dict]:
    return [
        {"start": 0.0, "end": 4.0, "text": FR1},
        {"start": 4.0, "end": 9.0, "text": AR},
        {"start": 9.0, "end": 14.0, "text": VERSE},
        {"start": 14.0, "end": 20.0, "text": FR2},
    ]


def _verset() -> list[dict]:
    return [
        {
            "ref": "65:3",
            "start": 9.0,
            "end": 14.0,
            "heard": VERSE,
            "verse_text": VERSE,
            "verse_fr": VERSE_FR,
        }
    ]


def test_piste_fr_ajoute_les_passages_arabes_traduits():
    tr = {
        "seg": {translate._seg_key(AR): {"fr": "Ni force ni puissance si ce n'est par Allah.", "coran": False}},
        "verse_fr": {},
    }
    pistes = s.tracks(_segments(), _verset(), tr)
    assert [p["text"] for p in pistes["fr"]] == [
        FR1,
        "Ni force ni puissance si ce n'est par Allah.",
        VERSE_FR,
        FR2,
    ]
    # le passage traduit garde l'intervalle de son segment source
    assert (pistes["fr"][1]["start"], pistes["fr"][1]["end"]) == (4.0, 9.0)


def test_piste_ar_ajoute_les_passages_francais_traduits():
    ar = {"seg": {translate._seg_key(FR1): {"ar": "أيها الإخوة، الصبر نور."}}}
    pistes = s.tracks(_segments(), _verset(), {"seg": {}, "verse_fr": {}}, ar)
    assert [p["text"] for p in pistes["ar"]] == ["أيها الإخوة، الصبر نور.", AR, VERSE]


def test_une_langue_nest_complete_que_si_tout_est_traduit():
    couverture = s.coverage(_segments(), _verset(), {"seg": {}, "verse_fr": {}})
    # fr : les trois passages français + le verset ; ar : le seul passage arabe
    assert couverture["fr"] == {"done": 3, "total": 4, "complete": False}
    assert couverture["ar"] == {"done": 2, "total": 4, "complete": False}

    tr = {"seg": {translate._seg_key(AR): {"fr": "Ni force ni puissance...", "coran": False}}, "verse_fr": {}}
    ar = {"seg": {k: {"ar": "…"} for k in (translate._seg_key(FR1), translate._seg_key(FR2))}}
    assert s.coverage(_segments(), _verset(), tr, ar) == {
        "ar": {"done": 4, "total": 4, "complete": True},
        "fr": {"done": 4, "total": 4, "complete": True},
    }


def test_verset_texte_coranique_et_traduction_de_reference_jamais_le_modele():
    # une traduction de modèle qui traînerait en cache ne doit pas écraser le verset
    tr = {"seg": {translate._seg_key(VERSE): {"fr": "TRADUCTION DU MODELE", "coran": True}}, "verse_fr": {}}
    pistes = s.tracks(_segments(), _verset(), tr)
    assert [p["text"] for p in pistes["fr"] if p["start"] == 9.0] == [VERSE_FR]
    assert [p["text"] for p in pistes["ar"] if p["start"] == 9.0] == [VERSE]


def test_traduction_fr_vers_ar_avec_cache_et_sans_versets(tmp_path, monkeypatch):
    cache = tmp_path / "translations_ar.json"
    promptes: list[str] = []

    def ask(prompt, timeout=600, log=print):
        promptes.append(prompt)
        return {
            "seg": {
                "0": {"ar": "أيها الإخوة، الصبر نور."},
                "3": {"ar": "نسأل الله أن يرزقنا الصبر."},
            }
        }

    monkeypatch.setattr(translate.llm, "ask_json", ask)
    res = translate.translate_french(_segments(), _verset(), cache, log=lambda m: None)
    assert len(promptes) == 1  # un seul appel pour tout le sermon
    assert VERSE not in promptes[0]  # le verset n'est jamais traduit par le modèle
    assert res["seg"][translate._seg_key(FR1)]["ar"] == "أيها الإخوة، الصبر نور."
    assert len(res["seg"]) == 2

    promptes.clear()
    monkeypatch.setattr(translate.llm, "ask_json", lambda *a, **k: promptes.append(a[0]) or {})
    translate.translate_french(_segments(), _verset(), cache, log=lambda m: None)
    assert promptes == []  # cache complet : aucune retraduction


def test_traduction_fr_vers_ar_par_lots(tmp_path, monkeypatch):
    segments = [{"start": float(i), "end": float(i + 1), "text": f"Phrase française numéro {i}."} for i in range(5)]
    appels: list[str] = []

    def ask(prompt, timeout=600, log=print):
        appels.append(prompt)
        return {"seg": {}}

    monkeypatch.setattr(translate.llm, "ask_json", ask)
    translate.translate_french(segments, [], tmp_path / "ar.json", log=lambda m: None, batch=2)
    assert len(appels) == 4  # 2 + 2 + 1, puis un seul rattrapage pour les cinq passages
    assert "Phrase française numéro 4." in appels[2]
    assert "Phrase française numéro 0." not in appels[2]
    assert "Phrase française numéro 0." in appels[3] and "Phrase française numéro 4." in appels[3]


def test_une_omission_du_modele_est_rattrapee_une_fois(tmp_path, monkeypatch):
    """Le modèle saute un index : un second appel, sur ce seul passage, complète la piste arabe."""
    cache = tmp_path / "translations_ar.json"
    appels: list[str] = []

    def ask(prompt, timeout=600, log=print):
        appels.append(prompt)
        if len(appels) == 1:
            return {"seg": {"0": {"ar": "أيها الإخوة، الصبر نور."}}}  # le passage 3 manque
        return {"seg": {"3": {"ar": "نسأل الله أن يرزقنا الصبر."}}}

    monkeypatch.setattr(translate.llm, "ask_json", ask)
    res = translate.translate_french(_segments(), _verset(), cache, log=lambda m: None)
    assert len(appels) == 2
    assert FR2 in appels[1] and FR1 not in appels[1]  # le rattrapage ne renvoie que le passage perdu
    assert len(res["seg"]) == 2  # les deux passages français sont là


def test_traduction_des_deux_pistes_et_progression(tmp_path, monkeypatch):
    monkeypatch.setattr(
        translate.llm,
        "ask_json",
        lambda prompt, timeout=600, log=print: {"seg": {}},
    )
    avancement: list[float] = []
    fr, ar = translate.translate_tracks(
        _segments(),
        _verset(),
        tmp_path / "translations.json",
        tmp_path / "translations_ar.json",
        progress=avancement.append,
    )
    assert fr["verse_fr"]["65:3@9.0"] == VERSE_FR  # le verset reste la référence, sans modèle
    assert isinstance(ar, dict)
    assert avancement and avancement[-1] == 1.0


def test_traduction_sans_modele_ne_maquille_rien(tmp_path):
    cache = tmp_path / "translations_ar.json"
    res = translate.translate_french(_segments(), _verset(), cache, use_llm=False, log=lambda m: None)
    assert res["seg"] == {}
    assert json.loads(cache.read_text())["seg"] == {}


# ------------------------------------------------------------------ API


fastapi = pytest.importorskip("fastapi")
from test_backoffice import client  # noqa: E402


def _ecrire_etat(env, **champs) -> None:
    from sada.pipeline.output import SessionOutput

    o = SessionOutput(env["k"]["output_dir"])
    o.translation_job.write_text(json.dumps(champs), encoding="utf-8")


def _khutba(env) -> dict:
    from sada.backoffice import db

    return db.one(env["conn"], "SELECT * FROM khutbas WHERE id = ?", env["kid"])


def test_api_etat_des_pistes(env):
    env["k"] = _khutba(env)
    c = client(env, "admin")
    r = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres")
    assert r.status_code == 200
    corps = r.json()
    # aucun français traduit en arabe dans la fixture : l'arabe n'est pas complet
    assert corps["languages"]["ar"]["complete"] is False
    assert corps["languages"]["ar"]["done"] == 1
    assert corps["languages"]["fr"]["complete"] is True
    assert corps["translation"]["status"] == ""


def test_api_traduire_puis_piste_arabe_complete(env, monkeypatch):
    env["k"] = _khutba(env)

    def ask(prompt, timeout=600, log=print):
        return {
            "seg": {"0": {"ar": "أيها الإخوة، الصبر نور."}, "1": {"ar": "الصبر نور."}, "3": {"ar": "نسأل الله الصبر."}}
        }

    monkeypatch.setattr(translate.llm, "ask_json", ask)
    c = client(env, "admin")
    r = c.ecrire(f"/sermons/{env['kid']}/habillage/sous-titres")
    assert r.status_code == 200, r.text
    etat = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres").json()
    assert etat["languages"]["ar"]["complete"] is True
    assert etat["translation"]["status"] == "pret"

    _habille(env)
    ar = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/ar.srt").content.decode("utf-8")
    assert "أيها الإخوة، الصبر نور." in ar  # le français traduit entre dans la piste arabe


def _habille(env) -> None:
    """Vidéo habillée en place (fichier + statut), sans passer par ffmpeg : les .srt l'exigent."""
    from sada.backoffice import habillage as bo

    k = _khutba(env)
    bo.dossier(k).mkdir(parents=True, exist_ok=True)
    bo.fichier_video(k).write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
    bo.fichier_options_rendues(k).write_text(
        json.dumps({"title": "Patience", "speaker": "Cheikh Test", "intro_s": 5.0, "outro_s": 2}), encoding="utf-8"
    )
    env["conn"].execute(
        "INSERT INTO habillages (mosque_id, khutba_id, options, status, updated_at) VALUES (?, ?, '{}', 'pret', 'x')",
        (env["mid"], k["id"]),
    )


def test_api_srt_repond_pendant_la_traduction_et_dit_l_etat(env):
    env["k"] = _khutba(env)
    _habille(env)
    _ecrire_etat(env, status="en_cours", progress=0.4, pid=os.getpid())
    c = client(env, "imam")
    fr = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.srt")
    assert fr.status_code == 200
    assert fr.headers["X-Sada-Subtitles-Complete"] == "1"  # le français est entier
    assert "la patience" in fr.content.decode("utf-8")

    # piste encore partielle : servie tout de suite, avec son compte de passages
    ar = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/ar.srt")
    assert ar.status_code == 200
    assert ar.headers["X-Sada-Subtitles-Complete"] == "0"
    assert int(ar.headers["X-Sada-Subtitles-Done"]) >= 1
    assert int(ar.headers["X-Sada-Subtitles-Total"]) > int(ar.headers["X-Sada-Subtitles-Done"])

    # un « en cours » dont le processus a disparu est un échec, pas une attente éternelle
    _ecrire_etat(env, status="en_cours", progress=0.4, pid=999999)
    etat = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres").json()
    assert etat["translation"]["status"] == "echec"


def test_api_traduction_refusee_en_lecture_seule(env):
    c = client(env, "imam")
    r = c.ecrire(f"/sermons/{env['kid']}/habillage/sous-titres")
    assert r.status_code in (403, 401)
