"""Pseudonymisointi ennen kuin teksti lähtee mallille.

Kaksi kerrosta:
1. Säännöt: henkilötunnus, sähköposti, puhelin, osoite, postinumero, päivämäärä,
   ikä, linkit, yleiset etunimet ja paikkakunnat.
2. Valinnainen NER: jos spaCyn suomimalli on asennettu (APURI_SPACY_MODEL),
   sen löytämät henkilöt ja paikat korvataan myös.

Paikkamerkit numeroidaan ([NIMI_1], [PAIKKA_2] ...) ja vastaavuustaulu jää vain
säätiön omaan ympäristöön, jotta nimet voidaan palauttaa vastaajan näkymässä.
Mikään menetelmä ei ole täydellinen: tämä on suojakerros, ei anonymisointi.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# --- Yleisiä suomalaisia etunimiä (laajennettava, esim. DVV:n nimiaineistosta) ---
_FIRST_NAMES = """
Aada Aino Aleksi Anna Anne Anni Antti Ari Arja Eero Eeva Eino Elina Elias Emilia Emma
Erkki Esa Hanna Hannu Harri Heikki Heli Helmi Henna Ilkka Irma Jaana Jani Janne Jari
Jarmo Jenna Jesse Johanna Jonna Jouni Juha Juho Jukka Jussi Kaija Kaisa Kalle Kari
Katja Kati Kimmo Kirsi Kristiina Lauri Leena Leo Liisa Linnea Maija Marja Marko Markku
Matti Merja Mika Mikko Minna Niklas Niko Noora Oliver Olli Outi Paavo Päivi Pasi Pekka
Pentti Petri Petteri Pia Pirjo Raija Riikka Ritva Sami Sanna Sari Satu Seppo Siiri
Sirpa Sofia Tapani Tapio Teemu Terhi Timo Tiina Toni Tuomas Tuula Tuomo Vesa Veera
Ville Aleksanteri Onni Väinö Eetu Ella Iida Saara Ilona Kaarina Jaakko Joonas
""".split()

# Paikkakuntien vartalot, koska taivutus muuttaa sanaa (Helsinki -> Helsingissä)
_PLACE_STEMS = """
Helsin Helsing Espoo Espoo Tamper Vanta Vantaa Oulu Oulu Turku Turu Turuu Jyväskyl
Lahti Lahde Lahte Kuopio Pori Joensuu Joensuu Lappeenran Hämeenlinn Vaasa Vaasa
Seinäjo Rovanie Mikkeli Mikkeli Kotka Kotkas Porvoo Kouvola Lohja Lohjal Hyvinkää
Nurmijärv Järvenpää Rauma Kajaani Kerava Kokkola Tuusula Kirkkonumm Kangasala
Savonlinn Ylöjärv Riihimä Raahe Imatra Iisalm Kemi Tornio Kauniai Sipoo Pietarsaa
""".split()

_NAME_SUFFIX = r"(?:n|a|ä|lle|lla|llä|lta|ltä|sta|stä|ssa|ssä|kin|han|hän|ille|ilta)?"


@dataclass
class Pseudonymized:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # paikkamerkki -> alkuperäinen

    def restore(self, text: str) -> str:
        """Palauttaa alkuperäiset arvot. Kutsutaan vain vastaajan näkymässä."""
        for ph, orig in sorted(self.mapping.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(ph, orig)
        return text

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ph in self.mapping:
            kind = ph.strip("[]").rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return out


# Järjestys on tärkeä: tarkimmat ensin, ettei esim. hetun numeroita tulkita päiväyksiksi.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("HETU", re.compile(r"\b\d{6}[-+A-FU-Ya-fu-y]\d{3}[0-9A-Ya-y]\b")),
    ("SPOSTI", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("LINKKI", re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)),
    ("PUHELIN", re.compile(r"(?<!\w)(?:\+358|00358|0)[\s-]?\d{1,3}(?:[\s-]?\d{2,4}){2,3}(?!\w)")),
    (
        "OSOITE",
        re.compile(
            r"\b[A-ZÅÄÖ][a-zåäö-]*(?:katu|kadu|tie|tien|polku|kuja|väylä|raitti|ranta|puistikko|"
            r"tori|kaari|rinne|mäki|piha)\w*\s+\d+\s?[A-Za-z]?(?:\s?\d+)?\b"
        ),
    ),
    ("POSTINRO", re.compile(r"\b\d{5}\b")),
    ("PVM", re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2,4})?(?!\d)")),
    ("IKÄ", re.compile(r"\b\d{1,3}\s?-?\s?(?:v\b|vuotia\w*|vuoden ikäi\w*)", re.I)),
    (
        "NIMI",
        re.compile(r"\b(?:" + "|".join(map(re.escape, _FIRST_NAMES)) + r")" + _NAME_SUFFIX + r"\b"),
    ),
    ("PAIKKA", re.compile(r"\b(?:" + "|".join(map(re.escape, set(_PLACE_STEMS))) + r")[a-zåäö]*\b")),
    # Allekirjoitus: "Terveisin Maija" tai "t. M" -> nimi
    (
        "NIMI",
        re.compile(r"(?<=\b[Tt]erveisin\s)[A-ZÅÄÖ][\wåäö-]+|(?<=\bt\.\s)[A-ZÅÄÖ][\wåäö-]*"),
    ),
    # "nimeni on X", "olen X" isolla alkukirjaimella
    ("NIMI", re.compile(r"(?<=\b[Nn]imeni on\s)[A-ZÅÄÖ][\wåäö-]+")),
]


def _load_ner():
    model = os.getenv("APURI_SPACY_MODEL")
    if not model:
        return None
    try:
        import spacy  # type: ignore

        return spacy.load(model)
    except Exception:  # pragma: no cover - valinnainen riippuvuus
        return None


_NER = _load_ner()
_NER_LABELS = {"PER": "NIMI", "PERSON": "NIMI", "LOC": "PAIKKA", "GPE": "PAIKKA"}


def pseudonymize(text: str) -> Pseudonymized:
    result = Pseudonymized(text=text)
    reverse: dict[str, str] = {}  # alkuperäinen -> paikkamerkki (sama arvo = sama merkki)
    counter: dict[str, int] = {}

    def placeholder(kind: str, original: str) -> str:
        key = f"{kind}:{original}"
        if key not in reverse:
            counter[kind] = counter.get(kind, 0) + 1
            ph = f"[{kind}_{counter[kind]}]"
            reverse[key] = ph
            result.mapping[ph] = original
        return reverse[key]

    out = text
    if _NER is not None:  # pragma: no cover
        doc = _NER(out)
        for ent in sorted(doc.ents, key=lambda e: -e.start_char):
            kind = _NER_LABELS.get(ent.label_)
            if kind:
                ph = placeholder(kind, ent.text)
                out = out[: ent.start_char] + ph + out[ent.end_char :]

    for kind, pattern in _RULES:
        out = pattern.sub(lambda m, k=kind: placeholder(k, m.group(0)), out)

    result.text = out
    return result
