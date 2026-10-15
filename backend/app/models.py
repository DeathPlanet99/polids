from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Aerodrome(Base):
    __tablename__ = "aerodromes"

    icao: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    city: Mapped[str | None] = mapped_column(String(120))
    iata: Mapped[str | None] = mapped_column(String(3))

    runways: Mapped[list["Runway"]] = relationship(back_populates="aerodrome", cascade="all, delete-orphan")
    frequencies: Mapped[list["Frequency"]] = relationship(back_populates="aerodrome", cascade="all, delete-orphan")


class Runway(Base):
    """Jeden kierunek pasa (np. 29), żeby łatwo liczyć składowe wiatru."""

    __tablename__ = "runways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aerodrome_icao: Mapped[str] = mapped_column(ForeignKey("aerodromes.icao"), index=True)
    designator: Mapped[str] = mapped_column(String(4))
    opposite: Mapped[str | None] = mapped_column(String(4))
    heading_true: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    width_m: Mapped[float | None] = mapped_column(Float)
    surface: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    preferred: Mapped[int] = mapped_column(Integer, default=0)

    aerodrome: Mapped[Aerodrome] = relationship(back_populates="runways")


class Frequency(Base):
    """Częstotliwość lotniska z AIP (AD 2.18, AIP MIL AD 4.18, AIP VFR AD 4.8-4.9) albo, dla lądowisk spoza AIP,
    z lotniska.dlapilota.pl (data/seed/frequencies.csv)."""

    __tablename__ = "frequencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aerodrome_icao: Mapped[str] = mapped_column(ForeignKey("aerodromes.icao"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # służba: TWR, APP, ATIS, AFIS, RADIO, PAR…
    description: Mapped[str | None] = mapped_column(String(120))  # znak wywoławczy
    mhz: Mapped[str] = mapped_column(String(10))
    hours: Mapped[str | None] = mapped_column(Text)  # godziny pracy jak w AIP: zima UTC, w nawiasie lato
    note: Mapped[str | None] = mapped_column(Text)  # uwaga z AIP, np. EPRA: TWR cywilna i wojskowa na tej samej
    source: Mapped[str | None] = mapped_column(String(80))

    aerodrome: Mapped[Aerodrome] = relationship(back_populates="frequencies")


class AircraftType(Base):
    __tablename__ = "aircraft_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    icao: Mapped[str] = mapped_column(String(4), index=True)
    iata: Mapped[str | None] = mapped_column(String(3))
    manufacturer: Mapped[str | None] = mapped_column(String(120), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    aircraft_type: Mapped[str | None] = mapped_column(String(30))
    engine_type: Mapped[str | None] = mapped_column(String(30))
    engine_count: Mapped[int | None] = mapped_column(Integer)
    wtc: Mapped[str | None] = mapped_column(String(1))
    recat: Mapped[str | None] = mapped_column(String(1))
    wingspan: Mapped[float | None] = mapped_column(Float)
    length: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    mtow: Mapped[float | None] = mapped_column(Float)
    mlw: Mapped[float | None] = mapped_column(Float)
    ceiling_ft: Mapped[float | None] = mapped_column(Float)
    vmo_kt: Mapped[float | None] = mapped_column(Float)
    mmo: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(String(4))  # opis ICAO DOC 8643, np. L2J
    use: Mapped[str | None] = mapped_column(String(2))  # H/M/... z ICAO_Aircraft.json
    remarks: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(40))


class Callsign(Base):
    __tablename__ = "callsigns"

    icao: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    telephony: Mapped[str | None] = mapped_column(String(120), index=True)
    country: Mapped[str | None] = mapped_column(String(80))
    category: Mapped[str | None] = mapped_column(String(10))  # CARGO / MIL (z GRpluginOperatorInfo.txt)
    source: Mapped[str | None] = mapped_column(String(40))


