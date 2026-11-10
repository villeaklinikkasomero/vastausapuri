"""Sisältökatalogi ja haku.

Katalogi on ainoa lähde linkeille: malli valitsee ehdokkaista id:n perusteella,
eikä se koskaan kirjoita URL-osoitetta itse.

Haku on merkki-n-grammeihin perustuva TF-IDF. Se kestää suomen taivutusta
(juominen / juomisesta / juon) paremmin kuin sanahaku, eikä vaadi ulkoisia
palveluita. Myöhemmin rinnalle voi lisätä upotushaun (esim. Cohere Embed
Multilingual Bedrockissa).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Sequence, TypeVar


@dataclass(frozen=True)
class CatalogItem:
    id: str
    title: str
    url: str
    description: str
    type: str  # "artikkeli" | "testi" | "oma-apu" | "palvelu" | "kriisi"
    topics: tuple[str, ...] = ()
    audience: tuple[str, ...] = ()  # "itse" | "läheinen" | "ammattilainen" | "nuori"
    signals: tuple[str, ...] = ()  # kriisikohteet: mihin signaalilajeihin tämä nostetaan automaattisesti
    content: str = ""  # sivun tekstiote (katalogityökalu täyttää), annetaan mallille lähteeksi

    @property
    def search_text(self) -> str:
        # Otsikko ja aiheet painavat enemmän kuin leipäteksti
        head = " ".join([self.title, self.title, " ".join(self.topics), " ".join(self.topics)])
        return " ".join([head, self.description, " ".join(self.audience), self.content[:1500]])


def load_catalog(path: str | Path) -> list[CatalogItem]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        CatalogItem(
            id=r["id"],
            title=r["title"],
            url=r["url"],
            description=r.get("description", ""),
            type=r.get("type", "artikkeli"),
            topics=tuple(r.get("topics", [])),
            audience=tuple(r.get("audience", [])),
            signals=tuple(r.get("signals", [])),
            content=r.get("content", ""),
        )
        for r in raw
    ]


def merge_catalogs(*catalogs: list[CatalogItem]) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    seen: set[str] = set()
    for cat in catalogs:
        for it in cat:
            if it.id in seen:
                raise ValueError(f"Katalogissa on kaksi kohdetta samalla id:llä: {it.id}")
            seen.add(it.id)
            items.append(it)
    return items


def _ngrams(text: str, n_min: int = 3, n_max: int = 5) -> Counter[str]:
    text = re.sub(r"\[[A-ZÅÄÖ]+_\d+\]", " ", text.lower())  # paikkamerkit pois
    grams: Counter[str] = Counter()
    for word in re.findall(r"[a-zåäö0-9-]+", text):
        w = f" {word} "
        for n in range(n_min, n_max + 1):
            for i in range(len(w) - n + 1):
                grams[w[i : i + n]] += 1
    return grams


T = TypeVar("T")


class TextIndex(Generic[T]):
    """Yleinen n-grammi-TF-IDF-indeksi mille tahansa olioille."""

    def __init__(self, objects: Sequence[T], texts: Sequence[str]):
        self.objects = list(objects)
        docs = [_ngrams(t) for t in texts]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(d.keys())
        n = max(len(docs), 1)
        self._idf = {g: math.log((1 + n) / (1 + c)) + 1 for g, c in df.items()}
        self._vecs = [self._vec(d) for d in docs]

    def _vec(self, grams: Counter[str]) -> dict[str, float]:
        v = {g: (1 + math.log(c)) * self._idf.get(g, 0.0) for g, c in grams.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / norm for g, x in v.items()}

    def search(self, query: str, k: int = 10, where=None) -> list[tuple[T, float]]:
        q = self._vec(_ngrams(query))
        scored = []
        for obj, vec in zip(self.objects, self._vecs):
            if where is not None and not where(obj):
                continue
            scored.append((obj, sum(w * vec.get(g, 0.0) for g, w in q.items())))
        scored.sort(key=lambda t: -t[1])
        return scored[:k]


class Index(TextIndex[CatalogItem]):
    def __init__(self, items: list[CatalogItem]):
        super().__init__(items, [it.search_text for it in items])
        self.items = self.objects

    def by_type(self, type_: str) -> list[CatalogItem]:
        return [it for it in self.items if it.type == type_]

    def get(self, item_id: str) -> CatalogItem | None:
        return next((it for it in self.items if it.id == item_id), None)
