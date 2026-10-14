from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


# Podnieś przy każdej zmianie tabel (albo sposobu importu danych): stara baza zostanie zbudowana od nowa z plików w repo.
# 3: indeks nav_points, poprawione sklejanie granic sektorów .ese. 4: sektory sąsiadów z ich plików .ese.
# 5: sąsiedzi – jeden układ pasów na lotnisko, bez sektorów bez OWNER. 6: połączenie z wersją serwerową.
# 7: pasy, częstotliwości (godziny, źródło) i elewacje lotnisk z AIP. 8: uwagi do częstotliwości, elewacje lądowisk
# spoza AIP.
SCHEMA_VERSION = 8


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    if url.startswith("sqlite:///"):
        from pathlib import Path

        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("sqlite"):
        # max_overflow=-1: async endpointy trzymają połączenie w trakcie await (VATSIM, METAR). Z domyślnym
        # limitem 5+10 przy >15 równoczesnych zapytaniach checkout blokował event loop i serwer stawał.
        eng = create_engine(url, connect_args={"check_same_thread": False}, pool_size=20, max_overflow=-1)
    else:
        eng = create_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragma(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
