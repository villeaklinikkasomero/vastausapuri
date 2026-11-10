"""Sisältökatalogi ja haku.

Katalogi on ainoa lähde linkeille: malli valitsee ehdokkaista id:n perusteella,
eikä se koskaan kirjoita URL-osoitetta itse.

Haku on MVP:ssä merkki-n-grammeihin perustuva TF-IDF. Se kestää suomen
taivutusta (juominen / juomisesta / juon) paremmin kuin sanahaku, eikä vaadi
ulkoisia palveluita. Myöhemmin rinnalle voi lisätä upotushaun (esim. Cohere
Embed Multilingual Bedrockissa).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CatalogItem:
    id: str
    title: str
    url: str
    description: str
    type: str  # "artikkeli" | "testi" | "oma-apu" | "palvelu" | "kriisi"
    topics: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()  # "itse" | "läheinen" | "ammattilainen" | "nuori"

    @property
    def search_text(self) -> str:
        return " ".join([self.title, self.description, " ".join(self.topics), " ".join(self.audience)])


def load_catalog(path: str | Path) -> list[CatalogItem]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = []
    seen = set()
    for r in raw:
        if r["id"] in seen:
            raise ValueError(f"Katalogissa on kaksi kohdetta samalla id:llä: {r['id']}")
        seen.add(r["id"])
        items.append(
            CatalogItem(
                id=r["id"],
                title=r["title"],
                url=r["url"],
                description=r["description"],
                type=r.get("type", "artikkeli"),
                topics=tuple(r.get("topics", [])),
                audience=tuple(r.get("audience", [])),
            )
        )
    return items


def _ngrams(text: str, n_min: int = 3, n_max: int = 5) -> Counter[str]:
    text = re.sub(r"\[[A-ZÅÄÖ]+_\d+\]", " ", text.lower())  # paikkamerkit pois
    grams: Counter[str] = Counter()
    for word in re.findall(r"[a-zåäö0-9]+", text):
        w = f" {word} "
        for n in range(n_min, n_max + 1):
            for i in range(len(w) - n + 1):
                grams[w[i : i + n]] += 1
    return grams


@dataclass
class Index:
    items: list[CatalogItem]
    _idf: dict[str, float] = field(default_factory=dict)
    _vecs: list[dict[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        docs = [_ngrams(it.search_text) for it in self.items]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d.keys())
        n = len(docs)
        self._idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self._vecs = [self._vec(d) for d in docs]

    def _vec(self, grams: Counter[str]) -> dict[str, float]:
        v = {g: (1 + math.log(c)) * self._idf.get(g, 0.0) for g, c in grams.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    def search(self, query: str, k: int = 10) -> list[tuple[CatalogItem, float]]:
        q = self._vec(_ngrams(query))
        scored = []
        for item, vec in zip(self.items, self._vecs):
            s = sum(w * vec.get(g, 0.0) for g, w in q.items())
            scored.append((item, s))
        scored.sort(key=lambda t: -t[1])
        return scored[:k]

    def by_type(self, type_: str) -> list[CatalogItem]:
        return [it for it in self.items if it.type == type_]

    def get(self, item_id: str) -> CatalogItem | None:
        return next((it for it in self.items if it.id == item_id), None)
