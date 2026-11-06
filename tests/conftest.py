"""Fixtures : chaque test travaille sur une copie du fichier de test."""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
FICHIER_TEST = RACINE / "donnees" / "videotheque_test.json"

# Avant l'import de l'application : jamais le vrai fichier, jamais la vraie clé TMDB
os.environ["VIDEOTHEQUE_FICHIER"] = str(Path(tempfile.mkdtemp()) / "videotheque.json")
os.environ.pop("TMDB_CLE", None)

import app as module_app  # noqa: E402


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    chemin = tmp_path / "videotheque.json"
    shutil.copy(FICHIER_TEST, chemin)
    monkeypatch.setenv("VIDEOTHEQUE_FICHIER", str(chemin))
    monkeypatch.delenv("TMDB_CLE", raising=False)
    return chemin


@pytest.fixture
def client(fichier):
    module_app.app.config.update(TESTING=True)
    return module_app.app.test_client()


@pytest.fixture
def donnees(fichier):
    """Relit le fichier JSON pour vérifier ce qui a vraiment été enregistré."""
    return lambda: json.loads(fichier.read_text(encoding="utf-8"))
