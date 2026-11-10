"""Translation cache: only missing segments go back to the model, cached corrections survive."""
import json

from minbar.pipeline import translate

SEGS = [{"start": 0.0, "end": 1.0, "text": "الحمد لله رب العالمين"},
        {"start": 1.0, "end": 2.0, "text": "الله أكبر كبيرا والحمد لله كثيرا"}]


def test_seuls_les_segments_absents_partent_au_modele(tmp_path, monkeypatch):
    cache = translate.empty()
    cache["seg"][translate._seg_key(SEGS[0]["text"])] = {"fr": "Louange à Allah, Seigneur des mondes",
                                                         "coran": False, "fixe": True}
    p = tmp_path / "translations.json"
    p.write_text(json.dumps(cache), encoding="utf-8")
    seen = {}

    def ask(prompt, timeout=600, log=print):
        seen["prompt"] = prompt
        return {"seg": {"1": {"fr": "Allah est le plus grand", "coran": False}}}

    monkeypatch.setattr(translate.llm, "ask_json", ask)
    res = translate.translate_session(SEGS, [], p, use_llm=True, log=lambda m: None)
    assert SEGS[0]["text"] not in seen["prompt"]          # already cached: not sent back to the model
    assert SEGS[1]["text"] in seen["prompt"]
    assert res["seg"][translate._seg_key(SEGS[0]["text"])]["fr"] == "Louange à Allah, Seigneur des mondes"
    assert res["seg"][translate._seg_key(SEGS[1]["text"])]["fr"] == "Allah est le plus grand"
