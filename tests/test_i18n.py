import re
import sys
from pathlib import Path

import pytest

from screenkaffee import i18n
from screenkaffee.i18n_en import CATALOG, app, editor, general

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import i18n_check  # noqa: E402

PLACEHOLDER = re.compile(r"\{[^{}]*\}")


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("de")


def test_german_is_the_default_in_tests():
    assert i18n.language() == "de"
    assert i18n.tr("Sprache") == "Sprache"


def test_english_uses_catalog_and_keeps_unknown_text(english):
    assert i18n.tr("Sprache") == "Language"
    assert i18n.tr("Unbekannter Text ohne Eintrag") == "Unbekannter Text ohne Eintrag"


def test_placeholders_are_filled_in_both_languages(english):
    CATALOG.setdefault("{count} Testaufnahmen", "{count} test captures")
    try:
        assert i18n.tr("{count} Testaufnahmen", count=3) == "3 test captures"
        i18n.set_language("de")
        assert i18n.tr("{count} Testaufnahmen", count=3) == "3 Testaufnahmen"
    finally:
        CATALOG.pop("{count} Testaufnahmen", None)


def test_language_setting_is_resolved(monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.resolve("auto") == "en"
    assert i18n.resolve("de") == "de"


def test_every_translation_keeps_the_same_placeholders():
    for german, english_text in CATALOG.items():
        assert sorted(PLACEHOLDER.findall(german)) == sorted(PLACEHOLDER.findall(english_text)), german
        assert english_text.strip(), german


def test_catalog_parts_do_not_contradict_each_other():
    seen = {}
    for part in (general, app, editor):
        for german, english_text in part.TEXTS.items():
            assert seen.setdefault(german, english_text) == english_text, german


def test_every_tr_call_in_the_app_has_an_english_entry():
    problems = []
    for path in sorted((ROOT / "screenkaffee").glob("*.py")):
        if path.name in i18n_check.SKIP_FILES:
            continue
        missing, _ = i18n_check.check(path, CATALOG)
        problems += [f"{path.name}:{line}: {text}" for line, text in missing]
    assert not problems, "\n".join(problems[:40])