class NavPoint(Base):
    """Punkt nawigacyjny: VOR, NDB, DME, FIX (z OurAirports albo z pliku sektorowego .sct)."""

    __tablename__ = "nav_points"
    # /api/nav/points: rodzaj + bbox, bez indeksu full scan ~290k wierszy
    __table_args__ = (Index("ix_nav_points_kind_lat_lon", "kind", "lat", "lon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ident: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(120))
    frequency: Mapped[str | None] = mapped_column(String(12))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(40))


class AirwaySegment(Base):
    """Odcinek drogi lotniczej (z airway.txt EuroScope)."""

    __tablename__ = "airway_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    airway: Mapped[str] = mapped_column(String(10), index=True)
    level: Mapped[str | None] = mapped_column(String(1))  # B / H / L
    from_ident: Mapped[str] = mapped_column(String(10))
    to_ident: Mapped[str] = mapped_column(String(10))
    from_lat: Mapped[float] = mapped_column(Float)
    from_lon: Mapped[float] = mapped_column(Float)
    to_lat: Mapped[float] = mapped_column(Float)
    to_lon: Mapped[float] = mapped_column(Float)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60), default="INNE")
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class AtcPosition(Base):
    """Stanowisko ATC z sekcji [POSITIONS] pliku .ese."""

    __tablename__ = "atc_positions"

    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    frequency: Mapped[str] = mapped_column(String(10))
    position_id: Mapped[str] = mapped_column(String(10), index=True)
    prefix: Mapped[str | None] = mapped_column(String(10))
    suffix: Mapped[str | None] = mapped_column(String(10))


class ImportLog(Base):
    """Które pliki z data/import zostały już wczytane (żeby nie importować ich przy każdym starcie)."""

    __tablename__ = "import_log"

    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    mtime: Mapped[float] = mapped_column(Float)
    result: Mapped[str | None] = mapped_column(Text)


class Sector(Base):
    """Sektor przestrzeni z pliku .ese EPWW, wczytany z data/seed/sectors.geojson (source EPWW; geometria
    jako GeoJSON)."""

    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fir: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(60), index=True)
    lower_ft: Mapped[int] = mapped_column(Integer)
    upper_ft: Mapped[int] = mapped_column(Integer)
    owners: Mapped[str] = mapped_column(Text)  # identyfikatory stanowisk w kolejności przejmowania, rozdzielone ":"
    geometry: Mapped[str] = mapped_column(Text)  # GeoJSON Polygon


class NbSector(Base):
    """Sektor FIR-u sąsiedniego z jego własnego pliku .ese (data/import/neighbours), tylko w rejonie Polski;
    wczytany z data/seed/sectors.geojson.

    owners to już znaki stanowisk (identyfikatory z [POSITIONS] są ważne tylko w obrębie jednego pliku)."""

    __tablename__ = "nb_sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(8), index=True)  # klucz pliku sąsiada: EDWW, LKAA, UKBV…
    fir: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(60))
    lower_ft: Mapped[int] = mapped_column(Integer)
    upper_ft: Mapped[int] = mapped_column(Integer)
    owners: Mapped[str] = mapped_column(Text)  # znaki stanowisk w kolejności przejmowania, rozdzielone ":"
    active: Mapped[str] = mapped_column(Text, default="")  # warunki ACTIVE "ICAO:pas" rozdzielone spacją
    geometry: Mapped[str] = mapped_column(Text)  # GeoJSON Polygon


class NbPosition(Base):
    """Stanowisko z pliku .ese sąsiada (bez stanowisk EP**): z kolejności przejmowania sektorów FIR-ów pakietu albo
    lotniska i FIR-u pakietu (prefiksy NB_SOURCES[…]["airports"], np. EDDF_TWR do karty lotniska w AERODROME)."""

    __tablename__ = "nb_positions"

    source: Mapped[str] = mapped_column(String(8), primary_key=True)
    callsign: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    frequency: Mapped[str] = mapped_column(String(10))
    prefix: Mapped[str | None] = mapped_column(String(10))
