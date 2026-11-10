"""Kahden mallin putki.

Tilat:
- "linkit"  (vaihe 1): linkkiehdotukset, runko ja huomiot
- "luonnos" (vaihe 2): kokonainen vastausluonnos lähdeotteiden ja vanhojen vastausten pohjalta

Molemmissa: pseudonymisointi -> haku -> luonnostelija -> tarkastaja -> koodin tarkistukset.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from . import prompts
from .catalog import CatalogItem
from .config import Settings
from .hanke import Hanke
from .llm import LLM, parse_draft, parse_json
from .pseudonymize import Pseudonymized, pseudonymize
from .safety import Signal, detect_signals
from .tutkimus import Paper, ResearchClient, format_papers

MODES = ("linkit", "luonnos")
_SEP = "\n\n\u27e6JATKOKYSYMYS\u27e7\n\n"
_REF = re.compile(r"\[\[([^\[\]\s]+)\]\]")


@dataclass
class LinkSuggestion:
    id: str
    title: str
    url: str
    type: str
    reason: str
    source: str  # "luonnostelija" | "tarkastaja" | "kriisi-automaatti"
    checker_note: str = ""


@dataclass
class Result:
    hanke: str
    mode: str
    followup: bool
    pseudonymized_question: str
    pseudonym_counts: dict[str, int]
    topics: list[str]
    asker: str
    links: list[LinkSuggestion]
    rejected: list[dict]
    outline: list[str]
    notes: list[str]
    signals: list[Signal]
    crisis: dict
    invalid_ids: list[str]
    models: dict[str, str]
    draft: str = ""
    issues: list[dict] = field(default_factory=list)
    examples_used: list[dict] = field(default_factory=list)
    research_query: str = ""
    research: list[dict] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self, restore: Pseudonymized | None = None) -> dict:
        d = asdict(self)
        if restore is not None:  # nimet takaisin vain vastaajan näkymään
            d["outline"] = [restore.restore(x) for x in self.outline]
            d["notes"] = [restore.restore(x) for x in self.notes]
            d["draft"] = restore.restore(self.draft)
        return d


class Assistant:
    def __init__(self, settings: Settings, hanke: Hanke, llm: LLM, research: ResearchClient | None = None):
        self.s = settings
        self.h = hanke
        self.index = hanke.index
        self.llm = llm
        self.research = research

    def _research(self, question: str, notes: list[str]) -> tuple[str, list[Paper]]:
        """Hakulause mallilta (vain yleiset termit) ja tiivistelmät Europe PMC:stä."""
        cfg = self.h.research
        try:
            raw = self.llm.complete(model=self.s.checker_model,
                                    system=prompts.research_query_system(self.h),
                                    user=f"KYSYMYS (pseudonymisoitu):\n{question}", max_tokens=200)
            q = parse_json(raw)
            if not q.get("tarpeen", True) or not q.get("haku"):
                return "", []
            query = str(q["haku"])
            papers = self.research.search(query, n=int(cfg.get("maara", 4)), years=int(cfg.get("vuodet", 12)))
            if not papers:
                notes.append(f"Tutkimushaku ei löytänyt tiivistelmiä hakulauseella: {query}")
            return query, papers
        except Exception as e:  # noqa: BLE001
            notes.append(f"Tutkimushaku epäonnistui ({type(e).__name__}); luonnos tehtiin ilman sitä.")
            return "", []

    def _candidates(self, text: str) -> list[CatalogItem]:
        found = [it for it, score in self.index.search(text, k=self.s.candidates) if score > 0]
        crisis = [it for it in self.index.by_type("kriisi") if it not in found]
        return found + crisis  # kriisipalvelut ovat aina tarjolla

    def run(self, question: str, mode: str = "linkit", vastaaja: str | None = None,
            own_style: bool = False, thread: str | None = None,
            use_research: bool = True) -> tuple[Result, Pseudonymized]:
        """thread: aiempi keskustelu (kysymys, vastaus, ...) jatkokysymystä varten."""
        if mode not in MODES:
            raise ValueError(f"Tuntematon tila: {mode}")
        draft_mode = mode == "luonnos"
        t0 = time.perf_counter()
        thread = (thread or "").strip() or None
        followup = thread is not None
        # Keskustelu ja kysymys pseudonymisoidaan yhdessä, jotta sama nimi saa saman paikkamerkin
        ps = pseudonymize(f"{thread}{_SEP}{question}" if followup else question, self.h.safe_domains)
        p_thread, p_question = ps.text.split(_SEP, 1) if followup else (None, ps.text)
        signals = detect_signals(question, self.h.signal_patterns)  # paikallinen, alkuperäisestä
        query = f"{p_question}\n{p_question}\n{p_thread[-1000:]}" if followup else p_question
        candidates = self._candidates(query)
        cand_by_id = {c.id: c for c in candidates}
        sources = [c for c in candidates if c.type != "kriisi"][: self.s.sources]
        examples = []
        if draft_mode:
            examples = self.h.answers.similar(query, k=self.s.examples,
                                              vastaaja=vastaaja if own_style else None)
        own = vastaaja if (own_style and any(e.vastaaja == vastaaja for e in examples)) else None
        t1 = time.perf_counter()

        notes: list[str] = []
        research_query, papers = "", []
        if draft_mode and use_research and self.research is not None and self.h.research.get("kaytossa"):
            research_query, papers = self._research(p_question, notes)
        research_text = format_papers(papers) if papers else None
        t_r = time.perf_counter()

        # 1) Luonnostelija
        draft_text = ""
        if draft_mode:
            raw = self.llm.complete(
                model=self.s.drafter_model,
                system=prompts.draft_system(self.h, self.s.max_links, own_style=own is not None,
                                            followup=followup),
                user=prompts.draft_user(p_question, candidates, sources, examples, own, p_thread,
                                        research_text),
                max_tokens=3000,
            )
            meta, draft_text = parse_draft(raw)
            if own_style and own is None:
                notes.append("Omia vastauksiasi ei löytynyt indeksistä, joten luonnos on kirjoitettu yleisellä tyylillä.")
        else:
            raw = self.llm.complete(
                model=self.s.drafter_model,
                system=prompts.links_system(self.h, self.s.max_links),
                user=prompts.links_user(p_question, candidates, p_thread),
            )
            meta = parse_json(raw)
        notes += list(meta.get("huomiot", []))
        t2 = time.perf_counter()

        # 2) Tarkastaja (virhe ei kaada putkea, mutta näkyy vastaajalle)
        try:
            raw_c = self.llm.complete(
                model=self.s.checker_model,
                system=prompts.checker_system(self.h, draft_mode),
                user=prompts.checker_user(p_question, candidates, meta,
                                          sources if draft_mode else None,
                                          draft_text if draft_mode else None, p_thread, research_text),
            )
            check = parse_json(raw_c)
        except Exception as e:  # noqa: BLE001
            check = {"linkit": [], "lisaa": [], "ongelmat": [], "huomiot": [],
                     "kriisi": {"havaittu": False, "kuvaus": ""}}
            notes.append(f"Tarkastus epäonnistui ({type(e).__name__}). Tarkista luonnos ja linkit itse.")
        t3 = time.perf_counter()

        # 3) Koodin tarkistukset: vain katalogin id:t kelpaavat
        invalid: list[str] = []
        verdicts = {v.get("id"): v for v in check.get("linkit", [])}
        links: list[LinkSuggestion] = []
        rejected: list[dict] = []
        seen: set[str] = set()

        def add(item_id: str, reason: str, source: str, note: str = "") -> None:
            item = cand_by_id.get(item_id)
            if item is None:
                if item_id not in invalid:
                    invalid.append(item_id)
                return
            if item_id in seen:
                return
            seen.add(item_id)
            links.append(LinkSuggestion(item.id, item.title, item.url, item.type, reason, source, note))

        for l in meta.get("linkit", []):
            v = verdicts.get(l.get("id"), {})
            if v and v.get("hyvaksytty") is False:
                rejected.append({"id": l.get("id"), "syy": v.get("syy", "")})
                continue
            add(l.get("id", ""), l.get("perustelu", ""), "luonnostelija", v.get("syy", ""))
        # Luonnoksen tekstissä viitatut, joita ei ollut listattu
        for ref in _REF.findall(draft_text):
            if ref not in seen and ref not in {r["id"] for r in rejected}:
                add(ref, "Viitattu luonnoksessa.", "luonnostelija")
        for l in check.get("lisaa", []):
            add(l.get("id", ""), l.get("syy", ""), "tarkastaja")

        crisis = check.get("kriisi") or {"havaittu": False, "kuvaus": ""}
        if signals or crisis.get("havaittu"):
            kinds = {sg.kind for sg in signals}
            for it in self.index.by_type("kriisi"):
                # Paikallinen signaali nostaa vain siihen sopivat palvelut; mallin havainto kaikki
                relevant = crisis.get("havaittu") or not it.signals or kinds & set(it.signals)
                if relevant and it.id not in seen:
                    seen.add(it.id)
                    links.append(LinkSuggestion(it.id, it.title, it.url, it.type,
                                                "Kysymyksessä on mahdollinen kriisisignaali.",
                                                "kriisi-automaatti"))
        links.sort(key=lambda l: l.type != "kriisi")  # kriisipalvelut ensin
        n_crisis = sum(1 for l in links if l.type == "kriisi")
        links = links[: self.s.max_links + n_crisis]

        # Luonnoksen [[id]]-viittaukset osoitteiksi; tuntemattomat ja hylätyt merkitään tarkistettaviksi
        rejected_ids = {r["id"] for r in rejected}

        def link_sub(m: re.Match) -> str:
            item = cand_by_id.get(m.group(1))
            if item is None:
                return "[TARKISTA: tuntematon linkki]"
            if item.id in rejected_ids:
                return f"{item.url} [TARKISTA: tarkastaja ei suositellut tätä linkkiä]"
            return item.url

        draft_out = _REF.sub(link_sub, draft_text)
        feedback = self.h.vastaus.get("palaute", "").strip()
        if draft_out and feedback and not followup:
            draft_out = f"{draft_out.rstrip()}\n\n{feedback}"

        issues = [i for i in check.get("ongelmat", []) if isinstance(i, dict)]
        order = {"korkea": 0, "keski": 1, "matala": 2}
        issues.sort(key=lambda i: order.get(i.get("vakavuus", "matala"), 3))
        notes += check.get("huomiot", [])
        if invalid:
            notes.append(f"Mallin ehdottamia tuntemattomia sisältöjä poistettiin: {', '.join(invalid)}")

        used_research = {str(r.get("id")): str(r.get("kaytto", "")) for r in meta.get("tutkimukset", [])
                         if isinstance(r, dict)}
        result = Result(
            hanke=self.h.id,
            mode=mode,
            pseudonymized_question=ps.text.replace(_SEP, "\n\n--- JATKOKYSYMYS ---\n\n"),
            followup=followup,
            pseudonym_counts=ps.counts,
            topics=meta.get("aiheet", []),
            asker=meta.get("kysyja", "ei tiedossa"),
            links=links,
            rejected=rejected,
            outline=meta.get("runko", []),
            notes=notes,
            signals=signals,
            crisis=crisis,
            invalid_ids=invalid,
            models={"luonnostelija": self.s.drafter_model, "tarkastaja": self.s.checker_model,
                    "provider": self.s.provider},
            draft=draft_out,
            issues=issues,
            examples_used=[{"id": e.id, "oma": bool(own and e.vastaaja == own)} for e in examples],
            sources_used=[s.id for s in sources] if draft_mode else [],
            research_query=research_query,
            research=[{**p.to_dict(), "abstract": p.abstract[:800], "used": p.id in used_research,
                       "kaytto": used_research.get(p.id, "")} for p in papers],
            timings_ms={
                "esikasittely": int((t1 - t0) * 1000),
                "tutkimushaku": int((t_r - t1) * 1000),
                "luonnostelija": int((t2 - t_r) * 1000),
                "tarkastaja": int((t3 - t2) * 1000),
            },
        )
        return result, ps
