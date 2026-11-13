"""Sous-titres .srt du long format : horodatage, décalage du carton, découpe des lignes, endpoint."""

from __future__ import annotations

import json

import pytest

from sada.pipeline import subtitles as s

VERSE = "ومن يتوكل على الله فهو حسبه"
LONG = (
    "Chers frères, la patience est une lumière qui éclaire le chemin de celui qui croit et qui espère, "
    "et elle se cultive jour après jour dans l'épreuve comme dans la facilité."
)


def test_horodatage_srt():
    assert s.timestamp(0) == "00:00:00,000"
    assert s.timestamp(3725.4) == "01:02:05,400"
    assert s.timestamp(9.0004) == "00:00:09,000"
    assert s.timestamp(59.9996) == "00:01:00,000"  # arrondi au millième, pas 59,1000
    assert s.timestamp(-3) == "00:00:00,000"


def test_decalage_du_carton():
    passage = [{"start": 2.0, "end": 4.0, "text": "La patience est une lumière."}]
    assert s.cues(passage)[0]["start"] == 2.0
    decale = s.cues(passage, shift=5.5)
    assert decale[0]["start"] == 7.5 and abs(decale[0]["end"] - 9.5) < 1e-6
    assert "00:00:07,500 --> 00:00:09,500" in s.srt(decale)


def test_debut_de_rendu_avant_le_zero():
    # passage coupé par le début de la vidéo : rien à afficher à 00:00:00 par erreur
    assert s.cues([{"start": 1.0, "end": 3.0, "text": "Un début coupé."}], shift=-5) == []


def test_lignes_de_42_caracteres_deux_maximum():
    pages = s.cues([{"start": 0.0, "end": 12.0, "text": LONG}])
    assert len(pages) >= 2  # le segment long est découpé en plusieurs sous-titres
    for cue in pages:
        assert 1 <= len(cue["lines"]) <= 2
        assert all(len(line) <= s.WIDTH for line in cue["lines"]), cue["lines"]
    # l'ordre et la couverture sont conservés
    assert abs(pages[0]["start"] - 0.0) < 1e-6
    assert abs(pages[-1]["end"] - 12.0) < 0.5
    assert all(a["start"] <= a["end"] <= b["start"] + 1e-6 for a, b in zip(pages, pages[1:], strict=False))
    texte = " ".join(" ".join(cue["lines"]) for cue in pages)
    for mot in LONG.replace(",", "").split():
        assert mot in texte


def test_duree_minimale_et_maximale():
    lent = [{"start": 0.0, "end": 24.0, "text": "Une phrase dite très lentement, mot après mot."}]
    denses = [{"start": 0.0, "end": 1.0, "text": " ".join(["mot"] * 40)}]
    for cue in s.cues(lent) + s.cues(denses):
        assert 0.15 <= cue["end"] - cue["start"] <= s.MAX_DURATION + 1e-6


def test_pas_deux_sous_titres_a_l_ecran():
    passages = [
        {"start": 0.0, "end": 4.0, "text": "Première phrase courte."},
        {"start": 3.5, "end": 6.0, "text": "Deuxième phrase qui chevauche la première."},
    ]
    cues = s.cues(passages)
    assert all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:], strict=False))


def test_mots_horodates_de_whisper_utilises():
    passage = {
        "start": 0.0,
        "end": 10.0,
        "text": "Premier mot puis second",
        "words": [
            {"w": "Premier", "s": 1.0, "e": 1.6},
            {"w": "mot", "s": 1.6, "e": 2.0},
            {"w": "puis", "s": 6.0, "e": 6.4},
            {"w": "second", "s": 6.4, "e": 7.0},
        ],
    }
    cues = s.cues([passage])
    assert [cue["start"] for cue in cues] == [1.0, 6.0]  # la pause de 4 s sépare deux sous-titres


