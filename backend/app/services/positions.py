"""Wszystkie stanowiska do RADIO i dopasowania kontrolerów online: plik .ese EPWW, vacs-data, pliki .ese sąsiadów."""

import json
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AtcPosition
from .neighbours import listed_callsigns, nb_positions
from .radio import merge_positions


@lru_cache(maxsize=1)
def vacs_positions() -> list[dict]:
    try:
        return json.loads((settings.seed_dir / "vacs_epww.json").read_text("utf-8")).get("neighbour_positions", [])
    except (OSError, ValueError):
        return []


def all_positions(db: Session, airports: bool = False) -> list[dict]:
    """airports=True: także stanowiska lotnisk z plików sąsiadów, które nie obsługują żadnego sektora (karty lotnisk
    w AERODROME); RADIO, MAP i dopasowanie zalogowanych biorą listę bez nich."""
    ese = [{"callsign": p.callsign, "name": p.name, "frequency": p.frequency, "position_id": p.position_id,
            "prefix": p.prefix} for p in db.scalars(select(AtcPosition))]
    return merge_positions(ese, vacs_positions(), nb_positions(db, airports), listed_callsigns(db))
