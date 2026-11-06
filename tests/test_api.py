"""Tests de l'API REST : toutes les routes, codes de retour et règles métier.

Données de départ (donnees/videotheque_test.json) :
  utilisateurs 1 admin, 2 alice, 3 bob, 4 chloe (bloquée)
  films 1 à 12 (1 Inception, 2 Matrix, 3 Interstellar, ...), catégories 1 à 11
  supports 1 (Inception, Blu-ray) et 6 (Interstellar) empruntés,
  3 (Inception) numérique ; emprunts 3 (bob) et 4 (alice) en cours
"""
import threading
from datetime import timedelta

import requests

import app as module_app
import stockage
import tmdb

ALICE = {"X-Utilisateur-Id": "2"}
CHLOE = {"X-Utilisateur-Id": "4"}


def nouvel_utilisateur(**champs):
    utilisateur = dict(nom_utilisateur="david", prenom="David", nom="Petit",
                       email="david@exemple.fr", annee_naissance=2000,
                       mot_de_passe="david-2026")
    utilisateur.update(champs)
    return utilisateur


# --------------------------------------------------------------------- stockage
def test_fichier_cree_avec_six_listes_vides(tmp_path, monkeypatch):
    chemin = tmp_path / "nouveau" / "videotheque.json"
    monkeypatch.setenv("VIDEOTHEQUE_FICHIER", str(chemin))
    assert stockage.initialiser() is True
    assert stockage.lire() == {nom: [] for nom in stockage.LISTES}
    assert stockage.initialiser() is False


