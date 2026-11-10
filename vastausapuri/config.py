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
        default_factory=lambda: os.getenv("APURI_DRAFTER_MODEL", "eu.anthropic.claude-opus-5-5")
    )
    checker_model: str = field(
        default_factory=lambda: os.getenv("APURI_CHECKER_MODEL", "eu.anthropic.claude-sonnet-5-5")
    )
    catalog_path: Path = field(
        default_factory=lambda: Path(os.getenv("APURI_CATALOG", ROOT / "data" / "katalogi.json"))
    )
    # Montako ehdokasta haku antaa luonnostelijalle ja montako linkkiä enintään ehdotetaan
    candidates: int = int(os.getenv("APURI_CANDIDATES", "10"))
    max_links: int = int(os.getenv("APURI_MAX_LINKS", "5"))

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
