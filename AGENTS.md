# AGENTS.md — videotek-api

Consignes pour toute personne ou tout agent qui fait évoluer ce repo. Lire
aussi le [README](README.md) (routes, modèle de données, diagrammes).

## Rôle du repo

Tiers **données** de l'application web 3 tiers du TP RT0705 « Gestion d'une
vidéothèque » :

```text
navigateur ──HTTP:5000──▶ videotek-serveur (Flask + Jinja) ──HTTP/JSON──▶ videotek-api (ce repo) ──▶ videotheque.json
                                                                                  └──HTTPS──▶ TMDB
```

- Seule l'API lit et écrit le fichier JSON ; le serveur web passe toujours par elle.
- L'API ne gère pas de session. Le serveur web transmet l'utilisateur connecté
  dans l'en-tête `X-Utilisateur-Id` (et `utilisateur_id` dans le corps ou le
  chemin selon la route) ; l'API vérifie qu'il existe (401) et n'est pas bloqué (403).
- Les routes *(admin)* ne vérifient pas le rôle : c'est le serveur web qui le
  fait avant de les appeler (choix de la conception). Le port 5001 publié sur la
  VM ne sert qu'aux tests.

La conception du TP (fonctionnalités, données, routes, codes de retour) est la
référence : ne pas changer un nom de route, un champ JSON ou un code de retour
sans le reporter dans **les deux repos** (ici + `videotek-serveur/api.py`) et
dans les deux README.

## Arborescence

| Fichier | Rôle |
|---|---|
| `app.py` | Routes Flask, validation des champs, règles métier, gestion des erreurs |
| `stockage.py` | Seul accès au fichier JSON : `lire()`, `transaction()`, `prochain_id()`, `initialiser()` |
| `comptes.py` | Règles des comptes et bcrypt, partagées avec `creer_admin.py` |
| `tmdb.py` | Client TMDB (`rechercher`, `details`), exceptions `TmdbIndisponible` / `TmdbIntrouvable` |
| `creer_admin.py` | Script CLI : créer / réinitialiser un administrateur |
| `donnees/videotheque_test.json` | Données de préproduction (versionnées) |
| `tests/test_api.py`, `tests/conftest.py` | Tests pytest |
| `tests/tests_curl.sh` | Scénario curl sur l'API lancée |
| `Dockerfile` | Image commune `videotek-flask:1.0` — **identique** à celle de videotek-serveur |
| `docker-compose.yml` | API seule, port 5001 (le compose global est dans videotek-serveur) |

## Commandes

```bash
cp .env.example .env                              # VIDEOTHEQUE_FICHIER, TMDB_CLE
docker compose up -d --build                      # API seule sur http://localhost:5001/api
docker compose exec api python -m pytest -v       # tests unitaires
./tests/tests_curl.sh                             # scénario curl (API lancée)
docker compose exec api python creer_admin.py <nom_utilisateur>
docker compose logs -f api
git checkout -- donnees/videotheque_test.json     # réinitialiser la préproduction
```

Sans Docker : `pip install -r requirements.txt` puis
`VIDEOTHEQUE_FICHIER=/tmp/v.json flask --app app run --port 5001`.

## Conventions

- **Français partout** : noms de variables, fonctions, routes, champs JSON,
  messages d'erreur, commentaires, messages de commit.
- Champs JSON en `snake_case` sans accents (`date_retour_prevue`), dans l'ordre
  de la conception (`app.json.sort_keys = False`). Valeur facultative absente : `null`.
- Dates : chaînes `AAAA-MM-JJ`, calculées en heure de Paris (`aujourdhui()`).
- Identifiant d'un nouvel objet : `stockage.prochain_id(liste)` (max + 1).
- Erreurs : lever `erreur(code, "message")` ; la réponse est toujours
  `{"erreur": "message"}`. Codes : 400 invalide, 401 identifiants, **403 réservé au
  compte bloqué** (le serveur web vide alors la session), 404 inexistant,
  409 conflit, 502 TMDB. Ne jamais utiliser 403 pour un autre cas.
