import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vastausapuri.catalog import Index, load_catalog
from vastausapuri.config import Settings
from vastausapuri.llm import MockLLM
from vastausapuri.pipeline import Assistant
from vastausapuri.pseudonymize import pseudonymize
from vastausapuri.safety import detect_signals

CATALOG = Path(__file__).resolve().parent.parent / "data" / "katalogi.json"


@pytest.fixture(scope="module")
def index():
    return Index(load_catalog(CATALOG))


# --- Pseudonymisointi -------------------------------------------------------

def test_pseudonymize_direct_identifiers():
    text = ("Hei, olen Matti, 42-vuotias Tampereelta. Puh 040 123 4567, matti.m@esimerkki.fi, "
            "hetu 010180-123A. Asun Hämeenkatu 12 B 5, 33100. Tapahtui 3.5.2026. Terveisin Matti")
    ps = pseudonymize(text)
    for secret in ["Matti", "42", "Tampere", "040 123 4567", "matti.m@esimerkki.fi",
                   "010180-123A", "Hämeenkatu 12", "33100", "3.5.2026"]:
        assert secret not in ps.text, secret
    assert ps.restore(ps.text) == text


def test_same_value_gets_same_placeholder():
    ps = pseudonymize("Liisa juo paljon. Liisalle sanoin asiasta.")
    assert ps.text.count("[NIMI_1]") == 1  # Liisa ja Liisalle ovat eri merkkijonoja
    assert "Liisa" not in ps.text


def test_placeholders_are_numbered_per_kind():
    ps = pseudonymize("Mikko ja Sanna asuvat Oulussa.")
    assert "[NIMI_1]" in ps.text and "[NIMI_2]" in ps.text and "[PAIKKA_1]" in ps.text


def test_ordinary_text_untouched():
    t = "Juon viikonloppuisin noin kymmenen olutta ja mietin, onko se liikaa."
    assert pseudonymize(t).text == t


# --- Kriisisignaalit --------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("En enää halua elää tällaista elämää.", "itsetuhoisuus"),
    ("Mieheni löi minua eilen humalassa.", "väkivalta"),
    ("Äiti otti kaikki pillerit ja ei herää.", "akuutti_myrkytys"),
])
def test_signals(text, kind):
    assert kind in {s.kind for s in detect_signals(text)}


def test_no_signal_in_neutral_text():
    assert detect_signals("Miten voisin vähentää juomista arkisin?") == []


# --- Haku -------------------------------------------------------------------

def test_search_handles_inflection(index):
    top = [it.id for it, _ in index.search("Haluaisin vähentää juomistani", k=3)]
    assert "juomapaivakirja" in top or "alkoholi-vahentaminen" in top


def test_search_gambling(index):
    top = [it.id for it, _ in index.search("pelaan nettikasinolla ja velkaa kertyy", k=3)]
    assert {"rahapeli-testi", "pelaamisen-hallinta"} & set(top)


# --- Putki ------------------------------------------------------------------

def make_assistant(index, llm=None):
    return Assistant(Settings(provider="mock"), index, llm or MockLLM())


def test_pipeline_mock(index):
    res, ps = make_assistant(index).run("Olen Pekka ja juon liikaa viikonloppuisin, miten vähennän?")
    assert "Pekka" not in res.pseudonymized_question
    assert 1 <= len(res.links) <= 5
    assert all(l.url.startswith("https://") for l in res.links)
    assert not res.signals


class HallucinatingLLM(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if "TARKASTAJA" in system:
            return super().complete(model=model, system=system, user=user)
        return json.dumps({"aiheet": ["alkoholi"], "kysyja": "itse",
                           "linkit": [{"id": "keksitty-sivu", "perustelu": "x"},
                                      {"id": "juomapaivakirja", "perustelu": "y"}],
                           "runko": [], "huomiot": []})


def test_invented_ids_are_dropped(index):
    res, _ = make_assistant(index, HallucinatingLLM()).run("Juon liikaa, mitä teen?")
    ids = [l.id for l in res.links]
    assert "keksitty-sivu" not in ids and "juomapaivakirja" in ids
    assert res.invalid_ids == ["keksitty-sivu"]


def test_crisis_links_added_first(index):
    res, _ = make_assistant(index).run("Juon joka päivä enkä enää halua elää.")
    assert res.links[0].type == "kriisi"
    assert any(s.kind == "itsetuhoisuus" for s in res.signals)


class BrokenChecker(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if "TARKASTAJA" in system:
            return "en osaa vastata"
        return super().complete(model=model, system=system, user=user)


def test_checker_failure_is_visible(index):
    res, _ = make_assistant(index, BrokenChecker()).run("Juon liikaa, mitä teen?")
    assert res.links and any("Tarkastus epäonnistui" in n for n in res.notes)


def test_bedrock_requires_eu_profile():
    with pytest.raises(ValueError):
        Settings(provider="bedrock", drafter_model="global.anthropic.claude-opus-5-5").validate()


# --- API --------------------------------------------------------------------

def test_api(monkeypatch):
    monkeypatch.setenv("APURI_PROVIDER", "mock")
    from vastausapuri import app as appmod
    appmod.assistant.cache_clear()
    c = TestClient(appmod.app)
    assert c.get("/api/status").json()["provider"] == "mock"
    r = c.post("/api/suggest", json={"question": "Poikani polttaa kannabista, mitä teen?"})
    assert r.status_code == 200 and r.json()["links"]


def test_auth_required_when_users_set(monkeypatch):
    monkeypatch.setenv("APURI_PROVIDER", "mock")
    monkeypatch.setenv("APURI_USERS", "vastaaja:salainen")
    from vastausapuri import app as appmod
    appmod.assistant.cache_clear()
    c = TestClient(appmod.app)
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/status", auth=("vastaaja", "väärä")).status_code == 401
    r = c.get("/api/status", auth=("vastaaja", "salainen"))
    assert r.status_code == 200 and r.headers["X-Robots-Tag"].startswith("noindex")


def test_bedrock_closed_without_users(monkeypatch):
    monkeypatch.setenv("APURI_PROVIDER", "bedrock")
    monkeypatch.delenv("APURI_USERS", raising=False)
    from vastausapuri import app as appmod
    c = TestClient(appmod.app)
    assert c.get("/").status_code == 503
