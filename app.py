"""API REST de la vidéothèque (tiers données).

C'est le seul composant qui lit et écrit le fichier JSON (module stockage).
Toutes les données reçues et renvoyées sont en JSON.

L'API ne gère pas de session : pour chaque opération d'un utilisateur, le
serveur web transmet l'identifiant de l'utilisateur connecté (en-tête
X-Utilisateur-Id et, selon la route, champ utilisateur_id), et l'API vérifie
que ce compte existe et n'est pas bloqué. Les routes marquées (admin) ne sont
appelées que depuis les pages d'administration du serveur web, qui vérifie
le rôle de l'utilisateur.

Codes de retour : 200 succès, 201 objet créé, 400 données invalides,
401 identifiants incorrects, 403 compte bloqué, 404 objet inexistant,
409 conflit (doublon, support indisponible ou emprunté), 502 TMDB injoignable.
"""
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

import comptes
import stockage
import tmdb

DUREE_EMPRUNT_JOURS = 14
FUSEAU = ZoneInfo("Europe/Paris")
TRIS = ("titre", "note", "date_ajout")

app = Flask(__name__)
app.json.ensure_ascii = False  # accents lisibles dans les réponses
app.json.sort_keys = False     # ordre des champs de la conception conservé

stockage.initialiser()  # crée le fichier JSON (six listes vides) s'il n'existe pas


