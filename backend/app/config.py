"""Ustawienia aplikacji. Każdą wartość można nadpisać zmienną środowiskową
z prefiksem POLIDS_ albo wpisem w pliku .env w katalogu głównym projektu."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"

VERSION = "0.12.2"


def csv_set(value: str) -> set[str]:
    return {v.strip().upper() for v in value.split(",") if v.strip()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POLIDS_", env_file=ROOT_DIR / ".env", extra="ignore")

    name: str = "PolIDS"
    tagline: str = "Polish Integrated Display System · VATSIM PL vACC"
    public_url: str = "http://localhost:1337"  # bez / na końcu; z tego jest redirect_uri dla VATSIM Connect

    # none | dev (formularz testowy, tylko localhost) | vatsim
    auth_mode: str = "none"
    secret_key: str = ""  # wymagany przy vatsim, min. 32 znaki
    session_days: int = 7
    vatsim_auth_url: str = "https://auth.vatsim.net"  # sandbox: https://auth-dev.vatsim.net
    vatsim_client_id: str = ""
    vatsim_client_secret: str = ""
    # CID-y administratorów, po przecinku. Resztę dostępu nadaje admin w zakładce ADMIN.
    auth_admin_cids: str = ""

    database_url: str = f"sqlite:///{(DATA_DIR / 'polids.db').as_posix()}"
    # lista dostępu - osobno, bo polids.db jest przebudowywana z repo
    state_database_url: str = f"sqlite:///{(DATA_DIR / 'state' / 'polids-state.db').as_posix()}"
    seed_dir: Path = DATA_DIR / "seed"
    docs_dir: Path = DATA_DIR / "docs"
    photos_dir: Path = DATA_DIR / "photos"
    frontend_dir: Path = ROOT_DIR / "frontend"

    default_aerodrome: str = "EPWA"
    http_timeout: float = 10.0
    weather_cache_seconds: int = 120
    notam_cache_seconds: int = 600

    # METAR: "vatsim" (metar.vatsim.net, to samo co widzą piloci) albo "awc" (aviationweather.gov)
    metar_source: str = "vatsim"
    awc_api: str = "https://aviationweather.gov/api/data"
    vatsim_metar_url: str = "https://metar.vatsim.net/{icao}"

    # NOTAM z serwera PL vACC (ten sam format co w pliku konfiguracyjnym vATIS/Euroscope)
    notam_server: str = "https://cv.plvacc.pl/"
    notam_prefix: str = "notam/get-icao-format?icao="
    notam_suffix: str = "&read=true"

    # Zewnętrzne strony osadzane w zakładkach (zmieniaj przy nowym AIRAC)
    aip_ifr_url: str = "https://docs.pansa.pl/ais/eaipifr/AIRAC%20AMDT%2010-26_2026_10_01/index-v2.html"
    aip_vfr_url: str = "https://docs.pansa.pl/ais/eaipvfr/AIRAC%20AMDT%20VFR%2010-26_2026_10_01/index-v2.html"
    aip_mil_url: str = "https://docs.pansa.pl/ais/eaipmil/AIRAC%20AMDT%20MIL%2010-26_2026_10_01/index-v2.html"
    inop_url: str = "https://om.plvacc.pl/"
    imgw_url: str = "https://awiacja.imgw.pl/"
    sectors_url: str = "https://plvacc.pl/acc-sectors/"
    # Frazeologia: "Say Again? The Phraseology Database" w trybie pełnoekranowym (link od Marka, runda 5)
    phraseology_url: str = ("https://learningzone.eurocontrol.int/ilp/pages/media-wbtfullscreen.jsf?mediaId=5453741"
                            "&mediaName=Say+Again%3F+The+Phraseology+Database+%5BATC-PHRA%5D&mediaLanguage=English%20(GB)"
                            "&catalogId=230552&wbtPath=https://learningzone.eurocontrol.int/ilp/customs/PHRA/Default.aspx"
                            "&aspectStyle=&aspectControlled=&openMode=same-page")
    performance_db_url: str = "https://learningzone.eurocontrol.int/ilp/customs/ATCPFDB/default.aspx"

    # Mapa: podkład lotniczy. OpenAIP wymaga darmowego klucza API (https://www.openaip.net)
    openaip_api_key: str = ""
    # Klucz CARTO do podkładów mapy (opcjonalny; trzymaj go tylko w lokalnym pliku .env, nie w repozytorium)
    carto_api_key: str = ""

    # vIFF (VATSIM IFPS/ETFMS/CDM, https://api.viffsys.com): odloty z EOBT/CTOT/statusem i ruch vs przepustowość sektorów.
    # Odczyt działa bez klucza. Klucz (nagłówek x-api-key) ustaw tylko, jeśli dostaniesz go od autora vIFF.
    viff_url: str = "https://api.viffsys.com"
    viff_api_key: str = ""
    viff_cache_seconds: int = 60
    # Lotniska z A-CDM (TOBT/TSAT/TTOT); vIFF podaje to też sam (isCdm), ta lista jest zapasowa
    viff_cdm_airports: str = "EPWA"
    # Prefiks "traffic volumes" (sektorów) vIFF dla FIR EPWW
    viff_sector_prefix: str = "EP"

    @property
    def public_origin(self) -> str:
        u = urlsplit(self.public_url)
        return f"{u.scheme}://{u.netloc}"

    @property
    def user_agent(self) -> str:
        # nagłówki HTTP muszą być w ASCII (Żółw -> Zolw)
        name = unicodedata.normalize("NFKD", self.name.replace("ł", "l").replace("Ł", "L"))
        name = re.sub(r"[^A-Za-z0-9._-]", "", name.encode("ascii", "ignore").decode()) or "polids"
        origin = self.public_origin.encode("ascii", "ignore").decode()
        return f"{name}/{VERSION} (VATSIM PL vACC; +{origin})"


settings = Settings()
