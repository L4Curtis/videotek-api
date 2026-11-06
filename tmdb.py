"""Client minimal de l'API TMDB (https://developer.themoviedb.org).

La clé est lue dans la variable d'environnement TMDB_CLE. Elle peut être
une clé d'API v3 (paramètre api_key) ou un jeton d'accès en lecture v4
(en-tête Authorization: Bearer, reconnu car il commence par « eyJ »).
"""
import os

import requests

URL_API = "https://api.themoviedb.org/3"
URL_AFFICHES = "https://image.tmdb.org/t/p/w500"
LANGUE = "fr-FR"
NB_ACTEURS = 10
DELAI_SECONDES = 8


class TmdbIndisponible(Exception):
    """TMDB ne répond pas, refuse la clé, ou la clé n'est pas configurée."""


class TmdbIntrouvable(Exception):
    """Le film demandé n'existe pas sur TMDB."""


def _appeler(chemin, **parametres):
    cle = os.environ.get("TMDB_CLE", "").strip()
    if not cle:
        raise TmdbIndisponible("clé TMDB non configurée (variable TMDB_CLE)")
    entetes = {"Accept": "application/json"}
    if cle.startswith("eyJ"):
        entetes["Authorization"] = f"Bearer {cle}"
    else:
        parametres["api_key"] = cle
    try:
        reponse = requests.get(f"{URL_API}{chemin}", params=parametres,
                               headers=entetes, timeout=DELAI_SECONDES)
    except requests.RequestException as exc:
        raise TmdbIndisponible(f"TMDB injoignable ({exc.__class__.__name__})") from exc
    if reponse.status_code == 404:
        raise TmdbIntrouvable("film introuvable sur TMDB")
    if reponse.status_code in (401, 403):
        raise TmdbIndisponible("clé TMDB refusée")
    if reponse.status_code != 200:
        raise TmdbIndisponible(f"TMDB a répondu {reponse.status_code}")
    try:
        return reponse.json()
    except ValueError as exc:
        raise TmdbIndisponible("réponse de TMDB illisible") from exc


def _annee(date_sortie):
    """« 2010-07-15 » → 2010 ; None si la date est absente."""
    if date_sortie and date_sortie[:4].isdigit():
        return int(date_sortie[:4])
    return None


def _affiche(chemin_image):
    return f"{URL_AFFICHES}{chemin_image}" if chemin_image else None


def rechercher(titre):
    """Films TMDB correspondant à un titre : [{tmdb_id, titre, annee, affiche}]."""
    resultat = _appeler("/search/movie", query=titre, language=LANGUE,
                        include_adult="false")
    return [
        {
            "tmdb_id": film["id"],
            "titre": film.get("title") or film.get("original_title") or "",
            "annee": _annee(film.get("release_date")),
            "affiche": _affiche(film.get("poster_path")),
        }
        for film in resultat.get("results", [])
    ]


def details(tmdb_id):
    """Détails et acteurs d'un film, convertis vers les champs d'un Film.

    Les genres sont renvoyés par leur nom : c'est l'API qui les associe aux
    catégories existantes (ou les crée).
    """
    film = _appeler(f"/movie/{tmdb_id}", language=LANGUE,
                    append_to_response="credits")
    description = (film.get("overview") or "").strip()
    if not description:  # pas de synopsis en français : on prend l'anglais
        try:
            anglais = _appeler(f"/movie/{tmdb_id}", language="en-US")
            description = (anglais.get("overview") or "").strip()
        except (TmdbIndisponible, TmdbIntrouvable):
            description = ""
    distribution = sorted(film.get("credits", {}).get("cast", []),
                          key=lambda acteur: acteur.get("order", 999))
    return {
        "tmdb_id": film["id"],
        "titre": film.get("title") or film.get("original_title") or "",
        "annee": _annee(film.get("release_date")),
        "genres": [genre["name"] for genre in film.get("genres", [])],
        "acteurs": [acteur["name"] for acteur in distribution[:NB_ACTEURS]],
        "description": description,
        "affiche": _affiche(film.get("poster_path")),
    }
