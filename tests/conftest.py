import pytest


@pytest.fixture(autouse=True)
def _sans_verification_de_signature(monkeypatch):
    """Les tests existants postent des événements sans signature ; ceux de la signature la rétablissent."""
    monkeypatch.setenv("WEBHOOK_SIGNATURE_OFF", "1")
