"""Sélection des extraits (sada.pipeline.select) : candidats par le code, notation, sélection espacée."""

from sada.pipeline import select as S


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


def _khutba_propre(n=12):
    """Transcript artificiel : phrases françaises propres de 7 s, chacune terminée par un point."""
    return [seg(i * 8.0, i * 8.0 + 7.0, "Voici une phrase claire du discours qui se termine ici.") for i in range(n)]


def test_candidats_fenetres_propres_non_chevauchantes():
    segs = _khutba_propre(12)
    wins = S.candidate_windows(segs, 96.0, log=lambda *a: None)
    assert [w["start"] for w in wins] == [0.0, 32.0, 64.0], wins
    for w in wins:
        assert 30.0 <= w["end"] - w["start"] <= 75.0, w
        assert S.clean_window_text(segs, w["start"], w["end"]).rstrip().endswith(".")
    # pas de chevauchement, ordre chronologique
    for a, b in zip(wins, wins[1:], strict=False):
        assert a["end"] <= b["start"] + 1.0


def test_ancres_propres_seulement():
    """Jamais d'ancrage sur « Et »/« Donc » en pleine phrase, l'arabe ou une suite phonétique.

    Après une récitation arabe, le propos est refermé : on tolère alors une conjonction
    (« Et on ramène… »), sinon on manque des récits dont l'introduction française commence par « et »."""
    segs = [
        seg(0, 5, "Voici une introduction correcte du discours."),
        seg(5, 10, "Et cette phrase commence par une conjonction."),
        seg(10, 15, "qala anna inna qawluhu walha lahwin, un passage."),
        seg(15, 20, "أعوذ بالله من الشيطان الرجيم"),
        seg(20, 25, "Et on ramène celui qui était le plus malheureux."),
        seg(25, 30, "Seconde phrase propre qui termine le propos."),
    ]
    ancres = {segs[i]["start"] for i in S._anchor_indexes(segs)}
    assert 0.0 in ancres
    assert 25.0 in ancres
    assert 5.0 not in ancres  # « Et » en pleine phrase
    assert 10.0 not in ancres  # phonétique
    assert 15.0 not in ancres  # arabe
    assert 20.0 in ancres  # « Et » après une récitation : le propos est refermé


def test_nettoyage_phonetique_et_mot_isole():
    # suite phonétique retirée, le français qui la traduit reste
    assert S.clean_fr_text("qala anna inna qawluhu, il oublie.") == "il oublie."
    # un mot isolé (glose) reste
    assert "dunya" in S.clean_fr_text("Le mot dunya est important.")
    # espace avant ponctuation réglé
    assert S.clean_fr_text("une phrase .") == "une phrase."


def test_parsing_des_notes_robuste():
    d = {"extraits": [{"note": "7", "titre": "ok"}, {"note": 9, "titre": "autre"}, {"titre": "sans note"}]}
    assert S._parse_scores(d) == [(7.0, "ok"), (9.0, "autre"), None]
    assert S._parse_scores({"extraits": "pas une liste"}) is None
    assert S._parse_scores({"autre": []}) is None
    # bornes 0..10
    assert S._parse_scores({"extraits": [{"note": 15}, {"note": -3}]}) == [(10.0, ""), (0.0, "")]


def test_selection_espacee_sans_chevauchement():
    scored = [
        {"kind": "extrait", "start": 0.0, "end": 40.0, "note": 9.0, "titre": "a"},
        {"kind": "extrait", "start": 20.0, "end": 60.0, "note": 8.0, "titre": "b"},
        {"kind": "extrait", "start": 100.0, "end": 140.0, "note": 7.0, "titre": "c"},
        {"kind": "extrait", "start": 120.0, "end": 160.0, "note": 10.0, "titre": "d"},
    ]
    wins = S.pick_clips(scored, 2)
    assert [w["start"] for w in wins] == [0.0, 120.0]  # meilleures notes, espacées, chronologiques
    assert wins[1]["why"] == "d"  # le titre sert de « pourquoi »


def test_repli_sans_modele(monkeypatch):
    segs = _khutba_propre(20)
    # sans LLM : note heuristique seule
    scored = S.score_candidates(
        S.candidate_windows(segs, 160.0, log=lambda *a: None), segs, use_llm=False, log=lambda *a: None
    )
    assert scored and all(w["method"] == "heuristique" and w["note"] > 0 for w in scored)

    logs = []
    wins = S.select_clips(segs, 160.0, 3, use_llm=False, log=logs.append)
    assert 1 <= len(wins) <= 3
    assert all(w["method"] == "heuristique" for w in wins)
    assert any("candidates" in ligne for ligne in logs) and any("kept" in ligne for ligne in logs)

    # modèle indisponible : chaque lot retombe sur l'heuristique, rien ne lève
    monkeypatch.setattr(S.llm, "ask_json", lambda prompt, timeout, log: None)
    scored2 = S.score_candidates(
        S.candidate_windows(segs, 160.0, log=lambda *a: None), segs, use_llm=True, log=lambda *a: None
    )
    assert scored2 and all(w["method"] == "heuristique" for w in scored2)
