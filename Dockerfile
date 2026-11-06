# Image commune aux conteneurs « api » et « serveur » de la vidéothèque.
# Ce fichier est identique dans les repos videotek-api et videotek-serveur :
# les deux construisent la même image videotek-flask:1.0.
#
# Le code n'est pas copié dans l'image : il est monté dans /app par un volume
# partagé avec l'hôte Docker (voir docker-compose.yml). Le contexte de build
# est donc vide (.dockerignore).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris

# Python 3 + Flask (+ requests, bcrypt), tzdata pour le fuseau horaire,
# pytest pour lancer les tests dans le conteneur
RUN pip install --no-cache-dir \
        flask==3.1.3 \
        requests==2.34.2 \
        bcrypt==5.0.0 \
        tzdata==2026.6 \
        pytest==9.2.0

# Utilisateur non root, avec le même UID que l'utilisateur de la VM :
# les fichiers écrits dans les volumes lui appartiennent.
ARG UID=1000
ARG GID=1000
RUN groupadd --gid "${GID}" flask \
    && useradd --uid "${UID}" --gid "${GID}" --create-home flask

# Architecture de répertoires d'une application Flask. /app est remplacé par
# le volume du projet ; /donnees accueille le fichier JSON de production.
RUN mkdir -p /app/templates /app/static /donnees \
    && chown -R flask:flask /app /donnees
WORKDIR /app
USER flask

EXPOSE 5000
# --reload : le serveur redémarre quand un fichier du volume change
CMD ["flask", "--app", "app", "run", "--host=0.0.0.0", "--port=5000", "--reload"]
