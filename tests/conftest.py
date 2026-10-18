import pytest

from stockage import STOCKAGE


@pytest.fixture(autouse=True)
def _sans_verification_de_signature(monkeypatch):
    """Les tests existants postent des événements sans signature ; ceux de la signature la rétablissent."""
    monkeypatch.setenv("WEBHOOK_SIGNATURE_OFF", "1")


@pytest.fixture(scope="session", autouse=True)
def _stockage_pret():
    """Avec DATABASE_URL, toute la suite tourne sur Postgres : on ouvre la base une fois."""
    STOCKAGE.preparer()
    STOCKAGE.vider()
    yield
