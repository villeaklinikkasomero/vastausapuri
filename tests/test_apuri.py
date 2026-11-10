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


# --- Tutkimushaku -------------------------------------------------------------------

from vastausapuri.tutkimus import EuropePMC, Paper, _cached_search, sanitize_query  # noqa: E402


class FakeResearch:
    def __init__(self):
        self.queries = []

    def search(self, query, n, years):
        self.queries.append(query)
        return [Paper("T1", "Acne and anabolic steroids", "Smith J et al.", "J Derm", "2023",
                      "https://europepmc.org/article/MED/1", "Androgens increase sebum production.", True)]


def test_research_query_is_sanitized():
    q = sanitize_query('[NIMI_1] isotretinoin AND "anabolic steroids" 60mg/pv; DROP')
    assert "[NIMI_1]" not in q and "/" not in q and ";" not in q
    assert q.startswith("isotretinoin AND")


def test_research_used_in_draft(doping):
    fr = FakeResearch()
    a = Assistant(Settings(provider="mock"), doping, MockLLM(), fr)
    res, _ = a.run("Olen Pekka. Pahentaako testosteroni aknea?", mode="luonnos")
    assert fr.queries and "Pekka" not in fr.queries[0]
    assert res.research and res.research[0]["url"].startswith("https://europepmc.org/")
    assert res.research_query


def test_research_off_in_links_mode_and_on_request(doping):
    fr = FakeResearch()
    a = Assistant(Settings(provider="mock"), doping, MockLLM(), fr)
    a.run("Testosteroni ja akne?", mode="linkit")
    a.run("Testosteroni ja akne?", mode="luonnos", use_research=False)
    assert fr.queries == []


class FailingResearch:
    def search(self, query, n, years):
        raise TimeoutError()


def test_research_failure_does_not_break_draft(doping):
    a = Assistant(Settings(provider="mock"), doping, MockLLM(), FailingResearch())
    res, _ = a.run("Testosteroni ja akne?", mode="luonnos")
    assert res.draft and any("Tutkimushaku epäonnistui" in n for n in res.notes)


def test_europepmc_parsing(monkeypatch):
    rows = [{"id": "1", "source": "MED", "pmid": "1", "doi": "10.1/x", "title": "Acne <i>vulgaris</i>",
             "authorString": "Smith J, Doe A.", "journalInfo": {"journal": {"title": "Derm"}}, "pubYear": "2024",
             "abstractText": "<h4>Background</h4>Text &amp; more.", "pubTypeList": {"pubType": ["Review"]}}]
    client = EuropePMC()
    monkeypatch.setattr(client, "_query", lambda q, page_size=12, timeout=None: rows)
    _cached_search.cache_clear()
    papers = client.search("acne steroids", n=3, years=10)
    assert len(papers) == 1 and papers[0].title == "Acne vulgaris" and papers[0].review
    assert papers[0].abstract == "Background Text & more." and papers[0].authors == "Smith J et al."
    assert papers[0].url == "https://europepmc.org/article/MED/1"


class _ClientError(Exception):
    def __init__(self, code, msg):
        self.response = {"Error": {"Code": code, "Message": msg}}