def test_format_srt_et_arabe():
    cues = s.cues([{"start": 0.0, "end": 3.0, "text": VERSE}])
    texte = s.srt(cues)
    blocs = texte.strip().split("\n\n")
    assert blocs[0].splitlines()[0] == "1"
    assert blocs[0].splitlines()[1] == "00:00:00,000 --> 00:00:03,000"
    assert blocs[0].splitlines()[2] == VERSE
    assert texte.encode("utf-8").decode("utf-8") == texte  # UTF-8, rien ne se perd
    assert "<i>" not in s.srt(s.cues([{"start": 0.0, "end": 2.0, "text": "un <i>mot</i>"}]))


def test_pistes_arabe_et_francais():
    from sada.pipeline import verses

    segments = [
        {"start": 0.0, "end": 4.0, "text": "Chers frères, parlons de la patience."},
        {"start": 4.0, "end": 9.0, "text": VERSE},
        {"start": 9.0, "end": 12.0, "text": "ولا حول ولا قوة إلا بالله"},
    ]
    matches = verses.shown(
        [
            {
                "ref": "65:3",
                "start": 4.0,
                "end": 9.0,
                "heard": VERSE,
                "verse_fr": "Et quiconque place sa confiance en Allah, Il lui suffit.",
            }
        ]
    )
    pistes = s.tracks(segments, matches, {"seg": {}, "verse_fr": {}})
    assert list(pistes) == ["ar", "fr"]
    assert [p["text"] for p in pistes["ar"]] == [
        VERSE,
        "ولا حول ولا قوة إلا بالله",
    ]  # le français parlé n'entre pas dans l'arabe
    assert [p["text"] for p in pistes["fr"]] == [
        "Chers frères, parlons de la patience.",
        "Et quiconque place sa confiance en Allah, Il lui suffit.",
    ]  # passage arabe sans traduction : absent du français plutôt qu'approximé


# ------------------------------------------------------------------ endpoint


fastapi = pytest.importorskip("fastapi")
from test_backoffice import client  # noqa: E402


def habille(env, intro_s: float = 5.0) -> None:
    """Vidéo habillée en place : le fichier et les options du rendu, sans passer par ffmpeg."""
    from sada.backoffice import db
    from sada.backoffice import habillage as bo

    k = db.one(env["conn"], "SELECT * FROM khutbas WHERE id = ?", env["kid"])
    bo.dossier(k).mkdir(parents=True, exist_ok=True)
    bo.fichier_video(k).write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes(64))
    bo.fichier_options_rendues(k).write_text(
        json.dumps({"title": "Patience", "speaker": "Cheikh Test", "intro_s": intro_s, "outro_s": 2}),
        encoding="utf-8",
    )
    env["conn"].execute(
        "INSERT INTO habillages (mosque_id, khutba_id, options, status, updated_at) VALUES (?, ?, '{}', 'pret', 'x')",
        (env["mid"], env["kid"]),
    )


def test_api_srt_par_langue(env):
    c = client(env, "admin")
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.srt").status_code == 404
    habille(env)

    etat = c.get(f"/api/v1/sermons/{env['kid']}/habillage").json()
    assert etat["video_ready"] is True and etat["subtitles"] == ["ar", "fr"]

    fr = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.srt")
    assert fr.status_code == 200, fr.text
    assert "attachment" in fr.headers["content-disposition"]
    assert "sous-titres-fr.srt" in fr.headers["content-disposition"]
    assert str(env["tmp"]) not in fr.headers["content-disposition"]
    corps = fr.content.decode("utf-8")
    assert corps.startswith("1\n00:00:05,000 -->")  # carton d'ouverture de 5 s
    assert "la patience" in corps and "Et quiconque place sa confiance" in corps

    ar = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/ar.srt").content.decode("utf-8")
    assert VERSE in ar and "00:00:14,000 -->" in ar  # le verset, décalé du carton

    brut = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.srt?brut=1").content.decode("utf-8")
    assert brut.startswith("1\n00:00:00,000 -->")  # version alignée sur la vidéo d'origine
    assert "00:00:05,000" not in brut


