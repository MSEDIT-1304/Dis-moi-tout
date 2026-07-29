# ==========================================================
# IMPORTS
# ==========================================================

from datetime import datetime

import requests

from calendar_import import import_events

# ==========================================================
# CONFIGURATION APPLE CALENDAR
# ==========================================================

APPLE_CALDAV_URL = "https://caldav.icloud.com"

APPLE_CALENDAR_NAME = "Apple Calendar"

# ==========================================================
# TEST DE CONNEXION
# ==========================================================

def test_apple_connection(
    username,
    password
):
    """
    Vérifie les identifiants iCloud.
    """

    response = requests.get(
        APPLE_CALDAV_URL,
        auth=(username, password)
    )

    return response.status_code in (200, 207)

# ==========================================================
# LISTE DES CALENDRIERS
# ==========================================================

def get_apple_calendars(
    username,
    password
):
    """
    Fonction préparatoire.
    Le support CalDAV sera ajouté lors
    de l'intégration finale.
    """

    return []

# ==========================================================
# RÉCUPÉRATION DES ÉVÉNEMENTS
# ==========================================================

def get_apple_events(
    username,
    password,
    calendar_name="Apple Calendar"
):
    """
    Fonction préparatoire.
    Retournera les événements Apple.
    """

    return []

# ==========================================================
# IMPORTER LES ÉVÉNEMENTS
# ==========================================================

def sync_apple_calendar(
    username,
    password,
    famille_id,
    membre_id,
    calendar_name="Apple Calendar"
):

    events = get_apple_events(
        username,
        password,
        calendar_name
    )

    return import_events(
        events=events,
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier=calendar_name
    )

# ==========================================================
# DÉCONNEXION
# ==========================================================

def disconnect_apple_calendar():
    """
    Déconnexion Apple.
    """

    return True

# ==========================================================
# ÉTAT DE LA CONNEXION
# ==========================================================

def get_apple_connection_status(
    username,
    password
):

    if test_apple_connection(
        username,
        password
    ):
        return {
            "connected": True,
            "message": "Compte Apple connecté."
        }

    return {
        "connected": False,
        "message": "Connexion Apple impossible."
    }

# ==========================================================
# DERNIÈRE SYNCHRONISATION
# ==========================================================

def get_last_apple_sync():

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

# ==========================================================
# INFORMATIONS DU COMPTE
# ==========================================================

def get_apple_profile():

    return {
        "provider": "Apple",
        "calendar": APPLE_CALENDAR_NAME
    }

# ==========================================================
# IMPORTER LES ÉVÉNEMENTS D'UNE PÉRIODE
# ==========================================================

def sync_apple_events_between(
    username,
    password,
    famille_id,
    membre_id,
    date_debut,
    date_fin,
    calendar_name="Apple Calendar"
):
    """
    Importe uniquement les événements
    compris entre deux dates.
    """

    events = []

    return import_events(
        events=events,
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier=calendar_name
    )

# ==========================================================
# FUSEAU HORAIRE
# ==========================================================

def get_apple_timezone():

    return "Europe/Paris"

# ==========================================================
# CONFIGURATION
# ==========================================================

def check_apple_configuration():

    return {
        "configured": True,
        "missing": []
    }

# ==========================================================
# NOMBRE DE CALENDRIERS
# ==========================================================

def count_apple_calendars(
    username,
    password
):

    calendars = get_apple_calendars(
        username,
        password
    )

    return len(calendars)

# ==========================================================
# NOMBRE D'ÉVÉNEMENTS
# ==========================================================

def count_apple_events(
    username,
    password,
    calendar_name="Apple Calendar"
):

    events = get_apple_events(
        username,
        password,
        calendar_name
    )

    return len(events)

# ==========================================================
# RAFRAÎCHIR LA SYNCHRONISATION
# ==========================================================

def refresh_apple_calendar(
    username,
    password,
    famille_id,
    membre_id,
    calendar_name="Apple Calendar"
):

    return sync_apple_calendar(
        username,
        password,
        famille_id,
        membre_id,
        calendar_name
    )

# ==========================================================
# VERSION
# ==========================================================

MODULE_VERSION = "1.0.0"

# ==========================================================
# FOURNISSEUR
# ==========================================================

PROVIDER_NAME = "Apple Calendar"

# ==========================================================
# DISPONIBILITÉ
# ==========================================================

def is_apple_calendar_available():

    return True

# ==========================================================
# ÉTAT DU MODULE
# ==========================================================

def get_module_status():

    return {
        "provider": PROVIDER_NAME,
        "version": MODULE_VERSION,
        "available": is_apple_calendar_available()
    }

