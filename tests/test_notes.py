

def test_fenetres_du_modele_normalisees_puis_terminees():
    """Le modèle propose, le code cale : une borne au milieu d'une phrase est prolongée, pas jetée."""
    from minbar.pipeline import notes as N
    from minbar.pipeline import clips as C
    segs = [{"start": 0.0, "end": 6.0, "text": "Debut du discours complet."},
            {"start": 6.0, "end": 20.0, "text": "Une phrase importante qui continue"},
            {"start": 20.0, "end": 40.0, "text": "et se termine proprement ici."},
            {"start": 40.0, "end": 60.0, "text": "Passage suivant de la khutba."}]

    w = N.clips_from_model([{"start": "0:06", "end": "0:40", "why": "ok"}], segs)
    assert w and w[0]["start"] == 6.0 and w[0]["end"] == 40.0

    # deux extraits distincts sont conservés (plus qu'un clip par khutba)
    two = N.clips_from_model([{"start": "0:06", "end": "0:20"}, {"start": "0:45", "end": "1:00"}], segs)
    assert len(two) == 2

    # horodatage illisible, fenêtre minuscule, liste absente : rien
    assert N.clips_from_model([{"start": "0:00", "end": "0:06"}], segs) == []
    assert N.clips_from_model([{"start": "xx", "end": "0:40"}], segs) == []
    assert N.clips_from_model(None, segs) == []

    # une borne qui tombe en pleine phrase est prolongée jusqu'à la fin de cette phrase
    raw = N.clips_from_model([{"start": "0:06", "end": "0:20"}], segs)
    fin = C.refine_windows(raw, segs, 60.0)
    assert len(fin) == 1 and abs(fin[0]["end"] - 40.3) < 0.5, fin      # marge audio : +0,3 s
    assert abs(fin[0]["start"]) < 0.5, fin
