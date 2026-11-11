"""Käyttäjät ja oikeudet.

Kaksi lähdettä:
1. Käyttäjärekisteri levyllä (<APURI_DATA_DIR tai hankkeet>/kayttajat.json), jota ylläpitäjät
   hallitsevat ylläpitosivulla. Salasanat tallennetaan vain PBKDF2-tiivisteinä.
2. Ympäristömuuttujat APURI_USERS ja APURI_ADMINS: käynnistys- ja varakäyttäjät (esim. ensimmäinen
   ylläpitäjä). Niitä ei voi muokata ylläpitosivulta. Jos sama tunnus on molemmissa, rekisteri voittaa.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ITERATIONS = 200_000
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
MIN_PASSWORD = 10
_lock = threading.Lock()


@dataclass(frozen=True)
class User:
    name: str
    projects: frozenset[str] | None  # None = kaikki hankkeet
    admin: bool
    active: bool = True
    source: str = "rekisteri"  # "rekisteri" | "ympäristö"


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


class Registry:
    def __init__(self, path: Path):
        self.path = path

    # --- tallennus -------------------------------------------------------------------
    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # --- ympäristömuuttujat ------------------------------------------------------------
    @staticmethod
    def _env() -> dict[str, tuple[str, frozenset[str] | None]]:
        """APURI_USERS="nimi:salasana[:hanke1|hanke2],..." """
        out = {}
        for entry in filter(None, (os.getenv("APURI_USERS") or "").split(",")):
            parts = [x.strip() for x in entry.strip().split(":")]
            if len(parts) >= 2 and parts[0] and parts[1]:
                proj = (frozenset(p for p in parts[2].split("|") if p)
                        if len(parts) > 2 and parts[2] not in ("", "*") else None)
                out[parts[0]] = (parts[1], proj)
        return out

    @staticmethod
    def _env_admins() -> set[str]:
        return {a.strip() for a in (os.getenv("APURI_ADMINS") or "").split(",") if a.strip()}

    # --- kyselyt ----------------------------------------------------------------------
    def any_users(self) -> bool:
        return bool(self._env()) or bool(self._read())

    def get(self, name: str) -> User | None:
        rec = self._read().get(name)
        if rec is not None:
            proj = rec.get("hankkeet")
            return User(name, frozenset(proj) if proj is not None else None,
                        bool(rec.get("admin")) or name in self._env_admins(), bool(rec.get("aktiivinen", True)))
        env = self._env().get(name)
        if env is not None:
            return User(name, env[1], name in self._env_admins(), True, "ympäristö")
        return None

    def verify(self, name: str, password: str) -> User | None:
        rec = self._read().get(name)
        if rec is not None:
            ok = hmac.compare_digest(_hash(password, rec["suola"]), rec["tiiviste"])
            user = self.get(name)
            return user if ok and user and user.active else None
        env = self._env().get(name)
        if env is not None and secrets.compare_digest(password.encode(), env[0].encode()):
            return self.get(name)
        return None

    def list(self) -> list[dict]:
        data = self._read()
        out = []
        for name, rec in sorted(data.items()):
            out.append({"nimi": name, "hankkeet": rec.get("hankkeet"), "admin": bool(rec.get("admin")),
                        "aktiivinen": bool(rec.get("aktiivinen", True)), "lahde": "rekisteri",
                        "muokattu": rec.get("muokattu")})
        for name, (_, proj) in sorted(self._env().items()):
            if name not in data:
                out.append({"nimi": name, "hankkeet": sorted(proj) if proj is not None else None,
                            "admin": name in self._env_admins(), "aktiivinen": True, "lahde": "ympäristö",
                            "muokattu": None})
        return out

    # --- muutokset --------------------------------------------------------------------
    def upsert(self, name: str, *, password: str | None = None, projects: list[str] | None = None,
               admin: bool = False, active: bool = True, create: bool = False) -> None:
        if not USERNAME.match(name):
            raise ValueError("Käyttäjätunnus: 2–40 merkkiä, pienet kirjaimet, numerot sekä . _ -")
        if password is not None and len(password) < MIN_PASSWORD:
            raise ValueError(f"Salasanan pitää olla vähintään {MIN_PASSWORD} merkkiä.")
        with _lock:
            data = self._read()
            if create and name in data:
                raise ValueError("Käyttäjätunnus on jo käytössä.")
            if not create and name not in data:
                raise ValueError("Käyttäjää ei löydy rekisteristä.")
            if create and password is None:
                raise ValueError("Uudelle käyttäjälle tarvitaan salasana.")
            rec = data.get(name, {"luotu": int(time.time())})
            if password is not None:
                rec["suola"] = secrets.token_hex(16)
                rec["tiiviste"] = _hash(password, rec["suola"])
            rec["hankkeet"] = sorted(set(projects)) if projects is not None else None
            rec["admin"] = bool(admin)
            rec["aktiivinen"] = bool(active)
            rec["muokattu"] = int(time.time())
            data[name] = rec
            self._write(data)

    def change_password(self, name: str, old: str, new: str) -> None:
        if self._read().get(name) is None:
            raise ValueError("Tämän tunnuksen salasana on asetettu Renderin ympäristömuuttujissa, "
                             "eikä sitä voi vaihtaa täällä.")
        if not self.verify(name, old):
            raise ValueError("Nykyinen salasana on väärin.")
        rec = self._read()[name]
        self.upsert(name, password=new, projects=rec.get("hankkeet"), admin=bool(rec.get("admin")),
                    active=bool(rec.get("aktiivinen", True)))

    def delete(self, name: str) -> None:
        with _lock:
            data = self._read()
            if name not in data:
                raise ValueError("Käyttäjää ei löydy rekisteristä.")
            del data[name]
            self._write(data)
