# ==========================================================
# IMPORTS
# ==========================================================

from datetime import datetime

import json
import requests

from calendar_import import import_events

# ==========================================================
# CONFIGURATION GOOGLE CALENDAR
# ==========================================================

GOOGLE_API_URL = "https://www.googleapis.com/calendar/v3"

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly"
]

# ==========================================================
# GÉNÉRER L'URL D'AUTORISATION GOOGLE
# ==========================================================

def get_google_auth_url(
    client_id,
    redirect_uri,
    state
):
    """
    Génère l'URL permettant à l'utilisateur
    d'autoriser Dis-moi tout à accéder
    à son Google Agenda.
    """

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state
    }

    query = "&".join(
        f"{key}={value}"
        for key, value in params.items()
    )

    return f"{GOOGLE_AUTH_URL}?{query}"

# ==========================================================
# RÉCUPÉRER LE TOKEN D'ACCÈS GOOGLE
# ==========================================================

def get_google_token(
    client_id,
    client_secret,
    redirect_uri,
    authorization_code
):
    """
    Échange le code d'autorisation Google
    contre un access_token et un refresh_token.
    """

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": authorization_code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data=data
    )

    if response.status_code == 200:
        return response.json()

    return None

# ==========================================================
# RÉCUPÉRER LES ÉVÉNEMENTS GOOGLE
# ==========================================================

def get_google_events(access_token):
    """
    Récupère les événements du calendrier
    principal Google.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/calendars/primary/events",
        headers=headers
    )

    if response.status_code != 200:
        return []

    data = response.json()

    events = []

    for item in data.get("items", []):

        start = item.get("start", {})

        datetime_value = (
            start.get("dateTime")
            or start.get("date")
        )

        if not datetime_value:
            continue

        date = datetime_value[:10]

        heure = ""

        if "T" in datetime_value:
            heure = datetime_value[11:16]

        events.append({
            "titre": item.get("summary", "Sans titre"),
            "description": item.get("description", ""),
            "date": date,
            "heure": heure
        })

    return events

# ==========================================================
# IMPORTER LES ÉVÉNEMENTS GOOGLE DANS DIS-MOI TOUT
# ==========================================================

def sync_google_calendar(
    access_token,
    famille_id,
    membre_id
):
    """
    Récupère les événements Google Agenda
    puis les importe dans Dis-moi tout.
    """

    events = get_google_events(access_token)

    return import_events(
        events=events,
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier="Google Agenda"
    )

# ==========================================================
# ACTUALISER LE TOKEN GOOGLE
# ==========================================================

def refresh_google_token(
    client_id,
    client_secret,
    refresh_token
):
    """
    Récupère un nouveau token d'accès
    à partir du refresh_token.
    """

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data=data
    )

    if response.status_code == 200:
        return response.json()

    return None

# ==========================================================
# VÉRIFIER LA VALIDITÉ DU TOKEN GOOGLE
# ==========================================================

def is_google_token_valid(access_token):
    """
    Vérifie si le token Google est encore valide.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        "https://www.googleapis.com/oauth2/v1/tokeninfo",
        headers=headers,
        params={
            "access_token": access_token
        }
    )

    return response.status_code == 200

# ==========================================================
# INFORMATIONS DU CALENDRIER PRINCIPAL
# ==========================================================

def get_google_calendar_info(access_token):
    """
    Récupère les informations du calendrier principal.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/calendars/primary",
        headers=headers
    )

    if response.status_code != 200:
        return None

    data = response.json()

    return {
        "id": data.get("id"),
        "nom": data.get("summary"),
        "description": data.get("description", ""),
        "fuseau_horaire": data.get("timeZone")
    }

# ==========================================================
# RÉCUPÉRER LA LISTE DES CALENDRIERS GOOGLE
# ==========================================================

def get_google_calendars(access_token):
    """
    Retourne la liste des calendriers
    disponibles dans le compte Google.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/users/me/calendarList",
        headers=headers
    )

    if response.status_code != 200:
        return []

    data = response.json()

    calendars = []

    for item in data.get("items", []):

        calendars.append({
            "id": item.get("id"),
            "nom": item.get("summary"),
            "description": item.get("description", ""),
            "principal": item.get("primary", False),
            "couleur": item.get("backgroundColor", "#4285F4")
        })

    return calendars

# ==========================================================
# IMPORTER UN CALENDRIER GOOGLE SPÉCIFIQUE
# ==========================================================

def sync_google_calendar_by_id(
    access_token,
    calendar_id,
    famille_id,
    membre_id,
    calendar_name="Google Agenda"
):
    """
    Importe les événements d'un calendrier Google
    spécifique.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/calendars/{calendar_id}/events",
        headers=headers
    )

    if response.status_code != 200:
        return {
            "imported": 0,
            "skipped": 0
        }

    data = response.json()

    events = []

    for item in data.get("items", []):

        start = item.get("start", {})

        datetime_value = (
            start.get("dateTime")
            or start.get("date")
        )

        if not datetime_value:
            continue

        date = datetime_value[:10]

        heure = ""

        if "T" in datetime_value:
            heure = datetime_value[11:16]

        events.append({
            "titre": item.get("summary", "Sans titre"),
            "description": item.get("description", ""),
            "date": date,
            "heure": heure
        })

    return import_events(
        events=events,
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier=calendar_name
    )

# ==========================================================
# RÉVOQUER L'ACCÈS AU COMPTE GOOGLE
# ==========================================================

def revoke_google_access(access_token):
    """
    Révoque l'autorisation accordée
    à Dis-moi tout.
    """

    response = requests.post(
        "https://oauth2.googleapis.com/revoke",
        params={
            "token": access_token
        }
    )

    return response.status_code == 200

# ==========================================================
# TESTER LA CONNEXION À GOOGLE CALENDAR
# ==========================================================

def test_google_connection(access_token):
    """
    Vérifie que la connexion à Google Calendar
    est opérationnelle.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/users/me/calendarList",
        headers=headers
    )

    if response.status_code == 200:
        return {
            "success": True,
            "message": "Connexion Google Calendar réussie."
        }

    return {
        "success": False,
        "message": "Impossible de se connecter à Google Calendar."
    }