class RetiredCheckerLLM(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if model == "eu.vanha-malli":
            raise _ClientError("ResourceNotFoundException", "This model version has reached the end of its life.")
        return super().complete(model=model, system=system, user=user)


def test_checker_falls_back_to_drafter_model(doping):
    s = Settings(provider="mock", checker_model="eu.vanha-malli")
    a = Assistant(s, doping, RetiredCheckerLLM(), FakeResearch())
    res, _ = a.run("Testosteroni ja akne?", mode="luonnos")
    assert res.research, "tutkimushaku toimii varamallilla"
    assert not any("Tarkastus epäonnistui" in n for n in res.notes)
    assert sum("ei ole käytettävissä" in n and "end of its life" in n for n in res.notes) == 1


def test_europepmc_retries_after_timeout(monkeypatch):
    calls = []

    def flaky(q, page_size=12, timeout=None):
        calls.append(page_size)
        if len(calls) == 1:
            raise TimeoutError()
        return [{"id": "2", "pmid": "2", "title": "T", "abstractText": "A", "pubYear": "2022",
                 "pubTypeList": {"pubType": ["Journal Article"]}},
                {"id": "3", "pmid": "3", "title": "R", "abstractText": "B", "pubYear": "2021",
                 "pubTypeList": {"pubType": ["Review"]}}]

    client = EuropePMC()
    monkeypatch.setattr(client, "_query", flaky)
    _cached_search.cache_clear()
    papers = client.search("steroids timeout", n=2, years=10)
    assert len(calls) == 2 and calls[1] < calls[0]
    assert papers[0].review and papers[0].title == "R"  # katsaus ensin


def test_stream_reports_steps_then_result(monkeypatch):
    c = client(monkeypatch, "ville:pw:dopinglinkki")
    login(c, "ville", "pw")
    r = c.post("/api/suggest/stream", json={"question": "Mistä saan neuloja?", "hanke": "dopinglinkki",
                                            "mode": "luonnos"})
    events = [json.loads(l) for l in r.text.splitlines() if l.strip()]
    assert events[0]["type"] == "steps"
    planned = [s["id"] for s in events[0]["steps"]]
    seen = [e["id"] for e in events if e["type"] == "step"]
    assert seen == planned  # vaiheet tulevat ilmoitetussa järjestyksessä
    assert events[-1]["type"] == "result" and events[-1]["data"]["draft"]


def test_stream_denies_other_projects(monkeypatch):
    c = client(monkeypatch, "rajattu:pw:toinen")
    login(c, "rajattu", "pw")
    assert c.post("/api/suggest/stream", json={"question": "Testi kysymys", "hanke": "dopinglinkki"}).status_code == 403


# --- Lakitiivistelmät ---------------------------------------------------------------

def test_law_items_are_curated(doping):
    laws = doping.index.by_type("laki")
    assert {"laki-dopingrikos", "laki-laakkeiden-tuonti", "laki-potilasasiakirjat"} <= {l.id for l in laws}
    for l in laws:
        assert l.url.startswith("https://www.finlex.fi/fi/lainsaadanto/")
        assert "Tiivistelmä, ei virallinen lakiteksti" in l.content


@pytest.mark.parametrize("query,expected", [
    ("Voiko lääkäri ilmoittaa poliisille jos kerron steroidien käytöstä?", "laki-vaitiolovelvollisuus"),
    ("Näkeekö vakuutusyhtiö potilastietoni?", "laki-potilasasiakirjat"),
    ("Saako SARMeja tuoda matkalta Thaimaasta?", "laki-laakkeiden-tuonti"),
])
def test_law_found_and_used_as_source(doping, query, expected):
    res, _ = make(doping).run(query, mode="luonnos")
    assert expected in res.sources_used, (query, res.sources_used)


# --- Kieli --------------------------------------------------------------------------

def test_tidy_text_removes_dashes_and_bold_but_keeps_ranges():
    from vastausapuri.pipeline import tidy_text
    out, n = tidy_text("Testosteroni – erityisesti suurina annoksina – lisää aknea. Kestää 3–6 kuukautta. "
                       "**Tärkeää**: C-hepatiitti.")
    assert "–" not in out.replace("3–6", "") and "**" not in out
    assert "3–6 kuukautta" in out and "C-hepatiitti" in out and n == 3


class DashyLLM(MockLLM):
    def complete(self, *, model, system, user, max_tokens=1500):
        if "<luonnos>" in system:
            return '<meta>{"linkit": []}</meta><luonnos>Hei,\nAkne – etenkin selässä – on yleistä.\n</luonnos>'
        return super().complete(model=model, system=system, user=user)


def test_draft_is_tidied_and_noted(doping):
    res, _ = make(doping, DashyLLM()).run("Akne ja steroidit?", mode="luonnos")
    assert "–" not in res.draft and "Akne, etenkin selässä, on yleistä." in res.draft
    assert any("ajatusviivaa" in n for n in res.notes)


# --- Vertailun havainnot (lokakuu 2026) ------------------------------------------------

CARDIAC = ("Mieheni lopetti dopingin 2 v sitten. Sydämen tykytystä ja hikoilua, leposyke 124, heikotusta. "
           "Käynyt terveyskeskuslääkärillä, yksityisellä yleislääkärillä ja endokrinologilla.")


def test_cardiac_symptoms_trigger_signal_and_emergency_links(doping):
    res, _ = make(doping).run(CARDIAC, mode="luonnos")
    assert "akuutti_terveysriski" in {s.kind for s in res.signals}
    ids = [l.id for l in res.links]
    assert ids[:2] == ["hata-112", "paivystysapu"] or {"hata-112", "paivystysapu"} <= set(ids[:3])
    assert "kriisipuhelin" not in ids  # itsetuhoisuuden palvelu ei kuulu tähän


class CapturingLLM(MockLLM):
    def __init__(self):
        self.calls = []

    def complete(self, *, model, system, user, max_tokens=1500):
        self.calls.append((system, user))
        if "<luonnos>" in system:
            return '<meta>{"linkit": []}</meta><luonnos>Ymmärrämme hyvin huolesi.\n</luonnos>'
        return super().complete(model=model, system=system, user=user)


def test_expert_list_and_rules_reach_models_and_greeting_is_added(doping):
    llm = CapturingLLM()
    res, _ = make(doping, llm).run(CARDIAC, mode="luonnos")
    draft_sys, draft_user = next(c for c in llm.calls if "<luonnos>" in c[0])
    assert "ASIANTUNTIJALISTA" in draft_user and "Roman Rusman" in draft_user
    assert "SAMANA PÄIVÄNÄ" in draft_sys and "Hoitohistoria" in draft_sys
    checker_sys, checker_user = next(c for c in llm.calls if "TARKASTAJA" in c[0] and "ongelmat" in c[0])
    assert "aikarajan" in checker_sys and "ASIANTUNTIJALISTA" in checker_user
    assert res.draft.startswith("Hei, kiitos kysymyksestäsi.")
