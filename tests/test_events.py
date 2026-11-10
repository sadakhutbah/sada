"""Program (events): recurrence expansion, validation, upcoming occurrences."""
import datetime as dt
import json

import pytest

from minbar import events as ev
from minbar.state import Machine, MachineParams


def D(y, m, d):
    return dt.date(y, m, d)


PROG = {
    "events": [
        {"kind": "dars", "title": "Tafsir Al-Kahf", "speaker": "Imam Abdallah",
         "series": "Tafsir Al-Kahf", "session": 7,
         "recurrence": {"weekday": "mardi", "after": "isha", "offset_min": 15}, "max_min": 90},
        {"kind": "conference", "title": "Les droits des voisins", "speaker": "Cheikh X",
         "date": "2026-11-21", "time": "18:30"},
        {"kind": "dars", "title": "Fiqh du quotidien", "publish": False,
         "recurrence": {"weekday": "jeudi", "time": "19:00"}},
    ]
}

TIMES = {"dhuhr": "13:15", "asr": "15:40", "maghrib": "17:10", "isha": "18:45"}


def prog():
    return ev.loads(json.dumps(PROG))


def test_recurrence_apres_isha():
    dars = prog()[0]
    mardi = D(2026, 11, 10)          # a Tuesday
    inst = dars.instance(mardi, TIMES)
    assert inst is not None
    assert inst.starts == dt.datetime(2026, 11, 10, 19, 0)   # isha 18:45 + 15 min
    assert inst.label == "Dars : Tafsir Al-Kahf"
    assert inst.as_event()["session_no"] == 7
    assert dars.instance(D(2026, 11, 11), TIMES) is None      # Wednesday: nothing


def test_instance_porte_la_forme_canonique_de_l_evenement():
    """as_event() feeds the machine: the session number from the file arrives as-is in sessions.json."""
    inst = prog()[0].instance(D(2026, 11, 10), TIMES)
    assert inst is not None
    m = Machine([inst.starts], MachineParams(), extras=[inst.as_event()])
    rec = m.sessions_dicts()[0]
    assert (rec["kind"], rec["title"], rec["series"], rec["session_no"]) == \
        ("dars", "Tafsir Al-Kahf", "Tafsir Al-Kahf", 7)
    assert rec["speaker"] == "Imam Abdallah"


def test_recurrence_heure_absolue():
    jeudi = prog()[2]
    inst = jeudi.instance(D(2026, 11, 12), TIMES)
    assert inst is not None
    assert inst.starts == dt.datetime(2026, 11, 12, 19, 0)


def test_evenement_unique():
    conf = prog()[1]
    inst = conf.instance(D(2026, 11, 21), TIMES)
    assert inst is not None and inst.starts == dt.datetime(2026, 11, 21, 18, 30)
    assert conf.instance(D(2026, 11, 22), TIMES) is None


def test_heure_relative_sans_horaire_de_priere():
    dars = prog()[0]
    assert dars.starts_on(D(2026, 11, 10), {}) is None          # Mawaqit silent: no invented time


def test_non_publies_ignores():
    today = D(2026, 11, 12)                                      # Thursday
    assert [i.title for i in ev.instances_on(prog(), today, TIMES)] == []


def test_instances_triees_et_prochaines():
    day = D(2026, 11, 10)                                        # Tuesday
    inst = ev.instances_on(prog(), day, TIMES)
    assert [i.kind for i in inst] == ["dars"]
    # from Tuesday the 10th at noon: the evening dars, then the one on the 17th (the conference is later)
    nxt = ev.next_instances(prog(), dt.datetime(2026, 11, 10, 12, 0), count=2,
                            times_for=lambda d: TIMES)
    assert [i.title for i in nxt] == ["Tafsir Al-Kahf", "Tafsir Al-Kahf"]
    assert nxt[0].starts == dt.datetime(2026, 11, 10, 19, 0)
    nxt = ev.next_instances(prog(), dt.datetime(2026, 11, 11, 12, 0), count=2,
                            times_for=lambda d: TIMES)
    assert [i.title for i in nxt] == ["Tafsir Al-Kahf", "Les droits des voisins"]


def test_validation():
    for bad, motif in [
        ({"events": [{"kind": "mariage", "title": "X", "date": "2026-01-01", "time": "10:00"}]}, "type inconnu"),
        ({"events": [{"kind": "dars", "date": "2026-01-01", "time": "10:00"}]}, "titre manquant"),
        ({"events": [{"kind": "dars", "title": "X"}]}, "ni « date » ni « recurrence »"),
        ({"events": [{"kind": "dars", "title": "X", "recurrence": {"weekday": "mardy", "time": "19:00"}}]}, "jour de récurrence"),
        ({"events": [{"kind": "dars", "title": "X", "recurrence": {"weekday": "mardi"}}]}, "demande « time » ou « after »"),
        ({"events": [{"kind": "dars", "title": "X", "date": "2026-01-01"}]}, "demande aussi une heure"),
        ({"events": [{"kind": "dars", "title": "X", "date": "2026-13-01", "time": "10:00"}]}, "date invalide"),
        ({"events": [{"kind": "dars", "title": "X", "recurrence": {"weekday": "mardi", "after": "iftar"}}]}, "prière inconnue"),
    ]:
        with pytest.raises(ValueError, match=motif):
            ev.loads(json.dumps(bad))


def test_fichier_absent_ou_casse(tmp_path):
    assert ev.load(tmp_path / "absent.json") == ([], None)
    p = tmp_path / "bad.json"
    p.write_text("{ pas du json", encoding="utf-8")
    lst, err = ev.load(p)
    assert lst == [] and err is not None and "programme illisible" in err


def test_ecriture_et_relecture(tmp_path):
    p = tmp_path / "events.json"
    p.write_text(json.dumps(PROG), encoding="utf-8")
    lst, err = ev.load(p)
    assert err is None and len(lst) == 3
    _l, err = ev.load(p)
    assert err is None
    inst = lst[0].instance(D(2026, 11, 10), TIMES)
    assert inst is not None and inst.as_event()["session_no"] == 7
