#!/usr/bin/env bash
# Tests de l'API REST avec curl, depuis la VM (API publiée sur le port 5001).
#
#   ./tests/tests_curl.sh
#   API_URL=http://10.11.16.145:5001/api ./tests/tests_curl.sh
#
# Le scénario crée ses propres objets (utilisateur, catégorie, film, supports),
# vérifie le code de retour de chaque appel, puis supprime ce qui peut l'être
# (l'API n'a pas de route de suppression d'utilisateur : le compte de test reste).
set -u
API_URL="${API_URL:-http://localhost:5001/api}"
REUSSIS=0
ECHECS=0
CORPS=$(mktemp)
trap 'rm -f "$CORPS"' EXIT

# appel MÉTHODE CHEMIN CODES_ATTENDUS [JSON]   (CODES_ATTENDUS : "200" ou "200|502")
appel() {
  local methode=$1 chemin=$2 attendus=$3 json=${4:-} code
  local options=(-s -o "$CORPS" -w '%{http_code}' -X "$methode" "$API_URL$chemin")
  if [ -n "$json" ]; then
    options+=(-H 'Content-Type: application/json' -d "$json")
  fi
  code=$(curl "${options[@]}")
  if [[ "|$attendus|" == *"|$code|"* ]]; then
    REUSSIS=$((REUSSIS + 1))
    printf '  \033[32m✓\033[0m %-6s %-48s %s\n' "$methode" "${chemin:-/}" "$code"
  else
    ECHECS=$((ECHECS + 1))
    printf '  \033[31m✗\033[0m %-6s %-48s %s (attendu %s) %s\n' \
      "$methode" "${chemin:-/}" "$code" "$attendus" "$(head -c 200 "$CORPS")"
  fi
  CODE=$code
}

# valeur CHAMP : lit un champ de la dernière réponse JSON
valeur() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$CORPS" "$1"
}

N=$(date +%s)
UTILISATEUR="{\"nom_utilisateur\":\"curl$N\",\"prenom\":\"Test\",\"nom\":\"Curl\",\"email\":\"curl$N@exemple.fr\",\"annee_naissance\":2000,\"mot_de_passe\":\"curl-2026\"}"

echo "API : $API_URL"
appel GET "" 200

echo "== Inscription, connexion, utilisateurs"
appel POST /utilisateurs 201 "$UTILISATEUR"
ID_U=$(valeur id)
appel POST /utilisateurs 409 "$UTILISATEUR"
appel POST /utilisateurs 400 '{"nom_utilisateur":"x"}'
appel POST /connexion 200 "{\"nom_utilisateur\":\"curl$N\",\"mot_de_passe\":\"curl-2026\"}"
appel POST /connexion 401 "{\"nom_utilisateur\":\"curl$N\",\"mot_de_passe\":\"mauvais\"}"
appel GET /utilisateurs 200
appel GET "/utilisateurs/$ID_U" 200
appel GET /utilisateurs/999999 404

echo "== Catégories"
appel POST /categories 201 "{\"nom\":\"Curl $N\"}"
ID_C=$(valeur id)
appel POST /categories 409 "{\"nom\":\"curl $N\"}"
appel PUT "/categories/$ID_C" 200 "{\"nom\":\"Curl $N (renommée)\"}"
appel GET /categories 200

echo "== Films"
appel POST /films 201 "{\"titre\":\"Film curl $N\",\"annee\":2024,\"categories\":[$ID_C],\"acteurs\":[\"Actrice Curl\"],\"description\":\"Créé par tests_curl.sh\",\"affiche\":null}"
ID_F=$(valeur id)
appel POST /films 400 '{"titre":"","annee":2024}'
appel GET "/films?q=film%20curl%20$N" 200
appel GET "/films?annee=2024&categorie=$ID_C&tri=note" 200
appel GET "/films?tri=date_ajout" 200
appel GET "/films?tri=prix" 400
appel GET "/films/$ID_F" 200
appel PUT "/films/$ID_F" 200 '{"description":"Modifié par curl"}'
appel GET /films/999999 404

echo "== Supports"
appel POST "/films/$ID_F/supports" 201 '{"type":"physique","format":"DVD"}'
ID_S=$(valeur id)
appel POST "/films/$ID_F/supports" 201 '{"type":"numérique","format":"MP4","lien":"https://media.videotek.local/curl.mp4"}'
ID_SN=$(valeur id)
appel POST "/films/$ID_F/supports" 400 '{"type":"numérique","format":"MP4"}'
appel PUT "/supports/$ID_S" 200 '{"format":"Blu-ray"}'

echo "== Liste d'envies et films vus"
appel POST "/utilisateurs/$ID_U/envies/$ID_F" 200
appel DELETE "/utilisateurs/$ID_U/envies/$ID_F" 200
appel POST "/utilisateurs/$ID_U/vus/$ID_F" 200

echo "== Emprunts"
appel POST /emprunts 201 "{\"utilisateur_id\":$ID_U,\"support_id\":$ID_S}"
ID_E=$(valeur id)
appel POST /emprunts 409 "{\"utilisateur_id\":$ID_U,\"support_id\":$ID_S}"
appel POST /emprunts 409 "{\"utilisateur_id\":$ID_U,\"support_id\":$ID_SN}"
appel DELETE "/films/$ID_F" 409
appel DELETE "/supports/$ID_S" 409
appel GET "/emprunts?utilisateur_id=$ID_U" 200
appel GET /emprunts 200
appel PUT "/emprunts/$ID_E/retour" 200 "{\"utilisateur_id\":$ID_U}"
appel PUT "/emprunts/$ID_E/retour" 409 "{\"utilisateur_id\":$ID_U}"

echo "== Avis"
appel POST "/films/$ID_F/avis" 201 "{\"utilisateur_id\":$ID_U,\"note\":4,\"commentaire\":\"Testé avec curl\"}"
appel POST "/films/$ID_F/avis" 400 "{\"utilisateur_id\":$ID_U,\"note\":9}"

echo "== Blocage"
appel PUT "/utilisateurs/$ID_U/bloque" 200 '{"bloque":true}'
appel POST /connexion 403 "{\"nom_utilisateur\":\"curl$N\",\"mot_de_passe\":\"curl-2026\"}"
appel POST /emprunts 403 "{\"utilisateur_id\":$ID_U,\"support_id\":$ID_S}"
appel PUT "/utilisateurs/$ID_U/bloque" 200 '{"bloque":false}'

echo "== TMDB (502 si la clé TMDB_CLE n'est pas configurée)"
appel GET "/tmdb?titre=la%20haine" "200|502"
if [ "$CODE" = 200 ]; then
  ID_TMDB=$(python3 -c 'import json, sys
r = [f for f in json.load(open(sys.argv[1])) if f["film_id"] is None]
print(r[0]["tmdb_id"] if r else "")' "$CORPS")
  if [ -n "$ID_TMDB" ]; then
    appel POST /tmdb/import 201 "{\"tmdb_id\":$ID_TMDB}"
    ID_FT=$(valeur id)
    appel POST /tmdb/import 409 "{\"tmdb_id\":$ID_TMDB}"
    appel DELETE "/films/$ID_FT" 200
  fi
fi

echo "== Nettoyage"
appel DELETE "/supports/$ID_SN" 200
appel DELETE "/films/$ID_F" 200
appel DELETE "/categories/$ID_C" 200

echo
echo "Bilan : $REUSSIS réussis, $ECHECS échoués"
[ "$ECHECS" -eq 0 ]
