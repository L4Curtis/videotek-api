"""Règles des comptes utilisateurs, partagées par l'API et creer_admin.py.

Les fonctions `erreur_*` renvoient un message d'erreur, ou None si la valeur
est valide. Les mots de passe sont hachés avec bcrypt et ne sont jamais
stockés en clair.
"""
import re
from functools import lru_cache

import bcrypt

ROLE_ADMIN = "administrateur"
ROLE_UTILISATEUR = "utilisateur"

RE_NOM_UTILISATEUR = re.compile(r"[A-Za-z0-9._-]{3,30}")
RE_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MOT_DE_PASSE_MIN = 8
MOT_DE_PASSE_MAX_OCTETS = 72  # au-delà, bcrypt refuse le mot de passe
ANNEE_NAISSANCE_MIN = 1900


def erreur_nom_utilisateur(valeur):
    if not isinstance(valeur, str) or not RE_NOM_UTILISATEUR.fullmatch(valeur):
        return ("Nom d'utilisateur invalide : 3 à 30 caractères "
                "(lettres sans accent, chiffres, « . », « _ » ou « - »)")
    return None


def erreur_email(valeur):
    if not isinstance(valeur, str) or len(valeur) > 120 or not RE_EMAIL.fullmatch(valeur):
        return "Adresse e-mail invalide"
    return None


def erreur_mot_de_passe(valeur):
    if not isinstance(valeur, str) or len(valeur) < MOT_DE_PASSE_MIN:
        return f"Mot de passe trop court : {MOT_DE_PASSE_MIN} caractères minimum"
    if len(valeur.encode("utf-8")) > MOT_DE_PASSE_MAX_OCTETS:
        return f"Mot de passe trop long : {MOT_DE_PASSE_MAX_OCTETS} octets maximum"
    return None


def erreur_annee_naissance(valeur, annee_courante):
    if isinstance(valeur, bool) or not isinstance(valeur, int) \
            or not ANNEE_NAISSANCE_MIN <= valeur <= annee_courante:
        return f"Année de naissance invalide : entre {ANNEE_NAISSANCE_MIN} et {annee_courante}"
    return None


def hacher(mot_de_passe):
    """Renvoie le haché bcrypt (chaîne) d'un mot de passe."""
    return bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verifier(mot_de_passe, hache):
    """Compare un mot de passe saisi au haché bcrypt stocké."""
    try:
        return bcrypt.checkpw(mot_de_passe.encode("utf-8"), hache.encode("ascii"))
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _hache_factice():
    return hacher("mot-de-passe-factice")


def verifier_factice(mot_de_passe):
    """Même coût qu'une vraie vérification, pour un utilisateur inconnu :
    le temps de réponse ne révèle pas si le nom d'utilisateur existe."""
    verifier(mot_de_passe, _hache_factice())
    return False
