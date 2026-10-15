# zmienne środowiskowe wygrywają z lokalnym .env (tam zwykle POLIDS_AUTH_MODE=dev)

import os
import tempfile

import pytest

os.environ["POLIDS_AUTH_MODE"] = "none"
os.environ["POLIDS_PUBLIC_URL"] = "http://localhost:1337"
os.environ["POLIDS_SECRET_KEY"] = ""
os.environ["POLIDS_STATE_DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "state.db").replace("\\", "/")


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Pusta lista dostępu na każdy test (importy w środku, żeby nie tworzyć settings przed testami)."""
    from backend.app import access
    from backend.app.config import settings
    monkeypatch.setattr(settings, "state_database_url", f"sqlite:///{(tmp_path / 'state.db').as_posix()}")
    access.reset()
    yield access
    access.reset()


@pytest.fixture(autouse=True)
def lvp_memory():
    """Stan LVP ma histerezę (pamięć poprzedniego METAR); każdy test zaczyna bez historii."""
    from backend.app.services import lvp
    lvp.reset_memory()
    yield
    lvp.reset_memory()
