"""Kahden mallin putki: pseudonymisointi -> haku -> luonnostelija -> tarkastaja -> koodin tarkistukset."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from . import prompts
from .catalog import CatalogItem, Index
from .config import Settings
from .llm import LLM, parse_json
from .pseudonymize import Pseudonymized, pseudonymize
from .safety import Signal, detect_signals


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
    timings_ms: dict[str, int] = field(default_factory=dict)

    def to_dict(self, restore: Pseudonymized | None = None) -> dict:
        d = asdict(self)
        if restore is not None:  # nimet takaisin vain vastaajan näkymään
            d["outline"] = [restore.restore(x) for x in self.outline]
            d["notes"] = [restore.restore(x) for x in self.notes]
        return d


class Assistant:
    def __init__(self, settings: Settings, index: Index, llm: LLM):
        self.s = settings
        self.index = index
        self.llm = llm

    def _candidates(self, text: str) -> list[CatalogItem]:
        found = [it for it, score in self.index.search(text, k=self.s.candidates) if score > 0]
        crisis = [it for it in self.index.by_type("kriisi") if it not in found]
        return found + crisis  # kriisipalvelut ovat aina tarjolla

    def run(self, question: str) -> tuple[Result, Pseudonymized]:
        t0 = time.perf_counter()
        ps = pseudonymize(question)
        signals = detect_signals(question)  # paikallinen, alkuperäisestä tekstistä
        candidates = self._candidates(ps.text)
        cand_by_id = {c.id: c for c in candidates}
        t1 = time.perf_counter()

        # 1) Luonnostelija
        raw = self.llm.complete(
            model=self.s.drafter_model,
            system=prompts.DRAFTER_SYSTEM.format(max_links=self.s.max_links),
            user=prompts.drafter_user(ps.text, candidates),
        )
        draft = parse_json(raw)
        t2 = time.perf_counter()

        # 2) Tarkastaja (virhe ei kaada putkea, mutta näkyy vastaajalle)
        notes: list[str] = list(draft.get("huomiot", []))
        try:
            raw_c = self.llm.complete(
                model=self.s.checker_model,
                system=prompts.CHECKER_SYSTEM,
                user=prompts.checker_user(ps.text, candidates, draft),
            )
            check = parse_json(raw_c)
        except Exception as e:  # noqa: BLE001
            check = {"linkit": [], "lisaa": [], "huomiot": [], "kriisi": {"havaittu": False, "kuvaus": ""}}
            notes.append(f"Tarkastus epäonnistui ({type(e).__name__}). Tarkista linkit itse.")
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
                invalid.append(item_id)
                return
            if item_id in seen:
                return
            seen.add(item_id)
            links.append(
                LinkSuggestion(item.id, item.title, item.url, item.type, reason, source, note)
            )

        for l in draft.get("linkit", []):
            v = verdicts.get(l.get("id"), {})
            if v and v.get("hyvaksytty") is False:
                rejected.append({"id": l.get("id"), "syy": v.get("syy", "")})
                continue
            add(l.get("id", ""), l.get("perustelu", ""), "luonnostelija", v.get("syy", ""))
        for l in check.get("lisaa", []):
            add(l.get("id", ""), l.get("syy", ""), "tarkastaja")

        crisis = check.get("kriisi") or {"havaittu": False, "kuvaus": ""}
        if signals or crisis.get("havaittu"):
            for it in self.index.by_type("kriisi"):
                if it.id not in seen:
                    seen.add(it.id)
                    links.append(
                        LinkSuggestion(it.id, it.title, it.url, it.type,
                                       "Kysymyksessä on mahdollinen kriisisignaali.",
                                       "kriisi-automaatti"),
                    )
        # Kriisipalvelut aina ensimmäisiksi, muuten järjestys säilyy
        links.sort(key=lambda l: l.type != "kriisi")

        n_crisis = sum(1 for l in links if l.type == "kriisi")
        links = links[: self.s.max_links + n_crisis]
        notes += check.get("huomiot", [])
        if invalid:
            notes.append(f"Mallin ehdottamia tuntemattomia id:itä poistettiin: {', '.join(invalid)}")

        result = Result(
            pseudonymized_question=ps.text,
            pseudonym_counts=ps.counts,
            topics=draft.get("aiheet", []),
            asker=draft.get("kysyja", "ei tiedossa"),
            links=links,
            rejected=rejected,
            outline=draft.get("runko", []),
            notes=notes,
            signals=signals,
            crisis=crisis,
            invalid_ids=invalid,
            models={"luonnostelija": self.s.drafter_model, "tarkastaja": self.s.checker_model,
                    "provider": self.s.provider},
            timings_ms={
                "esikasittely": int((t1 - t0) * 1000),
                "luonnostelija": int((t2 - t1) * 1000),
                "tarkastaja": int((t3 - t2) * 1000),
            },
        )
        return result, ps
