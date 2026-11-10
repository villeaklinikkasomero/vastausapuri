"""Mallikerros. Toteutus valitaan asetuksella (APURI_PROVIDER)."""
from __future__ import annotations

import json
import re
from typing import Protocol

from .config import Settings


class LLM(Protocol):
    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1500) -> str: ...


class BedrockLLM:
    """Claude Amazon Bedrockissa Converse-rajapinnan kautta.

    Kutsu on tilaton: Bedrock ei tallenna kehotteita eikä vastauksia.
    Pidä mallikutsujen lokitus (model invocation logging) pois päältä tai ohjaa
    se säätiön omaan salattuun säilöön.
    """

    def __init__(self, region: str):
        import boto3  # vain kun Bedrockia oikeasti käytetään

        self._client = boto3.client("bedrock-runtime", region_name=region)

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 3000) -> str:
        resp = self._client.converse(
            modelId=model,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
        )
        parts = resp["output"]["message"]["content"]
        return "".join(p.get("text", "") for p in parts)


class MockLLM:
    """Kehitys- ja testikäyttöön. Ei verkkoyhteyttä, ei dataa minnekään.

    Luonnostelijana valitsee kolme ensimmäistä ehdokasta, tarkastajana hyväksyy kaiken.
    """

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 1500) -> str:
        if "TARKASTAJA" in system:
            ids = re.findall(r'"id":\s*"([^"]+)"', user.split("LUONNOS:", 1)[-1])
            return json.dumps(
                {
                    "linkit": [{"id": i, "hyvaksytty": True, "syy": "Sopii aiheeseen."} for i in ids],
                    "lisaa": [],
                    "ongelmat": [],
                    "huomiot": [],
                    "kriisi": {"havaittu": False, "kuvaus": ""},
                },
                ensure_ascii=False,
            )
        if "HAKULAUSE" in system:
            return json.dumps({"tarpeen": True, "haku": "anabolic androgenic steroids adverse effects"})
        ids = re.findall(r"^- id: (\S+)", user, flags=re.M)[:3]
        meta = {
            "aiheet": ["(mock) aihe"],
            "kysyja": "ei tiedossa",
            "linkit": [{"id": i, "perustelu": "(mock) Haun perusteella osuvin."} for i in ids],
            "runko": [
                "Kiitä yhteydenotosta ja tunnista kysyjän tilanne.",
                "Vastaa varsinaiseen kysymykseen.",
                "Ehdota sopivia sisältöjä ja jatko-ohjausta.",
            ],
            "huomiot": [],
        }
        if "<luonnos>" not in system:
            return json.dumps(meta, ensure_ascii=False)
        refs = "\n".join(f"- [[{i}]]" for i in ids)
        body = (
            "Hei,\n\nkiitos kysymyksestäsi. (Mock-luonnos: oikea malli kirjoittaa tähän vastauksen.)\n\n"
            "Tässä on tietoa aiheesta [TARKISTA: mock-väite]. Lisää luettavaa:\n"
            f"{refs}\n\nYstävällisin terveisin,\nNeuvontapalvelu"
        )
        return f"<meta>\n{json.dumps(meta, ensure_ascii=False)}\n</meta>\n<luonnos>\n{body}\n</luonnos>"


def get_llm(settings: Settings) -> LLM:
    if settings.provider == "bedrock":
        return BedrockLLM(settings.aws_region)
    if settings.provider == "mock":
        return MockLLM()
    raise ValueError(f"Tuntematon APURI_PROVIDER: {settings.provider}")


def parse_json(text: str) -> dict:
    """Poimii mallin vastauksesta ensimmäisen JSON-olion."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Mallin vastauksesta ei löytynyt JSON-oliota")
    return json.loads(text[start : end + 1])


def parse_draft(text: str) -> tuple[dict, str]:
    """Erottaa luonnostelijan vastauksesta metatiedot (JSON) ja luonnostekstin."""
    m = re.search(r"<luonnos>\s*(.*?)\s*(?:</luonnos>|$)", text, re.S)
    if not m:
        raise ValueError("Mallin vastauksesta ei löytynyt luonnosta")
    draft = m.group(1).strip()
    meta_m = re.search(r"<meta>\s*(.*?)\s*</meta>", text, re.S)
    meta = parse_json(meta_m.group(1) if meta_m else text[: m.start()])
    return meta, draft