# ==========================================================
# RÉCUPÉRER LES ÉVÉNEMENTS D'UNE PÉRIODE
# ==========================================================

def get_google_events_between(
    access_token,
    calendar_id,
    time_min,
    time_max
):
    """
    Récupère les événements compris
    entre deux dates.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/calendars/{calendar_id}/events",
        headers=headers,
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime"
        }
    )

    if response.status_code != 200:
        return []

    return response.json().get("items", [])

# ==========================================================
# IMPORTER LES ÉVÉNEMENTS D'UNE PÉRIODE
# ==========================================================

def sync_google_events_between(
    access_token,
    calendar_id,
    famille_id,
    membre_id,
    time_min,
    time_max,
    calendar_name="Google Agenda"
):
    """
    Importe dans Dis-moi tout uniquement les
    événements compris entre deux dates.
    """

    items = get_google_events_between(
        access_token,
        calendar_id,
        time_min,
        time_max
    )

    events = []

    for item in items:

        start = item.get("start", {})

        datetime_value = (
            start.get("dateTime")
            or start.get("date")
        )

        if not datetime_value:
            continue

        date = datetime_value[:10]

        heure = ""

        if "T" in datetime_value:
            heure = datetime_value[11:16]

        events.append({
            "titre": item.get("summary", "Sans titre"),
            "description": item.get("description", ""),
            "date": date,
            "heure": heure
        })

    return import_events(
        events=events,
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier=calendar_name
    )

# ==========================================================
# INFORMATIONS DU COMPTE GOOGLE
# ==========================================================

def get_google_profile(access_token):
    """
    Récupère les informations du compte Google
    ayant autorisé Dis-moi tout.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers=headers
    )

    if response.status_code != 200:
        return None

    data = response.json()

    return {
        "id": data.get("id"),
        "nom": data.get("name"),
        "prenom": data.get("given_name"),
        "nom_famille": data.get("family_name"),
        "email": data.get("email"),
        "photo": data.get("picture"),
        "langue": data.get("locale")
    }

# ==========================================================
# DÉCONNECTER GOOGLE ET SUPPRIMER LA SYNCHRONISATION
# ==========================================================

from calendar_import import delete_imported_events


def disconnect_google_calendar(
    access_token,
    famille_id,
    membre_id,
    calendar_name="Google Agenda"
):
    """
    Révoque l'accès Google puis supprime
    les événements importés de cet agenda.
    """

    revoke_google_access(access_token)

    deleted = delete_imported_events(
        famille_id=famille_id,
        membre_id=membre_id,
        calendrier=calendar_name
    )

    return {
        "success": True,
        "deleted_events": deleted
    }

# ==========================================================
# VÉRIFIER LA CONFIGURATION GOOGLE
# ==========================================================

def check_google_configuration(
    client_id,
    client_secret,
    redirect_uri
):
    """
    Vérifie que la configuration Google OAuth
    est complète.
    """

    missing = []

    if not client_id:
        missing.append("CLIENT_ID")

    if not client_secret:
        missing.append("CLIENT_SECRET")

    if not redirect_uri:
        missing.append("REDIRECT_URI")

    return {
        "configured": len(missing) == 0,
        "missing": missing
    }

# ==========================================================
# RÉCUPÉRER LE FUSEAU HORAIRE GOOGLE
# ==========================================================

def get_google_timezone(access_token):
    """
    Retourne le fuseau horaire du calendrier principal.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/calendars/primary",
        headers=headers
    )

    if response.status_code != 200:
        return None

    data = response.json()

    return data.get("timeZone")

# ==========================================================
# DATE DE LA DERNIÈRE SYNCHRONISATION
# ==========================================================

def get_last_google_sync(access_token):
    """
    Retourne la date et l'heure de la
    dernière synchronisation du calendrier.
    """

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    response = requests.get(
        f"{GOOGLE_API_URL}/users/me/calendarList",
        headers=headers
    )

    if response.status_code != 200:
        return None

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ==========================================================
# ÉTAT DE LA CONNEXION GOOGLE
# ==========================================================

def get_google_connection_status(access_token):
    """
    Retourne l'état de la connexion Google.
    """

    if not access_token:
        return {
            "connected": False,
            "message": "Aucun compte Google connecté."
        }

    if is_google_token_valid(access_token):
        return {
            "connected": True,
            "message": "Compte Google connecté."
        }

    return {
        "connected": False,
        "message": "Le token Google n'est plus valide."
    }

