"""Crée un compte administrateur, ou réinitialise un compte existant.

L'inscription ne crée que des comptes « utilisateur » et aucune route de
l'API ne donne le rôle « administrateur » : ce script, lancé en ligne de
commande sur la VM dans le conteneur « api », est le seul moyen de le faire.

    docker compose exec api python creer_admin.py <nom_utilisateur>

- compte inexistant : demande prénom, nom, e-mail, année de naissance et
  mot de passe, puis crée le compte avec le rôle « administrateur » ;
- compte existant : remplace son mot de passe, lui donne le rôle
  « administrateur » et le débloque (mot de passe perdu, nouvel admin).

Le mot de passe est saisi deux fois (saisie masquée) puis haché avec bcrypt.
Le script utilise les mêmes fonctions de lecture et d'écriture que l'API.
"""
import getpass
import sys
from datetime import date

import comptes
import stockage


def demander(question, verification):
    """Pose la question jusqu'à obtenir une réponse valide."""
    while True:
        reponse = input(question).strip()
        message = verification(reponse)
        if message is None:
            return reponse
        print(f"  ✗ {message}")


def obligatoire(valeur):
    return None if valeur else "valeur obligatoire"


def annee_valide(valeur):
    if not valeur.isdigit():
        return "nombre attendu (ex. : 2003)"
    return comptes.erreur_annee_naissance(int(valeur), date.today().year)


def demander_mot_de_passe():
    while True:
        mot_de_passe = getpass.getpass("Mot de passe : ")
        message = comptes.erreur_mot_de_passe(mot_de_passe)
        if message:
            print(f"  ✗ {message}")
            continue
        if getpass.getpass("Confirmez le mot de passe : ") != mot_de_passe:
            print("  ✗ Les deux saisies sont différentes, recommencez.")
            continue
        return mot_de_passe


def chercher(donnees, nom_utilisateur):
    return next((u for u in donnees["utilisateurs"]
                 if u["nom_utilisateur"].casefold() == nom_utilisateur.casefold()), None)


def main(arguments):
    if len(arguments) != 2:
        print(__doc__)
        return 2
    nom_utilisateur = arguments[1].strip()
    message = comptes.erreur_nom_utilisateur(nom_utilisateur)
    if message:
        print(f"✗ {message}")
        return 1

    stockage.initialiser()  # crée le fichier s'il n'existe pas encore
    print(f"Fichier : {stockage.chemin_fichier()}")
    existant = chercher(stockage.lire(), nom_utilisateur)
    if existant:
        print(f"Le compte « {existant['nom_utilisateur']} » existe : son mot de passe va être "
              "remplacé, il devient administrateur et il est débloqué.")
        nouveau = None
    else:
        print(f"Création du compte administrateur « {nom_utilisateur} ».")
        nouveau = {
            "prenom": demander("Prénom : ", obligatoire),
            "nom": demander("Nom : ", obligatoire),
            "email": demander("E-mail : ", comptes.erreur_email),
            "annee_naissance": int(demander("Année de naissance : ", annee_valide)),
        }
    hache = comptes.hacher(demander_mot_de_passe())

    with stockage.transaction() as donnees:
        utilisateur = chercher(donnees, nom_utilisateur)
        if utilisateur is not None:
            if nouveau is not None:  # compte créé entre-temps depuis le site
                raise SystemExit("✗ Ce compte vient d'être créé : relancez le script.")
            utilisateur.update(mot_de_passe=hache, role=comptes.ROLE_ADMIN, bloque=False)
            action = "mis à jour"
        else:
            if any(u["email"].casefold() == nouveau["email"].casefold()
                   for u in donnees["utilisateurs"]):
                raise SystemExit("✗ Cette adresse e-mail est déjà utilisée par un autre compte.")
            donnees["utilisateurs"].append({
                "id": stockage.prochain_id(donnees["utilisateurs"]),
                "nom_utilisateur": nom_utilisateur,
                **nouveau,
                "mot_de_passe": hache,
                "role": comptes.ROLE_ADMIN,
                "bloque": False,
                "liste_envies": [],
                "films_vus": [],
            })
            action = "créé"
    print(f"✓ Administrateur « {nom_utilisateur} » {action}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (KeyboardInterrupt, EOFError):
        print("\nAbandon : aucune modification.")
        sys.exit(1)
