import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vastausapuri.config import ROOT, Settings
from vastausapuri.hanke import load_all, load_hanke
from vastausapuri.llm import MockLLM, parse_draft
from vastausapuri.pipeline import Assistant
from vastausapuri.pseudonymize import pseudonymize
from vastausapuri.safety import detect_signals

HANKKEET = ROOT / "hankkeet"


@pytest.fixture(scope="module")
def doping():
    return load_hanke(HANKKEET, "dopinglinkki")


# --- Hankkeet ja katalogit ---------------------------------------------------------

def test_all_projects_load():
    hs = load_all(HANKKEET)
    assert "dopinglinkki" in hs
    for h in hs.values():
        assert h.index.by_type("kriisi"), "jokaisella hankkeella pitää olla kriisipalvelut"
        for it in h.index.items:
            assert it.url.startswith("https://"), it.id


def test_catalog_ids_unique(doping):
    ids = [it.id for it in doping.index.items]
    assert len(ids) == len(set(ids))


# --- Pseudonymisointi -------------------------------------------------------------

def test_pseudonymize_direct_identifiers():
    text = ("Hei, olen Matti, 42-vuotias Tampereelta. Puh 040 123 4567, matti.m@esimerkki.fi, "
            "hetu 010180-123A. Asun Hämeenkatu 12 B 5, 33100. Tapahtui 3.5.2026. Terveisin Matti")
    ps = pseudonymize(text)
    for secret in ["Matti", "42", "Tampere", "040 123 4567", "matti.m@esimerkki.fi",
                   "010180-123A", "Hämeenkatu 12", "33100", "3.5.2026"]:
        assert secret not in ps.text, secret
    assert ps.restore(ps.text) == text


def test_own_links_kept_others_replaced():
    ps = pseudonymize("Katso https://dopinglinkki.fi/neuvonta/ ja https://instagram.com/matti", {"dopinglinkki.fi"})
    assert "https://dopinglinkki.fi/neuvonta/" in ps.text and "instagram" not in ps.text


def test_ordinary_text_untouched():
    t = "Olen käyttänyt testosteronia kahdeksan viikkoa ja mietin palautumista."
    assert pseudonymize(t).text == t


# --- Kriisisignaalit --------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("En enää halua elää tällaista elämää.", "itsetuhoisuus"),
    ("Mieheni löi minua eilen.", "väkivalta"),
    ("Kaveri otti kaikki pillerit ja ei herää.", "akuutti_myrkytys"),
])
def test_common_signals(text, kind):
    assert kind in {s.kind for s in detect_signals(text)}


def test_project_specific_signals(doping):
    kinds = {s.kind for s in detect_signals(
        "Pistoskohta pakarassa on turvonnut ja kuumottaa, ja nyt on korkea kuume.", doping.signal_patterns)}
    assert "akuutti_terveysriski" in kinds


def test_no_signal_in_neutral_text(doping):
    assert detect_signals("Kuinka kauan hormonitoiminnan palautuminen kestää kuurin jälkeen?",
                          doping.signal_patterns) == []


# --- Haku --------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("Mistä saan puhtaita neuloja ja ruiskuja?", {"terveysneuvontapisteet", "turvallisempi-pistaminen"}),
    ("Palautuuko hedelmällisyys steroidikuurin jälkeen?", {"lisaantymisterveys", "ukk"}),
    ("Onko ostariinin tilaaminen netistä laitonta?", {"sarm", "lainsaadanto", "tulli-netista"}),
    ("Poikani on alkanut treenata ja epäilen että hän käyttää jotain", {"kayttaako-lapseni", "tunnistaminen"}),
])
def test_search_dopinglinkki(doping, query, expected):
    top = {it.id for it, _ in doping.index.search(query, k=5)}
    assert expected & top, (query, top)


# --- Putki ------------------------------------------------------------------------

def make(h, llm=None):
    return Assistant(Settings(provider="mock"), h, llm or MockLLM())


def test_links_mode(doping):
    res, _ = make(doping).run("Olen Pekka ja mietin klenbuterolin riskejä.", mode="linkit")
    assert "Pekka" not in res.pseudonymized_question
    assert 1 <= len(res.links) <= 5 and res.draft == ""