def test_api_srt_langue_inconnue(env):
    c = client(env, "admin", headers={"X-Sada-Lang": "fr"})
    habille(env)
    r = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/de.srt")
    assert r.status_code == 422 and "cette langue" in r.text


def test_api_srt_lecture_seule_autorisee(env):
    c = client(env, "imam")
    habille(env)
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.srt").status_code == 200


def test_decoupage_et_carton_dans_une_vraie_session(env):
    """Le carton se lit dans les options du rendu, la coupe dans la position de la session."""
    from sada.backoffice import db
    from sada.backoffice import habillage as bo

    habille(env, intro_s=5)
    rec = env["tmp"] / "enregistrement.mkv"
    rec.write_bytes(b"x")
    k = dict(db.one(env["conn"], "SELECT * FROM khutbas WHERE id = ?", env["kid"]))
    k.update(video_path=None, recording=str(rec), start_pos=100.0, end_pos=120.0)
    assert bo.alignements(env["conn"], k) == {"habille": -95.0, "brut": -100.0}


# ------------------------------------------------------------------ WebVTT (lu par le lecteur)


def test_format_vtt():
    texte = s.vtt(s.cues([{"start": 0.0, "end": 3.0, "text": "La patience est une lumière."}]))
    lignes = texte.splitlines()
    assert lignes[0] == "WEBVTT"
    assert lignes[1] == ""
    assert lignes[2] == "00:00:00.000 --> 00:00:03.000"  # point décimal, heure pleine
    assert lignes[3] == "La patience est une lumière."
    assert "," not in lignes[2]  # jamais l'horodatage .srt dans un .vtt
    assert not lignes[2].strip().isdigit()  # pas de numéro de bloc
    assert texte.endswith("\n")


def test_vtt_arabe_en_droite_a_gauche():
    texte = s.vtt(s.cues([{"start": 0.0, "end": 2.0, "text": VERSE}]))
    assert f"\u202b{VERSE}\u202c" in texte


def test_vtt_vide_reste_valide():
    # Rien à afficher dans la vidéo : un en-tête seul, jamais un fichier cassé.
    assert s.vtt([]) == "WEBVTT\n"


def test_api_vtt_en_ligne_des_la_transcription(env):
    """Pas besoin de vidéo habillée : la transcription suffit, et le fichier se lit sur place."""
    c = client(env, "admin")
    assert c.get(f"/api/v1/sermons/{env['kid']}").json()["subtitles"] == ["ar", "fr"]

    r = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.vtt?brut=1")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/vtt")
    assert r.headers["content-disposition"] == "inline"
    corps = r.content.decode("utf-8")
    assert corps.startswith("WEBVTT\n\n00:00:00.000 -->")
    assert "la patience" in corps


def test_api_vtt_suit_la_video_choisie(env):
    c = client(env, "admin")
    brut = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.vtt?brut=1").text
    assert "00:00:05.000 -->" not in brut

    habille(env)
    fr = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.vtt").text
    assert fr.splitlines()[2].startswith("00:00:05.000 -->")  # carton d'ouverture de 5 s
    ar = c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/ar.vtt").text
    assert VERSE in ar
    # `brut` reste aligné sur la vidéo d'origine, même une fois la vidéo habillée en place
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.vtt?brut=1").text == brut


def test_api_vtt_sans_transcription_est_introuvable(env):
    from sada.backoffice import db
    from sada.pipeline.output import SessionOutput

    k = db.one(env["conn"], "SELECT * FROM khutbas WHERE id = ?", env["kid"])
    assert k
    SessionOutput(k["output_dir"]).transcript.unlink()
    c = client(env, "admin")
    assert c.get(f"/api/v1/sermons/{env['kid']}/habillage/sous-titres/fr.vtt").status_code == 404
    assert c.get(f"/api/v1/sermons/{env['kid']}").json()["subtitles"] == []
