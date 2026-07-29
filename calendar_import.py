# ==========================================================
# IMPORTS
# ==========================================================

from datetime import datetime
import sqlite3
# ==========================================================
# CONNEXION SQLITE
# ==========================================================

DATABASE = "database/dismoitout.db"


def get_connection():

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    return conn

# ==========================================================
# ENREGISTRER UN ÉVÉNEMENT IMPORTÉ
# ==========================================================

def save_imported_event(
    famille_id,
    membre_id,
    titre,
    description,
    date,
    heure,
    calendrier
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO evenements (
            famille_id,
            membre_id,
            titre,
            description,
            date,
            heure,
            calendrier,
            rappel
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        famille_id,
        membre_id,
        titre,
        description,
        date,
        heure,
        calendrier,
        1
    ))

    conn.commit()
    conn.close()

# ==========================================================
# IMPORTER UNE LISTE D'ÉVÉNEMENTS
# ==========================================================

def import_events(
    events,
    famille_id,
    membre_id,
    calendrier
):
    """
    Importe une liste d'événements provenant
    d'un agenda externe.
    """

    for event in events:

        save_imported_event(
            famille_id=famille_id,
            membre_id=membre_id,
            titre=event["titre"],
            description=event["description"],
            date=event["date"],
            heure=event["heure"],
            calendrier=calendrier
        )

    return True

# ==========================================================
# VÉRIFIER SI L'ÉVÉNEMENT EXISTE DÉJÀ
# ==========================================================

def event_exists(
    famille_id,
    membre_id,
    titre,
    date,
    heure
):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id
        FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        AND titre = ?
        AND date = ?
        AND heure = ?
    """, (
        famille_id,
        membre_id,
        titre,
        date,
        heure
    ))

    result = cursor.fetchone()

    conn.close()

    return result is not None

# ==========================================================
# IMPORTER UNE LISTE D'ÉVÉNEMENTS
# ==========================================================

def import_events(
    events,
    famille_id,
    membre_id,
    calendrier
):
    """
    Importe une liste d'événements provenant
    d'un agenda externe sans créer de doublons.
    """

    imported = 0
    skipped = 0

    for event in events:

        if not event_exists(
            famille_id,
            membre_id,
            event["titre"],
            event["date"],
            event["heure"]
        ):

            save_imported_event(
                famille_id=famille_id,
                membre_id=membre_id,
                titre=event["titre"],
                description=event["description"],
                date=event["date"],
                heure=event["heure"],
                calendrier=calendrier
            )

            imported += 1

        else:

            skipped += 1

    return {
        "imported": imported,
        "skipped": skipped
    }

# ==========================================================
# SUPPRIMER LES ÉVÉNEMENTS D'UN AGENDA IMPORTÉ
# ==========================================================

def delete_imported_events(
    famille_id,
    membre_id,
    calendrier
):
    """
    Supprime tous les événements importés
    depuis un agenda spécifique.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        AND calendrier = ?
    """, (
        famille_id,
        membre_id,
        calendrier
    ))

    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted

# ==========================================================
# LISTE DES AGENDAS IMPORTÉS
# ==========================================================

def get_imported_calendars(
    famille_id,
    membre_id
):
    """
    Retourne la liste des agendas externes
    déjà importés pour un membre.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT DISTINCT calendrier
        FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        ORDER BY calendrier
    """, (
        famille_id,
        membre_id
    ))

    calendars = [
        row["calendrier"]
        for row in cursor.fetchall()
    ]

    conn.close()

    return calendars

# ==========================================================
# NOMBRE D'ÉVÉNEMENTS IMPORTÉS PAR AGENDA
# ==========================================================

def count_imported_events(
    famille_id,
    membre_id,
    calendrier
):
    """
    Retourne le nombre d'événements importés
    pour un agenda donné.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        AND calendrier = ?
    """, (
        famille_id,
        membre_id,
        calendrier
    ))

    total = cursor.fetchone()["total"]

    conn.close()

    return total

# ==========================================================
# RÉINITIALISER UNE SYNCHRONISATION
# ==========================================================

def reset_calendar_import(
    famille_id,
    membre_id,
    calendrier
):
    """
    Supprime tous les événements importés
    d'un agenda afin de permettre
    une nouvelle synchronisation complète.
    """

    deleted = delete_imported_events(
        famille_id,
        membre_id,
        calendrier
    )

    return {
        "success": True,
        "deleted": deleted
    }

# ==========================================================
# STATISTIQUES DES IMPORTS
# ==========================================================

def get_import_statistics(
    famille_id,
    membre_id
):
    """
    Retourne les statistiques des agendas importés
    pour un membre.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            calendrier,
            COUNT(*) AS total
        FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        GROUP BY calendrier
        ORDER BY calendrier
    """, (
        famille_id,
        membre_id
    ))

    stats = []

    for row in cursor.fetchall():

        stats.append({
            "calendrier": row["calendrier"],
            "total": row["total"]
        })

    conn.close()

    return stats

# ==========================================================
# VÉRIFIER SI UN AGENDA EST DÉJÀ CONNECTÉ
# ==========================================================

def is_calendar_connected(
    famille_id,
    membre_id,
    calendrier
):
    """
    Vérifie si un agenda externe est déjà
    synchronisé pour ce membre.
    """

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        AND calendrier = ?
    """, (
        famille_id,
        membre_id,
        calendrier
    ))

    connected = cursor.fetchone()["total"] > 0

    conn.close()

    return connected