def test_draft_mode_replaces_refs_with_urls(doping):
    res, ps = make(doping).run("Olen Pekka. Miten pistän turvallisemmin?", mode="luonnos")
    assert res.draft and "[[" not in res.draft
    assert "https://dopinglinkki.fi/" in res.draft
    assert res.sources_used


class HallucinatingLLM(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if "TARKASTAJA" in system:
            return super().complete(model=model, system=system, user=user)
        meta = {"aiheet": ["x"], "kysyja": "itse",
                "linkit": [{"id": "keksitty-sivu", "perustelu": "x"}, {"id": "klenbuteroli", "perustelu": "y"}]}
        return (f"<meta>{json.dumps(meta)}</meta><luonnos>Hei,\nLue [[klenbuteroli]] ja [[keksitty-sivu]].\n"
                "</luonnos>")


def test_invented_ids_never_become_links(doping):
    res, _ = make(doping, HallucinatingLLM()).run("Klenbuterolin riskit?", mode="luonnos")
    assert "keksitty-sivu" not in [l.id for l in res.links]
    assert "[TARKISTA: tuntematon linkki]" in res.draft
    assert "https://dopinglinkki.fi/tietopankki/dopingaineet/klenbuteroli/" in res.draft


def test_crisis_links_first(doping):
    res, _ = make(doping).run("Käytän steroideja enkä enää halua elää.", mode="luonnos")
    assert res.links[0].type == "kriisi"


class BrokenChecker(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if "TARKASTAJA" in system:
            return "en osaa"
        return super().complete(model=model, system=system, user=user)


def test_checker_failure_is_visible(doping):
    res, _ = make(doping, BrokenChecker()).run("Klenbuterolin riskit?", mode="luonnos")
    assert res.draft and any("Tarkastus epäonnistui" in n for n in res.notes)


def test_parse_draft_tolerates_missing_meta_tags():
    meta, text = parse_draft('{"linkit": []}\n<luonnos>\nHei!\n</luonnos>')
    assert meta == {"linkit": []} and text == "Hei!"


def test_own_style_examples(tmp_path):
    d = tmp_path / "dopinglinkki"
    d.mkdir()
    rows = [
        {"id": "1", "text": "Moi! Steroidikuurin jälkeen hormonitoiminta palautuu yleensä kuukausissa.", "vastaaja": "ville"},
        {"id": "2", "text": "Hei, hormonitoiminnan palautuminen kestää usein 3–6 kuukautta.", "vastaaja": "liisa"},
    ]
    (d / "vastaukset.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    h = load_hanke(HANKKEET, "dopinglinkki", data_dir=tmp_path)
    assert h.answers.respondents() == {"ville", "liisa"}
    res, _ = make(h).run("Kauanko hormonitoiminnan palautuminen kestää?", mode="luonnos",
                         vastaaja="ville", own_style=True)
    assert res.examples_used and res.examples_used[0] == {"id": "1", "oma": True}


def test_bedrock_requires_eu_profile():
    with pytest.raises(ValueError):
        Settings(provider="bedrock", drafter_model="global.anthropic.claude-opus-5-5").validate()


# --- Työkalut ---------------------------------------------------------------------

def test_catalog_page_parser():
    from vastausapuri.tyokalut.rakenna_katalogi import PageParser
    html = ("<html><head><title>Klenbuteroli - Dopinglinkki</title><meta name='description' content='Kuvaus.'>"
            "</head><body><nav>Valikko</nav><main><h1>Klenbuteroli</h1><p>" + "Tekstiä. " * 40 +
            "</p><script>x()</script></main><footer>Alatunniste</footer></body></html>")
    p = PageParser()
    p.feed(html)
    r = p.result("Dopinglinkki")
    assert r["title"] == "Klenbuteroli" and r["description"] == "Kuvaus."
    assert "Valikko" not in r["content"] and "x()" not in r["content"] and "Alatunniste" not in r["content"]


def test_import_answers(tmp_path, capsys):
    from vastausapuri.tyokalut.tuo_vastaukset import main
    src = tmp_path / "vienti.csv"
    src.write_text(
        "id;vastaaja;vastaus\n"
        "1;Ville S.;Hei Matti! Lue lisää https://dopinglinkki.fi/tietopankki/dopingaineet/klenbuteroli/ "
        "ja soita 040 123 4567. Klenbuteroli rasittaa sydäntä merkittävästi.\n"
        "2;Liisa;liian lyhyt\n", encoding="utf-8")
    out = tmp_path / "v.jsonl"
    assert main(["dopinglinkki", str(src), "--ulos", str(out), "--vastaajakartta", "Ville S.=ville",
                 "--min-pituus", "20"]) == 0
    recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert len(recs) == 1 and recs[0]["vastaaja"] == "ville"
    assert "Matti" not in recs[0]["text"] and "040 123 4567" not in recs[0]["text"]
    assert "https://dopinglinkki.fi/tietopankki/dopingaineet/klenbuteroli/" in recs[0]["text"]
    assert "kyllä" in capsys.readouterr().out  # linkki tunnistettiin katalogista


# --- API --------------------------------------------------------------------------

def client(monkeypatch, users=None, provider="mock"):
    monkeypatch.setenv("APURI_PROVIDER", provider)
    if users is None:
        monkeypatch.delenv("APURI_USERS", raising=False)
    else:
        monkeypatch.setenv("APURI_USERS", users)
    from vastausapuri import app as appmod
    appmod.assistants.cache_clear()
    return TestClient(appmod.app)


def login(c, u, p):
    return c.post("/login", data={"username": u, "password": p}, follow_redirects=False)


def test_login_flow(monkeypatch):
    c = client(monkeypatch, "vastaaja:salainen")
    assert c.get("/healthz").status_code == 200
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/status").status_code == 401
    assert login(c, "vastaaja", "väärä").status_code == 401
    assert login(c, "vastaaja", "salainen").status_code == 303
    r = c.get("/api/status")
    assert r.status_code == 200 and r.headers["X-Robots-Tag"].startswith("noindex")
    assert r.json()["user"] == "vastaaja" and r.json()["hankkeet"][0]["id"] == "dopinglinkki"
    c.cookies.set("apuri_session", "vastaaja|9999999999|vaarennetty")
    assert c.get("/api/status").status_code == 401


def test_project_access_restricted(monkeypatch):
    c = client(monkeypatch, "rajattu:pw:toinenhanke")
    login(c, "rajattu", "pw")
    assert c.get("/api/status").json()["hankkeet"] == []
    r = c.post("/api/suggest", json={"question": "Klenbuterolin riskit?", "hanke": "dopinglinkki"})
    assert r.status_code == 403


def test_api_draft(monkeypatch):
    c = client(monkeypatch, "ville:pw:dopinglinkki")
    login(c, "ville", "pw")
    r = c.post("/api/suggest", json={"question": "Olen Pekka, mistä saan neuloja?", "hanke": "dopinglinkki",
                                     "mode": "luonnos"})
    d = r.json()
    assert r.status_code == 200 and d["draft"] and d["links"]
    assert "Pekka" not in d["pseudonymized_question"]


def test_bedrock_closed_without_users(monkeypatch):
    c = client(monkeypatch, None, provider="bedrock")
    assert c.get("/").status_code == 503


# --- Jatkokysymykset ja Dopinglinkin tyyli ------------------------------------------

def test_followup_shared_placeholders_and_no_feedback(doping):
    thread = "Kysymys: Olen Pekka, aknea testosteronin jälkeen.\nVastaus: Hei Pekka, kerro lääkärille."
    res, ps = make(doping).run("Pekka taas: mitä potilastietoihin kirjataan?", mode="luonnos", thread=thread)
    assert res.followup
    assert "Pekka" not in res.pseudonymized_question
    assert res.pseudonymized_question.count("[NIMI_1]") == 3  # sama nimi, sama merkki
    assert "palautelomakkeen" not in res.draft
    assert {"kanta"} & {it.id for it, _ in doping.index.search("mitä potilastietoihin kirjataan vakuutus", k=5)}


def test_first_answer_gets_feedback_link(doping):
    res, _ = make(doping).run("Milloin anabolisten käytön voi aloittaa isotretinoiinikuurin jälkeen?", mode="luonnos")
    assert not res.followup and "webropolsurveys" in res.draft


def test_acne_question_finds_steroid_article(doping):
    top = {it.id for it, _ in doping.index.search(
        "Selkäakne paheni testosteronin ja masteronin käytön jälkeen, isotretinoiini", k=5)}
    assert "steroidit-testosteroni" in top