# =============================================================================
# Erreurs : toujours une réponse JSON {"erreur": "message"}
# =============================================================================
class ErreurApi(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def erreur(code, message):
    raise ErreurApi(code, message)


@app.errorhandler(ErreurApi)
def _erreur_api(exc):
    return jsonify(erreur=exc.message), exc.code


MESSAGES_HTTP = {
    400: "Requête invalide",
    404: "Ressource inexistante",
    405: "Méthode non autorisée sur cette ressource",
}


@app.errorhandler(HTTPException)
def _erreur_http(exc):
    return jsonify(erreur=MESSAGES_HTTP.get(exc.code, exc.name)), exc.code


@app.errorhandler(Exception)
def _erreur_interne(exc):
    app.logger.exception("Erreur interne")
    return jsonify(erreur="Erreur interne de l'API"), 500


# =============================================================================
# Outils
# =============================================================================
def aujourdhui():
    return datetime.now(FUSEAU).date()


def normaliser(texte):
    """Minuscules sans accents : « Amélie » → « amelie » (recherche, tri)."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in decompose if not unicodedata.combining(c)).casefold().strip()


def trouver(liste, identifiant):
    return next((objet for objet in liste if objet["id"] == identifiant), None)


def corps_json():
    corps = request.get_json(silent=True)
    if not isinstance(corps, dict):
        erreur(400, "Corps de requête JSON (objet) attendu")
    return corps


def chaine(corps, champ, obligatoire=True, longueur_max=200):
    valeur = corps.get(champ)
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        if obligatoire:
            erreur(400, f"Champ « {champ} » obligatoire")
        return None
    if not isinstance(valeur, str):
        erreur(400, f"Champ « {champ} » : chaîne de caractères attendue")
    valeur = valeur.strip()
    if len(valeur) > longueur_max:
        erreur(400, f"Champ « {champ} » : {longueur_max} caractères maximum")
    return valeur


def entier(valeur, champ, minimum=None, maximum=None):
    if isinstance(valeur, bool) or not isinstance(valeur, int):
        erreur(400, f"Champ « {champ} » : entier attendu")
    if (minimum is not None and valeur < minimum) or (maximum is not None and valeur > maximum):
        bornes = f"entre {minimum} et {maximum}" if maximum is not None else f"≥ {minimum}"
        erreur(400, f"Champ « {champ} » : valeur {bornes} attendue")
    return valeur


def parametre_entier(nom):
    """Paramètre d'URL facultatif converti en entier (None s'il est absent)."""
    valeur = request.args.get(nom, "").strip()
    if not valeur:
        return None
    if not valeur.isdigit():
        erreur(400, f"Paramètre « {nom} » : entier attendu")
    return int(valeur)


def sans_mot_de_passe(utilisateur):
    return {cle: valeur for cle, valeur in utilisateur.items() if cle != "mot_de_passe"}


def verifier_utilisateur(donnees, utilisateur_id, code_si_absent=404):
    """Le compte doit exister (404, ou 401 pour l'appelant) et ne pas être bloqué (403)."""
    utilisateur = trouver(donnees["utilisateurs"], utilisateur_id)
    if utilisateur is None:
        erreur(code_si_absent, "Utilisateur inexistant")
    if utilisateur["bloque"]:
        erreur(403, "Compte bloqué par un administrateur")
    return utilisateur


def support_emprunte(donnees, support_id):
    return any(e["support_id"] == support_id and e["date_retour_effective"] is None
               for e in donnees["emprunts"])


def statistiques_avis(donnees):
    """{film_id: (note moyenne arrondie à 0,1, nombre d'avis)}."""
    notes = {}
    for avis in donnees["avis"]:
        notes.setdefault(avis["film_id"], []).append(avis["note"])
    return {film_id: (round(sum(liste) / len(liste), 1), len(liste))
            for film_id, liste in notes.items()}


def avec_note(film, statistiques):
    moyenne, nombre = statistiques.get(film["id"], (None, 0))
    return {**film, "note_moyenne": moyenne, "nb_avis": nombre}


def emprunt_detaille(emprunt, donnees):
    """Emprunt + champs calculés utiles à l'affichage (film, format, emprunteur)."""
    support = trouver(donnees["supports"], emprunt["support_id"])
    film = trouver(donnees["films"], support["film_id"]) if support else None
    utilisateur = trouver(donnees["utilisateurs"], emprunt["utilisateur_id"])
    return {
        **emprunt,
        "film_id": film["id"] if film else None,
        "titre": film["titre"] if film else None,
        "format": support["format"] if support else None,
        "nom_utilisateur": utilisateur["nom_utilisateur"] if utilisateur else None,
    }


# =============================================================================
# Vérification du compte transmis par le serveur web
# =============================================================================
@app.before_request
def verifier_appelant():
    """Si l'en-tête X-Utilisateur-Id est présent (utilisateur connecté sur le
    site), le compte doit exister (401) et ne pas être bloqué (403)."""
    g.appelant = None
    valeur = request.headers.get("X-Utilisateur-Id")
    if valeur is None:
        return
    if not valeur.strip().isdigit():
        erreur(400, "En-tête X-Utilisateur-Id invalide")
    g.appelant = verifier_utilisateur(stockage.lire(), int(valeur), code_si_absent=401)


@app.get("/api")
def index():
    """Description de l'API (sert aussi au test de santé du conteneur)."""
    routes = sorted(
        ((regle.rule, methode) for regle in app.url_map.iter_rules()
         if regle.rule.startswith("/api") for methode in regle.methods - {"HEAD", "OPTIONS"}),
    )
    return jsonify(service="API REST de la vidéothèque",
                   routes=[f"{methode} {regle}" for regle, methode in routes]), 200


# =============================================================================
# Utilisateurs et connexion
# =============================================================================
@app.post("/api/utilisateurs")
def inscription():
    corps = corps_json()
    nom_utilisateur = chaine(corps, "nom_utilisateur", longueur_max=30)
    prenom = chaine(corps, "prenom", longueur_max=50)
    nom = chaine(corps, "nom", longueur_max=50)
    email = chaine(corps, "email", longueur_max=120)
    mot_de_passe = corps.get("mot_de_passe")
    annee_naissance = corps.get("annee_naissance")
    for message in (comptes.erreur_nom_utilisateur(nom_utilisateur),
                    comptes.erreur_email(email),
                    comptes.erreur_annee_naissance(annee_naissance, aujourdhui().year),
                    comptes.erreur_mot_de_passe(mot_de_passe)):
        if message:
            erreur(400, message)
    hache = comptes.hacher(mot_de_passe)  # hors verrou : bcrypt est volontairement lent

    with stockage.transaction() as donnees:
        for autre in donnees["utilisateurs"]:
            if autre["nom_utilisateur"].casefold() == nom_utilisateur.casefold():
                erreur(409, "Ce nom d'utilisateur est déjà utilisé")
            if autre["email"].casefold() == email.casefold():
                erreur(409, "Cette adresse e-mail est déjà utilisée")
        utilisateur = {
            "id": stockage.prochain_id(donnees["utilisateurs"]),
            "nom_utilisateur": nom_utilisateur,
            "prenom": prenom,
            "nom": nom,
            "email": email,
            "annee_naissance": annee_naissance,
            "mot_de_passe": hache,
            "role": comptes.ROLE_UTILISATEUR,
            "bloque": False,
            "liste_envies": [],
            "films_vus": [],
        }
        donnees["utilisateurs"].append(utilisateur)
    return jsonify(sans_mot_de_passe(utilisateur)), 201


@app.post("/api/connexion")
def connexion():
    corps = corps_json()
    nom_utilisateur = corps.get("nom_utilisateur")
    mot_de_passe = corps.get("mot_de_passe")
    if not isinstance(nom_utilisateur, str) or not nom_utilisateur.strip() \
            or not isinstance(mot_de_passe, str) or not mot_de_passe:
        erreur(400, "Nom d'utilisateur et mot de passe obligatoires")
    utilisateur = next((u for u in stockage.lire()["utilisateurs"]
                        if u["nom_utilisateur"].casefold() == nom_utilisateur.strip().casefold()),
                       None)
    if utilisateur is None:
        comptes.verifier_factice(mot_de_passe)
        erreur(401, "Nom d'utilisateur ou mot de passe incorrect")
    if not comptes.verifier(mot_de_passe, utilisateur["mot_de_passe"]):
        erreur(401, "Nom d'utilisateur ou mot de passe incorrect")
    if utilisateur["bloque"]:
        erreur(403, "Compte bloqué par un administrateur")
    return jsonify(id=utilisateur["id"], nom_utilisateur=utilisateur["nom_utilisateur"],
                   role=utilisateur["role"]), 200


@app.get("/api/utilisateurs")
def lister_utilisateurs():
    """(admin) Liste des utilisateurs, sans les mots de passe."""
    return jsonify([sans_mot_de_passe(u) for u in stockage.lire()["utilisateurs"]]), 200


@app.get("/api/utilisateurs/<int:utilisateur_id>")
def consulter_utilisateur(utilisateur_id):
    utilisateur = trouver(stockage.lire()["utilisateurs"], utilisateur_id)
    if utilisateur is None:
        erreur(404, "Utilisateur inexistant")
    return jsonify(sans_mot_de_passe(utilisateur)), 200


@app.put("/api/utilisateurs/<int:utilisateur_id>/bloque")
def bloquer_utilisateur(utilisateur_id):
    """(admin) Bloque ou débloque un compte : {"bloque": true | false}."""
    bloque = corps_json().get("bloque")
    if not isinstance(bloque, bool):
        erreur(400, "Champ « bloque » : true ou false attendu")
    with stockage.transaction() as donnees:
        utilisateur = trouver(donnees["utilisateurs"], utilisateur_id)
        if utilisateur is None:
            erreur(404, "Utilisateur inexistant")
        utilisateur["bloque"] = bloque
    return jsonify(sans_mot_de_passe(utilisateur)), 200


def modifier_liste(utilisateur_id, film_id, champ):
    """POST : ajoute le film à la liste ; DELETE : le retire."""
    with stockage.transaction() as donnees:
        utilisateur = verifier_utilisateur(donnees, utilisateur_id)
        liste = utilisateur[champ]
        if request.method == "POST":
            if trouver(donnees["films"], film_id) is None:
                erreur(404, "Film inexistant")
            if film_id not in liste:
                liste.append(film_id)
        elif film_id in liste:
            liste.remove(film_id)
    return jsonify({champ: liste}), 200


@app.route("/api/utilisateurs/<int:utilisateur_id>/envies/<int:film_id>",
           methods=["POST", "DELETE"])
def modifier_envies(utilisateur_id, film_id):
    return modifier_liste(utilisateur_id, film_id, "liste_envies")


@app.route("/api/utilisateurs/<int:utilisateur_id>/vus/<int:film_id>",
           methods=["POST", "DELETE"])
def modifier_vus(utilisateur_id, film_id):
    return modifier_liste(utilisateur_id, film_id, "films_vus")


# =============================================================================
# Films
# =============================================================================
@app.get("/api/films")
def lister_films():
    """Recherche (q : titre ou acteur), filtres (annee, categorie) et tri
    (titre, note, date_ajout). Chaque film est renvoyé avec sa note moyenne."""
    q = normaliser(request.args.get("q", ""))
    annee = parametre_entier("annee")
    categorie = parametre_entier("categorie")
    tri = request.args.get("tri", "").strip() or "titre"
    if tri not in TRIS:
        erreur(400, f"Paramètre « tri » : {', '.join(TRIS)}")

    donnees = stockage.lire()
    statistiques = statistiques_avis(donnees)
    films = [avec_note(film, statistiques) for film in donnees["films"]]
    if q:
        films = [f for f in films if q in normaliser(f["titre"])
                 or any(q in normaliser(acteur) for acteur in f["acteurs"])]
    if annee is not None:
        films = [f for f in films if f["annee"] == annee]
    if categorie is not None:
        films = [f for f in films if categorie in f["categories"]]

    films.sort(key=lambda f: normaliser(f["titre"]))  # tri stable : départage par titre
    if tri == "note":    # meilleures notes d'abord, films sans avis à la fin
        films.sort(key=lambda f: (f["note_moyenne"] is None, -(f["note_moyenne"] or 0)))
    elif tri == "date_ajout":  # plus récents d'abord
        films.sort(key=lambda f: f["date_ajout"], reverse=True)
    return jsonify(films), 200


@app.get("/api/films/<int:film_id>")
def fiche_film(film_id):
    """Fiche : film, note moyenne, supports et avis (avec le nom de leur auteur)."""
    donnees = stockage.lire()
    film = trouver(donnees["films"], film_id)
    if film is None:
        erreur(404, "Film inexistant")
    noms = {u["id"]: u["nom_utilisateur"] for u in donnees["utilisateurs"]}
    avis = sorted(({**a, "nom_utilisateur": noms.get(a["utilisateur_id"])}
                   for a in donnees["avis"] if a["film_id"] == film_id),
                  key=lambda a: a["date"], reverse=True)
    supports = [s for s in donnees["supports"] if s["film_id"] == film_id]
    return jsonify({**avec_note(film, statistiques_avis(donnees)),
                    "supports": supports, "avis": avis}), 200


def champs_film(corps, donnees, partiel=False):
    """Valide les champs modifiables d'un film (tous si partiel est faux)."""
    champs = {}
    if not partiel or "titre" in corps:
        champs["titre"] = chaine(corps, "titre")
    if not partiel or "annee" in corps:
        champs["annee"] = entier(corps.get("annee"), "annee", 1888, aujourdhui().year + 5)
    if not partiel or "categories" in corps:
        categories = corps.get("categories") or []
        if not isinstance(categories, list) or any(
                isinstance(c, bool) or not isinstance(c, int) for c in categories):
            erreur(400, "Champ « categories » : liste d'identifiants attendue")
        existantes = {c["id"] for c in donnees["categories"]}
        inconnues = [c for c in categories if c not in existantes]
        if inconnues:
            erreur(400, f"Catégorie(s) inexistante(s) : {inconnues}")
        champs["categories"] = list(dict.fromkeys(categories))  # sans doublon
    if not partiel or "acteurs" in corps:
        acteurs = corps.get("acteurs") or []
        if not isinstance(acteurs, list) or any(not isinstance(a, str) for a in acteurs):
            erreur(400, "Champ « acteurs » : liste de chaînes attendue")
        champs["acteurs"] = [a.strip() for a in acteurs if a.strip()]
    if not partiel or "description" in corps:
        champs["description"] = chaine(corps, "description", obligatoire=False,
                                       longueur_max=5000) or ""
    if not partiel or "affiche" in corps:
        affiche = chaine(corps, "affiche", obligatoire=False, longueur_max=500)
        if affiche and not affiche.startswith(("http://", "https://")):
            erreur(400, "Champ « affiche » : URL http(s) attendue")
        champs["affiche"] = affiche
    return champs


@app.post("/api/films")
def ajouter_film():
    """(admin) Ajout manuel d'un film (sans TMDB)."""
    corps = corps_json()
    with stockage.transaction() as donnees:
        champs = champs_film(corps, donnees)
        film = {
            "id": stockage.prochain_id(donnees["films"]),
            "titre": champs["titre"],
            "categories": champs["categories"],
            "annee": champs["annee"],
            "acteurs": champs["acteurs"],
            "description": champs["description"],
            "affiche": champs["affiche"],
            "tmdb_id": None,
            "date_ajout": aujourdhui().isoformat(),
        }
        donnees["films"].append(film)
    return jsonify(film), 201


@app.put("/api/films/<int:film_id>")
def modifier_film(film_id):
    """(admin) Modifie les champs envoyés (titre, annee, categories, acteurs,
    description, affiche)."""
    corps = corps_json()
    with stockage.transaction() as donnees:
        film = trouver(donnees["films"], film_id)
        if film is None:
            erreur(404, "Film inexistant")
        champs = champs_film(corps, donnees, partiel=True)
        if not champs:
            erreur(400, "Aucun champ à modifier")
        film.update(champs)
    return jsonify(film), 200


@app.delete("/api/films/<int:film_id>")
def supprimer_film(film_id):
    """(admin) Supprime le film, ses supports, ses avis et ses emprunts terminés ;
    refusé (409) si l'un de ses supports est emprunté."""
    with stockage.transaction() as donnees:
        film = trouver(donnees["films"], film_id)
        if film is None:
            erreur(404, "Film inexistant")
        supports = {s["id"] for s in donnees["supports"] if s["film_id"] == film_id}
        if any(support_emprunte(donnees, support_id) for support_id in supports):
            erreur(409, "Un support de ce film est emprunté : suppression impossible")
        donnees["films"].remove(film)
        donnees["supports"] = [s for s in donnees["supports"] if s["film_id"] != film_id]
        donnees["avis"] = [a for a in donnees["avis"] if a["film_id"] != film_id]
        donnees["emprunts"] = [e for e in donnees["emprunts"] if e["support_id"] not in supports]
        for utilisateur in donnees["utilisateurs"]:
            for champ in ("liste_envies", "films_vus"):
                utilisateur[champ] = [f for f in utilisateur[champ] if f != film_id]
    return jsonify(message=f"Film « {film['titre']} » supprimé"), 200


# =============================================================================
# Catégories (genres)
# =============================================================================
@app.get("/api/categories")
def lister_categories():
    categories = stockage.lire()["categories"]
    return jsonify(sorted(categories, key=lambda c: normaliser(c["nom"]))), 200


def nom_categorie(corps, donnees, sauf_id=None):
    nom = chaine(corps, "nom", longueur_max=50)
    if any(normaliser(c["nom"]) == normaliser(nom) and c["id"] != sauf_id
           for c in donnees["categories"]):
        erreur(409, f"La catégorie « {nom} » existe déjà")
    return nom


@app.post("/api/categories")
def ajouter_categorie():
    """(admin) Crée une catégorie : {"nom": "..."}."""
    corps = corps_json()
    with stockage.transaction() as donnees:
        categorie = {"id": stockage.prochain_id(donnees["categories"]),
                     "nom": nom_categorie(corps, donnees)}
        donnees["categories"].append(categorie)
    return jsonify(categorie), 201


@app.put("/api/categories/<int:categorie_id>")
def renommer_categorie(categorie_id):
    """(admin) Renomme une catégorie."""
    corps = corps_json()
    with stockage.transaction() as donnees:
        categorie = trouver(donnees["categories"], categorie_id)
        if categorie is None:
            erreur(404, "Catégorie inexistante")
        categorie["nom"] = nom_categorie(corps, donnees, sauf_id=categorie_id)
    return jsonify(categorie), 200


@app.delete("/api/categories/<int:categorie_id>")
def supprimer_categorie(categorie_id):
    """(admin) Supprime une catégorie et la retire des films."""
    with stockage.transaction() as donnees:
        categorie = trouver(donnees["categories"], categorie_id)
        if categorie is None:
            erreur(404, "Catégorie inexistante")
        donnees["categories"].remove(categorie)
        for film in donnees["films"]:
            film["categories"] = [c for c in film["categories"] if c != categorie_id]
    return jsonify(message=f"Catégorie « {categorie['nom']} » supprimée"), 200


# =============================================================================
# Supports (exemplaires physiques ou versions numériques)
# =============================================================================
def type_support(valeur):
    if isinstance(valeur, str):
        if normaliser(valeur) == "physique":
            return "physique"
        if normaliser(valeur) == "numerique":
            return "numérique"
    erreur(400, "Champ « type » : « physique » ou « numérique » attendu")


@app.post("/api/films/<int:film_id>/supports")
def ajouter_support(film_id):
    """(admin) Ajoute un support au film : {type, format, lien}."""
    corps = corps_json()
    type_ = type_support(corps.get("type"))
    format_ = chaine(corps, "format", longueur_max=30)
    lien = chaine(corps, "lien", longueur_max=500) if type_ == "numérique" else None
    with stockage.transaction() as donnees:
        if trouver(donnees["films"], film_id) is None:
            erreur(404, "Film inexistant")
        support = {
            "id": stockage.prochain_id(donnees["supports"]),
            "film_id": film_id,
            "type": type_,
            "format": format_,
            "disponible": True,
            "lien": lien,
        }
        donnees["supports"].append(support)
    return jsonify(support), 201


@app.put("/api/supports/<int:support_id>")
def modifier_support(support_id):
    """(admin) Modifie type, format, lien ou disponibilité d'un support.

    Un support emprunté ne peut pas changer de type ni être rendu disponible
    (c'est le retour de l'emprunt qui le fait) ; un support numérique reste
    toujours disponible.
    """
    corps = corps_json()
    if not {"type", "format", "lien", "disponible"} & corps.keys():
        erreur(400, "Aucun champ à modifier")
    with stockage.transaction() as donnees:
        support = trouver(donnees["supports"], support_id)
        if support is None:
            erreur(404, "Support inexistant")
        emprunte = support_emprunte(donnees, support_id)
        nouveau_type = type_support(corps["type"]) if "type" in corps else support["type"]
        if nouveau_type != support["type"] and emprunte:
            erreur(409, "Support emprunté : son type ne peut pas changer")
        if "format" in corps:
            support["format"] = chaine(corps, "format", longueur_max=30)

        if nouveau_type == "numérique":
            lien = chaine(corps, "lien", longueur_max=500) if "lien" in corps else support["lien"]
            if not lien:
                erreur(400, "Champ « lien » obligatoire pour un support numérique")
            if corps.get("disponible") is False:
                erreur(400, "Un support numérique reste toujours disponible")
            support.update(type=nouveau_type, lien=lien, disponible=True)
        else:
            if support["type"] != "physique":  # numérique → physique
                support["disponible"] = True
            support.update(type=nouveau_type, lien=None)
            if "disponible" in corps:
                disponible = corps["disponible"]
                if not isinstance(disponible, bool):
                    erreur(400, "Champ « disponible » : true ou false attendu")
                if disponible and emprunte:
                    erreur(409, "Support emprunté : il redeviendra disponible à son retour")
                support["disponible"] = disponible and not emprunte
    return jsonify(support), 200


@app.delete("/api/supports/<int:support_id>")
def supprimer_support(support_id):
    """(admin) Supprime un support et ses emprunts terminés ; refusé s'il est emprunté."""
    with stockage.transaction() as donnees:
        support = trouver(donnees["supports"], support_id)
        if support is None:
            erreur(404, "Support inexistant")
        if support_emprunte(donnees, support_id):
            erreur(409, "Support emprunté : suppression impossible")
        donnees["supports"].remove(support)
        donnees["emprunts"] = [e for e in donnees["emprunts"] if e["support_id"] != support_id]
    return jsonify(message="Support supprimé"), 200


# =============================================================================
# Emprunts
# =============================================================================
@app.get("/api/emprunts")
def lister_emprunts():
    """Emprunts d'un utilisateur (?utilisateur_id=…), ou tous les emprunts
    sans paramètre (admin). En cours d'abord, puis les plus récents."""
    utilisateur_id = parametre_entier("utilisateur_id")
    donnees = stockage.lire()
    emprunts = donnees["emprunts"]
    if utilisateur_id is not None:
        if trouver(donnees["utilisateurs"], utilisateur_id) is None:
            erreur(404, "Utilisateur inexistant")
        emprunts = [e for e in emprunts if e["utilisateur_id"] == utilisateur_id]
    emprunts = sorted(emprunts, key=lambda e: e["date_emprunt"], reverse=True)
    emprunts.sort(key=lambda e: e["date_retour_effective"] is not None)
    return jsonify([emprunt_detaille(e, donnees) for e in emprunts]), 200


@app.post("/api/emprunts")
def emprunter():
    """Emprunt d'un support physique disponible : {utilisateur_id, support_id}."""
    corps = corps_json()
    utilisateur_id = entier(corps.get("utilisateur_id"), "utilisateur_id", 1)
    support_id = entier(corps.get("support_id"), "support_id", 1)
    with stockage.transaction() as donnees:
        verifier_utilisateur(donnees, utilisateur_id)
        support = trouver(donnees["supports"], support_id)
        if support is None:
            erreur(404, "Support inexistant")
        if support["type"] != "physique":
            erreur(409, "Seul un support physique peut être emprunté")
        if not support["disponible"] or support_emprunte(donnees, support_id):
            erreur(409, "Ce support n'est pas disponible")
        debut = aujourdhui()
        emprunt = {
            "id": stockage.prochain_id(donnees["emprunts"]),
            "utilisateur_id": utilisateur_id,
            "support_id": support_id,
            "date_emprunt": debut.isoformat(),
            "date_retour_prevue": (debut + timedelta(days=DUREE_EMPRUNT_JOURS)).isoformat(),
            "date_retour_effective": None,
        }
        donnees["emprunts"].append(emprunt)
        support["disponible"] = False
        reponse = emprunt_detaille(emprunt, donnees)
    return jsonify(reponse), 201


@app.put("/api/emprunts/<int:emprunt_id>/retour")
def rendre(emprunt_id):
    """Retour d'un emprunt : {utilisateur_id} (doit être l'emprunteur)."""
    corps = corps_json()
    utilisateur_id = entier(corps.get("utilisateur_id"), "utilisateur_id", 1)
    with stockage.transaction() as donnees:
        verifier_utilisateur(donnees, utilisateur_id)
        emprunt = trouver(donnees["emprunts"], emprunt_id)
        if emprunt is None or emprunt["utilisateur_id"] != utilisateur_id:
            erreur(404, "Emprunt inexistant pour cet utilisateur")
        if emprunt["date_retour_effective"] is not None:
            erreur(409, "Cet emprunt est déjà clos")
        emprunt["date_retour_effective"] = aujourdhui().isoformat()
        support = trouver(donnees["supports"], emprunt["support_id"])
        if support is not None:
            support["disponible"] = True
        reponse = emprunt_detaille(emprunt, donnees)
    return jsonify(reponse), 200


# =============================================================================
# Avis
# =============================================================================
@app.post("/api/films/<int:film_id>/avis")
def donner_avis(film_id):
    """Note (1 à 5) et commentaire facultatif : crée l'avis, ou remplace celui
    que l'utilisateur a déjà donné sur ce film (un seul avis par film)."""
    corps = corps_json()
    utilisateur_id = entier(corps.get("utilisateur_id"), "utilisateur_id", 1)
    note = entier(corps.get("note"), "note", 1, 5)
    commentaire = chaine(corps, "commentaire", obligatoire=False, longueur_max=2000)
    with stockage.transaction() as donnees:
        verifier_utilisateur(donnees, utilisateur_id)
        if trouver(donnees["films"], film_id) is None:
            erreur(404, "Film inexistant")
        avis = next((a for a in donnees["avis"]
                     if a["utilisateur_id"] == utilisateur_id and a["film_id"] == film_id), None)
        if avis is None:
            avis = {"id": stockage.prochain_id(donnees["avis"]),
                    "utilisateur_id": utilisateur_id, "film_id": film_id}
            donnees["avis"].append(avis)
        avis.update(note=note, commentaire=commentaire, date=aujourdhui().isoformat())
    return jsonify(avis), 201


# =============================================================================
# Import TMDB
# =============================================================================
@app.get("/api/tmdb")
def rechercher_tmdb():
    """(admin) Recherche par titre sur TMDB. Chaque résultat indique film_id si
    le film est déjà dans la vidéothèque."""
    titre = request.args.get("titre", "").strip()
    if not titre:
        erreur(400, "Paramètre « titre » obligatoire")
    try:
        resultats = tmdb.rechercher(titre)
    except (tmdb.TmdbIndisponible, tmdb.TmdbIntrouvable) as exc:
        erreur(502, f"TMDB indisponible : {exc}")
    importes = {f["tmdb_id"]: f["id"] for f in stockage.lire()["films"] if f.get("tmdb_id")}
    for resultat in resultats:
        resultat["film_id"] = importes.get(resultat["tmdb_id"])
    return jsonify(resultats), 200


@app.post("/api/tmdb/import")
def importer_tmdb():
    """(admin) Importe un film depuis TMDB : {tmdb_id}. Crée les catégories
    manquantes puis le film ; 409 si le film est déjà importé."""
    tmdb_id = entier(corps_json().get("tmdb_id"), "tmdb_id", 1)

    def deja_importe(donnees):
        if any(f.get("tmdb_id") == tmdb_id for f in donnees["films"]):
            erreur(409, "Ce film a déjà été importé")

    deja_importe(stockage.lire())
    try:
        meta = tmdb.details(tmdb_id)  # appel externe hors verrou
    except tmdb.TmdbIntrouvable:
        erreur(404, "Film introuvable sur TMDB")
    except tmdb.TmdbIndisponible as exc:
        erreur(502, f"TMDB indisponible : {exc}")

    with stockage.transaction() as donnees:
        deja_importe(donnees)  # revérifié sous verrou (import simultané)
        categories = []
        for nom in meta["genres"]:
            categorie = next((c for c in donnees["categories"]
                              if normaliser(c["nom"]) == normaliser(nom)), None)
            if categorie is None:
                categorie = {"id": stockage.prochain_id(donnees["categories"]), "nom": nom}
                donnees["categories"].append(categorie)
            categories.append(categorie["id"])
        film = {
            "id": stockage.prochain_id(donnees["films"]),
            "titre": meta["titre"],
            "categories": categories,
            "annee": meta["annee"],
            "acteurs": meta["acteurs"],
            "description": meta["description"],
            "affiche": meta["affiche"],
            "tmdb_id": tmdb_id,
            "date_ajout": aujourdhui().isoformat(),
        }
        donnees["films"].append(film)
    return jsonify(film), 201
