"""Asetukset ympäristömuuttujista. Mallit vaihdetaan tästä, ei koodista."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # "bedrock" tuotantoon/pilottiin, "mock" kehitykseen ja testeihin
    provider: str = field(default_factory=lambda: os.getenv("APURI_PROVIDER", "mock"))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "eu-north-1"))
    # Vain eu.-profiilit sallitaan, jotta käsittely pysyy EU:ssa
    drafter_model: str = field(
        default_factory=lambda: os.getenv("APURI_DRAFTER_MODEL", "eu.anthropic.claude-opus-4-6-v1")
    )
    checker_model: str = field(
        default_factory=lambda: os.getenv("APURI_CHECKER_MODEL", "eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
    )
    hankkeet_dir: Path = field(
        default_factory=lambda: Path(os.getenv("APURI_HANKKEET", ROOT / "hankkeet"))
    )
    # Vanhojen vastausten kansio (ei Git-repoon). Tyhjä = hankkeet-kansio.
    data_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["APURI_DATA_DIR"]) if os.getenv("APURI_DATA_DIR") else None
    )
    # Tutkimushaku: "europepmc" tai "pois". Oletuksena päällä Bedrock-tilassa, pois mock-tilassa.
    research: str = field(
        default_factory=lambda: os.getenv(
            "APURI_TUTKIMUS", "europepmc" if os.getenv("APURI_PROVIDER", "mock") == "bedrock" else "pois"
        )
    )
    # Montako ehdokasta haku antaa luonnostelijalle ja montako linkkiä enintään ehdotetaan
    candidates: int = field(default_factory=lambda: int(os.getenv("APURI_CANDIDATES", "12")))
    max_links: int = field(default_factory=lambda: int(os.getenv("APURI_MAX_LINKS", "5")))
    # Montako samankaltaista vanhaa vastausta annetaan luonnostelijalle esimerkiksi
    examples: int = field(default_factory=lambda: int(os.getenv("APURI_EXAMPLES", "3")))
    # Montako lähdeotetta (sivujen tekstiä) annetaan luonnokselle
    sources: int = field(default_factory=lambda: int(os.getenv("APURI_SOURCES", "4")))

    def validate(self) -> None:
        if self.provider == "bedrock":
            for m in (self.drafter_model, self.checker_model):
                if not m.startswith("eu."):
                    raise ValueError(
                        f"Malli '{m}' ei ole EU-inferenssiprofiili. "
                        "Sallittuja ovat vain eu.-alkuiset profiilit."
                    )


def get_settings() -> Settings:
    s = Settings()
    s.validate()
    return s
