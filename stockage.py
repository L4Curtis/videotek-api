"""Accès au fichier JSON de la vidéothèque.

C'est le seul module qui lit et écrit le fichier : l'API et le script
creer_admin.py passent tous les deux par lui.

Le chemin du fichier est lu dans la variable d'environnement
VIDEOTHEQUE_FICHIER, ce qui permet de passer du fichier de test
(préproduction) au fichier réel sans modifier le code.

Toute écriture se fait dans un bloc `with transaction() as donnees:` :
le fichier est relu, modifié par l'appelant puis réécrit en entier, sous un
verrou. Si l'appelant lève une exception dans le bloc, rien n'est écrit.
"""
import json
import os
import tempfile
import threading
from contextlib import contextmanager

try:  # verrou inter-processus (Linux) : protège aussi creer_admin.py
    import fcntl
except ImportError:  # Windows (tests hors conteneur) : verrou de thread seul
    fcntl = None

LISTES = ("utilisateurs", "films", "categories", "supports", "emprunts", "avis")
FICHIER_PAR_DEFAUT = "/donnees/videotheque.json"

_verrou = threading.Lock()


def chemin_fichier():
    """Chemin du fichier JSON utilisé (variable d'environnement)."""
    return os.environ.get("VIDEOTHEQUE_FICHIER") or FICHIER_PAR_DEFAUT


@contextmanager
def _verrous(chemin):
    """Verrou de thread (requêtes simultanées) + verrou de fichier (processus)."""
    with _verrou:
        if fcntl is None:
            yield
            return
        with open(chemin + ".lock", "a", encoding="utf-8") as fichier_verrou:
            fcntl.flock(fichier_verrou, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fichier_verrou, fcntl.LOCK_UN)


def _lire_fichier(chemin):
    with open(chemin, encoding="utf-8") as fichier:
        donnees = json.load(fichier)
    for nom in LISTES:  # tolère un fichier auquel il manque une liste
        donnees.setdefault(nom, [])
    return donnees


def _ecrire_fichier(chemin, donnees):
    """Écriture atomique : fichier temporaire puis remplacement du fichier."""
    dossier = os.path.dirname(os.path.abspath(chemin))
    descripteur, temporaire = tempfile.mkstemp(
        dir=dossier, prefix=".videotheque-", suffix=".tmp")
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
            json.dump(donnees, fichier, ensure_ascii=False, indent=2)
            fichier.write("\n")
            fichier.flush()
            os.fsync(fichier.fileno())
        os.replace(temporaire, chemin)
    except BaseException:
        if os.path.exists(temporaire):
            os.remove(temporaire)
        raise


def initialiser():
    """Crée le fichier avec les six listes vides s'il n'existe pas.

    Renvoie True si le fichier vient d'être créé.
    """
    chemin = chemin_fichier()
    if os.path.exists(chemin):
        return False
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    with _verrous(chemin):
        if os.path.exists(chemin):
            return False
        _ecrire_fichier(chemin, {nom: [] for nom in LISTES})
        return True


def lire():
    """Lecture seule : renvoie tout le contenu du fichier.

    Pas besoin de verrou : l'écriture remplace le fichier d'un coup, une
    lecture voit donc toujours un fichier complet.
    """
    initialiser()
    return _lire_fichier(chemin_fichier())


@contextmanager
def transaction():
    """Relit le fichier, le donne à modifier à l'appelant, puis le réécrit."""
    chemin = chemin_fichier()
    initialiser()
    with _verrous(chemin):
        donnees = _lire_fichier(chemin)
        yield donnees
        _ecrire_fichier(chemin, donnees)


def prochain_id(liste):
    """Identifiant d'un nouvel objet : plus grand identifiant de la liste + 1."""
    return max((objet["id"] for objet in liste), default=0) + 1