- Valeur de `type` d'un support : `"physique"` ou `"numérique"` (avec accent ;
  `"numerique"` est accepté en entrée).

## Règles du stockage (à respecter)

1. Toute écriture passe par `with stockage.transaction() as donnees:` ; aucune
   autre fonction n'ouvre le fichier en écriture.
2. Valider **dans** le bloc et lever `erreur(...)` en cas de problème : la
   transaction est abandonnée, rien n'est écrit.
3. Les appels réseau (TMDB) et le hachage bcrypt se font **hors** du bloc, pour
   ne pas bloquer les autres écritures ; revérifier les conflits sous verrou.
4. Les lectures utilisent `stockage.lire()` (pas de verrou nécessaire).
5. Le chemin vient de `VIDEOTHEQUE_FICHIER` : ne jamais coder un chemin en dur.

## Règles métier

- Inscription : rôle `utilisateur`, non bloqué ; nom d'utilisateur et e-mail
  uniques (sans tenir compte de la casse) ; mot de passe ≥ 8 caractères et ≤ 72
  octets (limite bcrypt). Le haché n'est **jamais** renvoyé par l'API.
- Aucune route ne donne le rôle administrateur : seulement `creer_admin.py`.
- Emprunt : support **physique** et **disponible**, retour prévu à +14 jours
  (`DUREE_EMPRUNT_JOURS`), le support passe indisponible ; retour par
  l'emprunteur uniquement, une seule fois.
- Un seul avis par utilisateur et par film (le second remplace le premier) ;
  note entière de 1 à 5 ; note moyenne arrondie à 0,1.
- Suppression d'un film refusée (409) si l'un de ses supports est emprunté ;
  sinon cascade : supports, avis, emprunts terminés, listes des utilisateurs.
- Suppression d'un support refusée s'il est emprunté ; un support emprunté ne
  change pas de type et ne peut pas être rendu disponible à la main.
- Catégories uniques sans tenir compte de la casse ni des accents ; une
  catégorie supprimée est retirée des films ; l'import TMDB réutilise une
  catégorie existante de même nom ou la crée.
- Recherche `q` : sous-chaîne du titre ou d'un acteur, sans accents ni casse.
  Tri `titre` (défaut), `note` (meilleures d'abord, sans avis à la fin) ou
  `date_ajout` (récents d'abord).

## Tests

- Toute nouvelle route ou règle s'accompagne de tests dans `tests/test_api.py`.
- Les tests travaillent sur une **copie temporaire** du fichier de test
  (fixture `fichier`) : ne jamais modifier `donnees/videotheque_test.json`
  depuis un test.
- Ne jamais appeler le vrai TMDB dans les tests : simuler `tmdb.requests.get`
  (voir `faux_tmdb`).
- Si une modification change les données de test, mettre à jour les attentes
  des tests et le tableau du README.
- `tests/tests_curl.sh` doit rester vert sur la préproduction.

## Ce qu'il ne faut pas faire

- Committer `.env`, une vraie clé TMDB ou `donnees/videotheque.json` (données réelles).
- Ajouter une dépendance sans la mettre dans le `Dockerfile` **des deux repos**
  (image commune) et dans `requirements.txt`.
- Copier le code dans l'image : il est monté en volume (`.:/app`).
- Activer le mode debug de Flask (débogueur interactif exposé sur le réseau du lab).

## Git

- Branche `main`, commits courts et thématiques, message en français à
  l'impératif (« Ajoute la route de retour d'un emprunt »).
- Pas de ligne `Co-Authored-By` ni de mention d'outil ou d'assistant dans les
  commits : l'auteur est le titulaire du repo.

## Vérifications avant de terminer

- [ ] `python -m pytest` vert (dans le conteneur ou en local)
- [ ] `./tests/tests_curl.sh` vert sur l'API lancée en préproduction
- [ ] README à jour (tableau des routes, diagrammes Mermaid) si le contrat change
- [ ] `videotek-serveur/api.py` et ses tests adaptés si une route change
- [ ] `git status` propre : ni `.env`, ni fichier `.lock`, ni données réelles