def test_ecritures_simultanees_sous_verrou(fichier):
    def ajouter(numero):
        with stockage.transaction() as donnees:
            donnees["categories"].append(
                {"id": stockage.prochain_id(donnees["categories"]), "nom": f"Genre {numero}"})

    fils = [threading.Thread(target=ajouter, args=(n,)) for n in range(25)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    categories = stockage.lire()["categories"]
    assert len(categories) == 11 + 25
    assert len({c["id"] for c in categories}) == len(categories)


def test_exception_dans_transaction_rien_ecrit(fichier):
    avant = fichier.read_text(encoding="utf-8")
    try:
        with stockage.transaction() as donnees:
            donnees["films"].clear()
            raise RuntimeError("abandon")
    except RuntimeError:
        pass
    assert fichier.read_text(encoding="utf-8") == avant


def test_index_liste_les_routes(client):
    reponse = client.get("/api")
    assert reponse.status_code == 200
    assert "POST /api/emprunts" in reponse.get_json()["routes"]


def test_route_inconnue_en_json(client):
    reponse = client.get("/api/inexistant")
    assert reponse.status_code == 404
    assert reponse.get_json() == {"erreur": "Ressource inexistante"}


# ----------------------------------------------------------- inscription / connexion
def test_inscription(client, donnees):
    reponse = client.post("/api/utilisateurs", json=nouvel_utilisateur())
    assert reponse.status_code == 201
    corps = reponse.get_json()
    assert corps["id"] == 5 and corps["role"] == "utilisateur" and corps["bloque"] is False
    assert "mot_de_passe" not in corps
    enregistre = donnees()["utilisateurs"][-1]
    assert enregistre["mot_de_passe"].startswith("$2b$")  # haché, jamais en clair


def test_inscription_doublons(client):
    assert client.post("/api/utilisateurs",
                       json=nouvel_utilisateur(nom_utilisateur="ALICE")).status_code == 409
    assert client.post("/api/utilisateurs",
                       json=nouvel_utilisateur(email="Bob@Exemple.fr")).status_code == 409


def test_inscription_champs_invalides(client):
    for champs in ({"email": "pas-un-email"}, {"mot_de_passe": "court"},
                   {"annee_naissance": 1850}, {"annee_naissance": "2000"},
                   {"nom_utilisateur": "a b"}, {"prenom": ""}):
        assert client.post("/api/utilisateurs",
                           json=nouvel_utilisateur(**champs)).status_code == 400, champs
    assert client.post("/api/utilisateurs", data="pas du json").status_code == 400


def test_connexion(client):
    reponse = client.post("/api/connexion",
                          json={"nom_utilisateur": "alice", "mot_de_passe": "alice-2026"})
    assert reponse.status_code == 200
    assert reponse.get_json() == {"id": 2, "nom_utilisateur": "alice", "role": "utilisateur"}


def test_connexion_refusee(client):
    mauvais = {"nom_utilisateur": "alice", "mot_de_passe": "mauvais-mdp"}
    inconnu = {"nom_utilisateur": "personne", "mot_de_passe": "alice-2026"}
    bloque = {"nom_utilisateur": "chloe", "mot_de_passe": "chloe-2026"}
    assert client.post("/api/connexion", json=mauvais).status_code == 401
    assert client.post("/api/connexion", json=inconnu).status_code == 401
    assert client.post("/api/connexion", json=bloque).status_code == 403
    assert client.post("/api/connexion", json={}).status_code == 400


def test_entete_utilisateur_verifie(client):
    assert client.get("/api/films", headers=ALICE).status_code == 200
    assert client.get("/api/films", headers=CHLOE).status_code == 403  # compte bloqué
    assert client.get("/api/films", headers={"X-Utilisateur-Id": "99"}).status_code == 401
    assert client.get("/api/films", headers={"X-Utilisateur-Id": "abc"}).status_code == 400


# ------------------------------------------------------------------ utilisateurs
def test_lister_et_consulter_utilisateurs(client):
    utilisateurs = client.get("/api/utilisateurs").get_json()
    assert [u["nom_utilisateur"] for u in utilisateurs] == ["admin", "alice", "bob", "chloe"]
    assert all("mot_de_passe" not in u for u in utilisateurs)
    alice = client.get("/api/utilisateurs/2").get_json()
    assert alice["liste_envies"] == [3, 7] and alice["films_vus"] == [1, 4, 6]
    assert client.get("/api/utilisateurs/99").status_code == 404


def test_bloquer_debloquer(client):
    assert client.put("/api/utilisateurs/3/bloque", json={"bloque": True}).get_json()["bloque"]
    assert client.post("/api/connexion", json={"nom_utilisateur": "bob",
                                               "mot_de_passe": "bob-2026"}).status_code == 403
    assert client.put("/api/utilisateurs/3/bloque", json={"bloque": False}).status_code == 200
    assert client.put("/api/utilisateurs/3/bloque", json={"bloque": "oui"}).status_code == 400
    assert client.put("/api/utilisateurs/99/bloque", json={"bloque": True}).status_code == 404


def test_liste_envies(client):
    reponse = client.post("/api/utilisateurs/2/envies/12")
    assert reponse.status_code == 200 and reponse.get_json() == {"liste_envies": [3, 7, 12]}
    assert client.post("/api/utilisateurs/2/envies/12").get_json()["liste_envies"] == [3, 7, 12]
    assert client.delete("/api/utilisateurs/2/envies/3").get_json() == {"liste_envies": [7, 12]}
    assert client.post("/api/utilisateurs/2/envies/99").status_code == 404
    assert client.post("/api/utilisateurs/4/envies/1").status_code == 403  # bloquée


def test_films_vus(client):
    assert client.post("/api/utilisateurs/3/vus/1").get_json() == {"films_vus": [2, 8, 10, 1]}
    assert client.delete("/api/utilisateurs/3/vus/2").get_json() == {"films_vus": [8, 10, 1]}


# ------------------------------------------------------------------------ films
def titres(reponse):
    return [film["titre"] for film in reponse.get_json()]


def test_recherche_titre_et_acteur(client):
    assert titres(client.get("/api/films?q=amelie")) == ["Le Fabuleux Destin d'Amélie Poulain"]
    assert titres(client.get("/api/films?q=DICAPRIO")) == ["Inception"]
    assert titres(client.get("/api/films?q=michael caine")) == ["Interstellar"]
    assert client.get("/api/films?q=zzz").get_json() == []


def test_filtres_annee_et_genre(client):
    assert sorted(titres(client.get("/api/films?annee=2001"))) == [
        "Le Fabuleux Destin d'Amélie Poulain",
        "Le Seigneur des anneaux : La Communauté de l'anneau",
        "Le Voyage de Chihiro"]
    assert titres(client.get("/api/films?categorie=3&annee=1994")) == ["Le Roi lion"]
    assert client.get("/api/films?annee=deux-mille").status_code == 400


def test_tris(client):
    par_titre = titres(client.get("/api/films"))
    assert par_titre[:3] == ["Inception", "Interstellar", "Intouchables"]
    par_note = client.get("/api/films?tri=note").get_json()
    notes = [f["note_moyenne"] for f in par_note]
    assert notes[:4] == [5.0, 5.0, 5.0, 4.5] and notes[-1] is None
    assert [f["titre"] for f in par_note[:3]] == [  # 5,0 : départage par titre
        "Interstellar", "Le Voyage de Chihiro", "Matrix"]
    par_date = titres(client.get("/api/films?tri=date_ajout"))
    assert par_date[0] == "Le Seigneur des anneaux : La Communauté de l'anneau"
    assert client.get("/api/films?tri=prix").status_code == 400


def test_note_moyenne(client):
    inception = next(f for f in client.get("/api/films").get_json() if f["id"] == 1)
    assert inception["note_moyenne"] == 4.5 and inception["nb_avis"] == 2


def test_fiche_film(client):
    fiche = client.get("/api/films/1").get_json()
    assert fiche["titre"] == "Inception" and fiche["note_moyenne"] == 4.5
    assert [s["id"] for s in fiche["supports"]] == [1, 2, 3]
    assert {a["nom_utilisateur"] for a in fiche["avis"]} == {"alice", "bob"}
    assert client.get("/api/films/99").status_code == 404


def test_ajouter_film(client):
    reponse = client.post("/api/films", json={
        "titre": "  La Haine ", "annee": 1995, "categories": [6, 5],
        "acteurs": ["Vincent Cassel", " Hubert Koundé ", ""],
        "description": "Vingt-quatre heures en banlieue.", "affiche": None})
    assert reponse.status_code == 201
    film = reponse.get_json()
    assert film["id"] == 13 and film["titre"] == "La Haine" and film["tmdb_id"] is None
    assert film["acteurs"] == ["Vincent Cassel", "Hubert Koundé"]
    assert film["date_ajout"] == module_app.aujourdhui().isoformat()
    assert list(film) == ["id", "titre", "categories", "annee", "acteurs", "description",
                          "affiche", "tmdb_id", "date_ajout"]


def test_ajouter_film_invalide(client):
    assert client.post("/api/films", json={"annee": 2000}).status_code == 400
    assert client.post("/api/films", json={"titre": "X"}).status_code == 400
    assert client.post("/api/films", json={"titre": "X", "annee": 2000,
                                           "categories": [42]}).status_code == 400
    assert client.post("/api/films", json={"titre": "X", "annee": 2000,
                                           "affiche": "ftp://x"}).status_code == 400


def test_modifier_film(client):
    reponse = client.put("/api/films/2", json={"titre": "The Matrix", "categories": [1]})
    assert reponse.status_code == 200
    film = reponse.get_json()
    assert film["titre"] == "The Matrix" and film["categories"] == [1] and film["annee"] == 1999
    assert client.put("/api/films/2", json={}).status_code == 400
    assert client.put("/api/films/99", json={"titre": "X"}).status_code == 404


def test_supprimer_film_emprunte_refuse(client):
    assert client.delete("/api/films/1").status_code == 409  # Blu-ray emprunté par alice


def test_supprimer_film_et_ses_dependances(client, donnees):
    assert client.delete("/api/films/8").status_code == 200  # Retour vers le futur
    d = donnees()
    assert all(f["id"] != 8 for f in d["films"])
    assert all(s["film_id"] != 8 for s in d["supports"])
    assert all(a["film_id"] != 8 for a in d["avis"])
    assert all(e["support_id"] not in (13, 14) for e in d["emprunts"])
    assert 8 not in next(u for u in d["utilisateurs"] if u["id"] == 3)["films_vus"]
    assert client.delete("/api/films/8").status_code == 404


# -------------------------------------------------------------------- catégories
def test_categories(client, donnees):
    categories = client.get("/api/categories").get_json()
    assert categories[0]["nom"] == "Action" and len(categories) == 11
    cree = client.post("/api/categories", json={"nom": "Documentaire"})
    assert cree.status_code == 201 and cree.get_json() == {"id": 12, "nom": "Documentaire"}
    assert client.post("/api/categories", json={"nom": "comedie"}).status_code == 409
    assert client.post("/api/categories", json={"nom": ""}).status_code == 400
    assert client.put("/api/categories/12", json={"nom": "Documentaires"}).status_code == 200
    assert client.put("/api/categories/12", json={"nom": "Drame"}).status_code == 409
    assert client.put("/api/categories/99", json={"nom": "X"}).status_code == 404


def test_supprimer_categorie_la_retire_des_films(client, donnees):
    assert client.delete("/api/categories/10").status_code == 200  # Science-Fiction
    d = donnees()
    assert all(c["id"] != 10 for c in d["categories"])
    assert all(10 not in f["categories"] for f in d["films"])
    assert client.delete("/api/categories/10").status_code == 404


# ---------------------------------------------------------------------- supports
def test_ajouter_supports(client):
    physique = client.post("/api/films/4/supports",
                           json={"type": "physique", "format": "Blu-ray", "lien": "ignoré"})
    assert physique.status_code == 201
    assert physique.get_json() == {"id": 20, "film_id": 4, "type": "physique",
                                   "format": "Blu-ray", "disponible": True, "lien": None}
    numerique = client.post("/api/films/4/supports",
                            json={"type": "numerique", "format": "MP4", "lien": "/media/amelie.mp4"})
    assert numerique.get_json()["type"] == "numérique"
    assert client.post("/api/films/4/supports",
                       json={"type": "numérique", "format": "MP4"}).status_code == 400
    assert client.post("/api/films/4/supports",
                       json={"type": "VHS", "format": "VHS"}).status_code == 400
    assert client.post("/api/films/99/supports",
                       json={"type": "physique", "format": "DVD"}).status_code == 404


def test_modifier_support(client):
    assert client.put("/api/supports/2", json={"format": "DVD collector"}).get_json()["format"] \
        == "DVD collector"
    abime = client.put("/api/supports/2", json={"disponible": False}).get_json()
    assert abime["disponible"] is False
    assert client.put("/api/supports/2", json={"disponible": True}).get_json()["disponible"]
    numerique = client.put("/api/supports/2", json={"type": "numérique", "lien": "/f.mkv"})
    assert numerique.get_json()["lien"] == "/f.mkv"
    assert client.put("/api/supports/3", json={"disponible": False}).status_code == 400
    assert client.put("/api/supports/1", json={"type": "numérique",
                                               "lien": "/x"}).status_code == 409
    assert client.put("/api/supports/1", json={"disponible": True}).status_code == 409
    assert client.put("/api/supports/1", json={}).status_code == 400
    assert client.put("/api/supports/99", json={"format": "DVD"}).status_code == 404


def test_supprimer_support(client):
    assert client.delete("/api/supports/1").status_code == 409  # emprunté
    assert client.delete("/api/supports/2").status_code == 200
    assert client.delete("/api/supports/2").status_code == 404


# ---------------------------------------------------------------------- emprunts
def test_lister_emprunts(client):
    tous = client.get("/api/emprunts").get_json()
    assert len(tous) == 4 and tous[0]["date_retour_effective"] is None
    de_bob = client.get("/api/emprunts?utilisateur_id=3").get_json()
    assert [e["id"] for e in de_bob] == [3, 2]  # en cours d'abord
    assert de_bob[0]["titre"] == "Interstellar" and de_bob[0]["film_id"] == 3
    assert client.get("/api/emprunts?utilisateur_id=99").status_code == 404


def test_emprunter(client, donnees):
    reponse = client.post("/api/emprunts", json={"utilisateur_id": 3, "support_id": 2})
    assert reponse.status_code == 201
    emprunt = reponse.get_json()
    debut = module_app.aujourdhui()
    assert emprunt["date_emprunt"] == debut.isoformat()
    assert emprunt["date_retour_prevue"] == (debut + timedelta(days=14)).isoformat()
    assert emprunt["date_retour_effective"] is None and emprunt["film_id"] == 1
    support = next(s for s in donnees()["supports"] if s["id"] == 2)
    assert support["disponible"] is False


def test_emprunts_refuses(client):
    assert client.post("/api/emprunts",
                       json={"utilisateur_id": 3, "support_id": 1}).status_code == 409
    assert client.post("/api/emprunts",
                       json={"utilisateur_id": 3, "support_id": 3}).status_code == 409  # numérique
    assert client.post("/api/emprunts",
                       json={"utilisateur_id": 4, "support_id": 2}).status_code == 403  # bloquée
    assert client.post("/api/emprunts",
                       json={"utilisateur_id": 3, "support_id": 99}).status_code == 404
    assert client.post("/api/emprunts", json={"utilisateur_id": 3}).status_code == 400


def test_rendre(client, donnees):
    assert client.put("/api/emprunts/4/retour", json={"utilisateur_id": 3}).status_code == 404
    reponse = client.put("/api/emprunts/4/retour", json={"utilisateur_id": 2})
    assert reponse.status_code == 200
    assert reponse.get_json()["date_retour_effective"] == module_app.aujourdhui().isoformat()
    assert next(s for s in donnees()["supports"] if s["id"] == 1)["disponible"] is True
    assert client.put("/api/emprunts/4/retour", json={"utilisateur_id": 2}).status_code == 409


# -------------------------------------------------------------------------- avis
def test_avis_cree_puis_remplace(client, donnees):
    cree = client.post("/api/films/2/avis",
                       json={"utilisateur_id": 2, "note": 3, "commentaire": "Pas mal"})
    assert cree.status_code == 201
    avis = cree.get_json()
    remplace = client.post("/api/films/2/avis",
                           json={"utilisateur_id": 2, "note": 5, "commentaire": ""})
    assert remplace.get_json()["id"] == avis["id"]
    assert remplace.get_json()["note"] == 5 and remplace.get_json()["commentaire"] is None
    assert len([a for a in donnees()["avis"] if a["film_id"] == 2]) == 2


def test_avis_invalides(client):
    for note in (0, 6, "5", None):
        assert client.post("/api/films/2/avis",
                           json={"utilisateur_id": 2, "note": note}).status_code == 400
    assert client.post("/api/films/99/avis",
                       json={"utilisateur_id": 2, "note": 4}).status_code == 404


# ---------------------------------------------------------------------- TMDB
class FausseReponse:
    def __init__(self, code, contenu):
        self.status_code = code
        self._contenu = contenu

    def json(self):
        return self._contenu


DETAILS_FIGHT_CLUB = {
    "id": 550, "title": "Fight Club", "release_date": "1999-10-15", "overview": "",
    "poster_path": "/fight.jpg",
    "genres": [{"id": 18, "name": "Drame"}, {"id": 53, "name": "Thriller"},
               {"id": 9648, "name": "Mystère"}],
    "credits": {"cast": [{"name": "Edward Norton", "order": 1},
                         {"name": "Brad Pitt", "order": 0}]},
}


def faux_tmdb(monkeypatch, cle="cle-de-test"):
    """Remplace requests.get par un faux TMDB ; renvoie la liste des appels."""
    appels = []

    def get(url, params=None, headers=None, timeout=None):
        appels.append({"url": url, "params": params, "headers": headers})
        if url.endswith("/search/movie"):
            return FausseReponse(200, {"results": [
                {"id": 550, "title": "Fight Club", "release_date": "1999-10-15",
                 "poster_path": "/fight.jpg"},
                {"id": 27205, "title": "Inception", "release_date": "2010-07-15",
                 "poster_path": None}]})
        if url.endswith("/movie/550"):
            if params.get("language") == "en-US":
                return FausseReponse(200, {"overview": "An insomniac office worker..."})
            return FausseReponse(200, DETAILS_FIGHT_CLUB)
        return FausseReponse(404, {"status_message": "not found"})

    monkeypatch.setattr(tmdb.requests, "get", get)
    monkeypatch.setenv("TMDB_CLE", cle)
    return appels


def test_recherche_tmdb(client, monkeypatch):
    appels = faux_tmdb(monkeypatch)
    reponse = client.get("/api/tmdb?titre=fight")
    assert reponse.status_code == 200
    assert reponse.get_json() == [
        {"tmdb_id": 550, "titre": "Fight Club", "annee": 1999,
         "affiche": "https://image.tmdb.org/t/p/w500/fight.jpg", "film_id": None},
        {"tmdb_id": 27205, "titre": "Inception", "annee": 2010, "affiche": None, "film_id": 1}]
    assert appels[0]["params"]["api_key"] == "cle-de-test"
    assert appels[0]["params"]["language"] == "fr-FR"
    assert client.get("/api/tmdb").status_code == 400


def test_jeton_v4_dans_l_entete(client, monkeypatch):
    appels = faux_tmdb(monkeypatch, cle="eyJhbGciOiJIUzI1NiJ9.jeton")
    client.get("/api/tmdb?titre=fight")
    assert appels[0]["headers"]["Authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.jeton"
    assert "api_key" not in appels[0]["params"]


def test_import_tmdb(client, monkeypatch, donnees):
    faux_tmdb(monkeypatch)
    reponse = client.post("/api/tmdb/import", json={"tmdb_id": 550})
    assert reponse.status_code == 201
    film = reponse.get_json()
    assert film["titre"] == "Fight Club" and film["annee"] == 1999 and film["tmdb_id"] == 550
    assert film["acteurs"] == ["Brad Pitt", "Edward Norton"]
    assert film["description"] == "An insomniac office worker..."  # repli en anglais
    assert film["categories"] == [6, 11, 12]  # Drame, Thriller existants + Mystère créé
    assert {"id": 12, "nom": "Mystère"} in donnees()["categories"]
    assert client.post("/api/tmdb/import", json={"tmdb_id": 550}).status_code == 409


def test_import_tmdb_erreurs(client, monkeypatch):
    assert client.post("/api/tmdb/import", json={"tmdb_id": 550}).status_code == 502  # pas de clé
    assert client.get("/api/tmdb?titre=fight").status_code == 502
    faux_tmdb(monkeypatch)
    assert client.post("/api/tmdb/import", json={"tmdb_id": 27205}).status_code == 409
    assert client.post("/api/tmdb/import", json={"tmdb_id": 999999}).status_code == 404
    assert client.post("/api/tmdb/import", json={"tmdb_id": "550"}).status_code == 400


def test_tmdb_injoignable(client, monkeypatch):
    def panne(*args, **kwargs):
        raise requests.ConnectionError("réseau coupé")

    monkeypatch.setattr(tmdb.requests, "get", panne)
    monkeypatch.setenv("TMDB_CLE", "cle-de-test")
    reponse = client.get("/api/tmdb?titre=fight")
    assert reponse.status_code == 502 and "injoignable" in reponse.get_json()["erreur"]
