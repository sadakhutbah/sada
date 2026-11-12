"""Server messages: English source, per-request language, complete catalogs."""
import ast
import json
from pathlib import Path

from sada import i18n

ROOT = Path(__file__).resolve().parent.parent


def test_language_negotiation():
    assert i18n.normalize("fr-FR,fr;q=0.9,en;q=0.8") == "fr"
    assert i18n.normalize("de-DE,ar;q=0.5") == "ar"
    assert i18n.normalize("de") is None
    tok = i18n.set_lang("fr")
    try:
        assert i18n._("not found") == "introuvable"
    finally:
        i18n.reset(tok)
    assert i18n.translate("not found", "ar") == "غير موجود"
    assert i18n.translate("unknown {x}", "fr", x=1) == "unknown 1"


def _sources() -> set[str]:
    """Every literal passed to _() in the code."""
    out = set()
    for f in (ROOT / "sada").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                out.add(node.args[0].value)
    return out


def test_catalogs_are_complete_and_valid():
    sources = _sources()
    for lang in ("fr", "ar"):
        cat = {}
        for f in sorted((ROOT / "sada" / "locales" / lang).glob("*.json")):
            cat.update(json.loads(f.read_text(encoding="utf-8")))
        missing = sorted(sources - cat.keys())
        assert not missing, f"{lang}: {len(missing)} missing, e.g. {missing[:5]}"
        for src, tr in cat.items():
            # placeholders must survive translation
            import string
            names = {n for _, n, _, _ in string.Formatter().parse(src) if n}
            assert names == {n for _, n, _, _ in string.Formatter().parse(tr) if n}, (lang, src)
