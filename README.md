
# Dis-moi tout

Application de gestion familiale permettant de partager un calendrier, organiser les événements et gérer les membres d'une famille.

## Technologies

- Python
- Flask
- SQLite
- Google Calendar
- Apple Calendar

## Installation

Installer les dépendances :

```bash
pip install -r requirements.txt
```

Lancer l'application :

```bash
python app.py
```

## Configuration

Les paramètres sensibles doivent être définis dans les variables d'environnement.

La base de données est stockée dans le dossier `database`.

En production, les données persistantes doivent être stockées sur le Render Disk.

## Hébergement

Application prévue pour être hébergée sur Render avec un disque persistant (Render Disk).

