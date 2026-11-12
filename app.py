# ==========================================================
# IMPORTS
# ==========================================================

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify
)
    

import os
import sqlite3
import pandas as pd
import requests
import bcrypt
import secrets
import base64
import tempfile

from datetime import datetime
from urllib.parse import urlencode

from config import (
    DATABASE,
    SECRET_KEY
)

from google_calendar import (
    get_google_auth_url,
    get_google_token,
    get_google_calendars,
    get_google_profile,
    refresh_google_token,
    sync_google_calendar_by_id
)

from apple_calendar import sync_apple_calendar


# ==========================================================
# CONFIGURATION GOOGLE SHEETS
# ==========================================================

SHEET_ID = "1JWwwLP3IKaG-ELsC3li84eouOFVFnv_C5MxBDQSfz3M"

# ==========================================================
# CONFIGURATION GOOGLE CALENDAR
# ==========================================================

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "http://127.0.0.1:5000/google/callback"
)


# ==========================================================
# CONFIGURATION MAKE
# ==========================================================

WEBHOOK_URL = "https://hook.eu1.make.com/942mf8fk2jehv637xc3s0tsjsxrad0gu"


# ==========================================================
# CONFIGURATION STRIPE — DIS-MOI TOUT
# ==========================================================


# Essai gratuit : 15 jours
TRIAL_DAYS = 15

TRIAL_LINK = "https://buy.stripe.com/00w28s2wifc1cmn7PW9fW0g"

# Abonnement mensuel : 10 € TTC
PRICE_MONTHLY = 10
STRIPE_LINK_MONTHLY = "https://buy.stripe.com/aFa14odaWd3Tdqrc6c9fW0n"

# Abonnement annuel : 90 € TTC
PRICE_YEARLY = 90
STRIPE_LINK_YEARLY = "https://buy.stripe.com/dRmaEY6Mye7Xcmngms9fW14"



# ==========================================================
# APPLICATION FLASK
# ==========================================================

app = Flask(__name__)

app.secret_key = SECRET_KEY

app.config["SESSION_PERMANENT"] = False

app.config["TEMPLATES_AUTO_RELOAD"] = True

# ==========================================================
# SQLITE
# ==========================================================

def get_connection():

    conn = sqlite3.connect(DATABASE)

    conn.row_factory = sqlite3.Row

    return conn


# ==========================================================
# SAUVEGARDE SQLITE VERS GOOGLE DRIVE
# ==========================================================

@app.route("/internal/database-backup", methods=["POST"])
def database_backup():

    backup_secret = os.environ.get("BACKUP_SECRET_KEY", "")
    received_secret = request.headers.get("X-Backup-Key", "")

    if not backup_secret or not secrets.compare_digest(
        received_secret, backup_secret
    ):
        return jsonify({"success": False, "error": "Accès refusé"}), 403

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        ) as temp_file:
            temp_path = temp_file.name

        source = sqlite3.connect(DATABASE)
        destination = sqlite3.connect(temp_path)

        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

        with open(temp_path, "rb") as backup_file:
            encoded_file = base64.b64encode(
                backup_file.read()
            ).decode("ascii")

        filename = (
            "dismoitout_"
            + datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            + ".db"
        )

        return jsonify({
            "success": True,
            "filename": filename,
            "file": encoded_file
        })

    except Exception:
        app.logger.exception("Erreur sauvegarde SQLite")
        return jsonify({
            "success": False,
            "error": "Échec de la sauvegarde"
        }), 500

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

# ==========================================================
# GOOGLE SHEETS
# ==========================================================

def load_users():

    url = (
        f"https://docs.google.com/spreadsheets/d/"
        f"{SHEET_ID}/export?format=csv"
    )

    df = pd.read_csv(url)

    df["username"] = (
        df["username"]
        .astype(str)
        .str.strip()
    )

    df["password"] = (
        df["password"]
        .astype(str)
        .str.strip()
    )

    df["expire"] = pd.to_datetime(
        df["expire"],
        errors="coerce"
    )

    return df

# ==========================================================
# CONTRÔLE DE L'ABONNEMENT
# ==========================================================

def check_login(username):

    df = load_users()

    user = df[
        df["username"]
        .astype(str)
        .str.strip()
        ==
        str(username).strip()
    ]

    if user.empty:
        return "error"

    expire_date = pd.to_datetime(
        user.iloc[0]["expire"],
        errors="coerce"
    )

    if pd.isna(expire_date):
        return "expired"

    if expire_date < pd.Timestamp.now():

        if (
            str(user.iloc[0]["trial"])
            .strip()
            .upper()
            ==
            "TRUE"
        ):
            return "trial_expired"

        return "subscription_expired"

    return "ok"


# ==========================================================
# WEBHOOK MAKE
# ==========================================================

def send_to_webhook(username, price=0, trial=True):

    data = {
        "username": username,
        "price": price,
        "trial": trial
    }

    try:

        requests.post(
            WEBHOOK_URL,
            json=data,
            timeout=10
        )

    except:

        pass

# ==========================================================
# MOTS DE PASSE
# ==========================================================

def hash_password(password):

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    )


def verify_password(
    password,
    hashed_password
):

    return bcrypt.checkpw(
        password.encode("utf-8"),
        hashed_password
    )


# ==========================================================
# ACCUEIL
# ==========================================================


@app.route("/")
def home():
    return render_template(
        "index.html",
        trial_link=TRIAL_LINK,
        price_monthly=PRICE_MONTHLY,
        stripe_link_monthly=STRIPE_LINK_MONTHLY,
        price_yearly=PRICE_YEARLY,
        stripe_link_yearly=STRIPE_LINK_YEARLY
    )




# ==========================================================
# CONNEXION
# ==========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET":
        return render_template(
            "login.html",
            stripe_link_monthly=STRIPE_LINK_MONTHLY,
            price_monthly=PRICE_MONTHLY,
            stripe_link_yearly=STRIPE_LINK_YEARLY,
            price_yearly=PRICE_YEARLY
        )

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    result = check_login(email)

    if result == "error":

        flash("Compte introuvable.")

        return redirect(url_for("login", abonnement="expire"))
    if result == "trial_expired":

        flash(
            "Votre essai gratuit est terminé. Choisissez votre abonnement pour continuer."
        )

        return redirect(url_for("login", abonnement="expire"))
    if result == "subscription_expired":

        flash(
            "Votre abonnement est expiré. Choisissez votre formule pour renouveler votre accès."
        )

        return redirect(url_for("login", abonnement="expire"))
        
    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM admin
        WHERE email = ?
        """,
        (email,)
    )

    admin = cursor.fetchone()

    conn.close()

    if admin is None:

        flash("Compte introuvable.")

        return redirect(url_for("home"))

    if not verify_password(
        password,
        admin["password"]
    ):

        flash("Mot de passe incorrect.")

        return redirect(url_for("home"))

    session["logged"] = True

    session["admin_logged"] = True

    session["admin_id"] = admin["id"]
    session["email"] = email

    session["user_name"] = (
        f"{admin['prenom']} {admin['nom']}"
    )

    return redirect(
        url_for("dashboard")
    )

    
# ==========================================================
# DÉCONNEXION
# ==========================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("home"))




# ==========================================================
# LISTE MEMBRES
# ==========================================================

# ==========================================================
# LISTE MEMBRES — ADMINISTRATEUR UNIQUEMENT
# ==========================================================

@app.route("/members")
def members():

    if not admin_required():
        return redirect(url_for("home"))

    famille_id = get_family_id(session.get("admin_id"))

    if famille_id is None:
        flash("Veuillez d'abord créer votre famille.")
        return redirect(url_for("dashboard"))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom
        """,
        (famille_id,)
    )

    membres = cursor.fetchall()
    conn.close()

    return render_template(
        "dashboard_member.html",
        membres=membres
    )





# ==========================================================
# RÉCUPÉRATION DE LA FAMILLE
# ==========================================================

def get_family(admin_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM famille
        WHERE admin_id = ?
        """,
        (admin_id,)
    )

    famille = cursor.fetchone()

    conn.close()

    return famille

# ==========================================================
# RÉCUPÉRATION DES MEMBRES
# ==========================================================

def get_members(famille_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom, nom
        """,
        (famille_id,)
    )

    membres = cursor.fetchall()

    conn.close()

    return membres

# ==========================================================
# NOMBRE DE MEMBRES
# ==========================================================

def count_members(famille_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM membres
        WHERE famille_id = ?
        """,
        (famille_id,)
    )

    total = cursor.fetchone()[0]

    conn.close()

    return total

# ==========================================================
# VÉRIFICATION ACCÈS ADMINISTRATEUR
# ==========================================================

def admin_required():

    return (
        session.get("logged") is True
        and session.get("admin_logged") is True
    )

# ==========================================================
# CRÉATION D'UN MEMBRE
# ==========================================================


def create_member(
    famille_id,
    prenom,
    nom,
    email,
    peut_modifier=True,
    role="enfant"
):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO membres
        (
            famille_id,
            prenom,
            nom,
            email,
            password,
            role,
            peut_modifier
        )
        VALUES (?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            famille_id,
            prenom,
            nom,
            email,
            role,
            1 if peut_modifier else 0
        )
    )

    conn.commit()
    conn.close()

    return True


# ==========================================================
# VÉRIFICATION E-MAIL MEMBRE
# ==========================================================

def member_email_exists(email):

    if not email.strip():
        return False

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM membres
        WHERE email = ?
        """,
        (email.strip().lower(),)
    )

    membre = cursor.fetchone()

    conn.close()

    return membre is not None

# ==========================================================
# SUPPRESSION D'UN MEMBRE
# ==========================================================

def delete_member(
    membre_id,
    famille_id
):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM membres
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            membre_id,
            famille_id
        )
    )

    conn.commit()

    deleted = cursor.rowcount > 0

    conn.close()

    return deleted

# ==========================================================
# VÉRIFICATION FAMILLE EXISTANTE
# ==========================================================

def family_exists(admin_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM famille
        WHERE admin_id = ?
        """,
        (admin_id,)
    )

    famille = cursor.fetchone()

    conn.close()

    return famille is not None

# ==========================================================
# RÉCUPÉRER L'ID D'UNE FAMILLE
# ==========================================================

def get_family_id(admin_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM famille
        WHERE admin_id = ?
        """,
        (admin_id,)
    )

    famille = cursor.fetchone()

    conn.close()

    if famille:
        return famille[0]

    return None
    

# ==========================================================
# RÉCUPÉRER L'ADMINISTRATEUR D'UNE FAMILLE
# ==========================================================

def get_family_admin(famille_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT admin_id
        FROM famille
        WHERE id = ?
        """,
        (famille_id,)
    )

    famille = cursor.fetchone()

    conn.close()

    if famille:
        return famille[0]

    return None

# ==========================================================
# VÉRIFIER L'APPARTENANCE D'UN MEMBRE À UNE FAMILLE
# ==========================================================

def member_belongs_to_family(
    membre_id,
    famille_id
):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM membres
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            membre_id,
            famille_id
        )
    )

    membre = cursor.fetchone()

    conn.close()

    return membre is not None

# ==========================================================
# VÉRIFIER SI UN EMAIL DE MEMBRE EXISTE
# ==========================================================

def member_exists_by_email(email):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM membres
        WHERE email = ?
        """,
        (email,)
    )

    membre = cursor.fetchone()

    conn.close()

    return membre is not None

# ==========================================================
# RÉCUPÉRER UN MEMBRE PAR SON EMAIL
# ==========================================================

def get_member_by_email(email):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE email = ?
        """,
        (email,)
    )

    membre = cursor.fetchone()

    conn.close()

    return membre

# ==========================================================
# RÉCUPÉRER UN MEMBRE PAR SON ID
# ==========================================================

def get_member_by_id(membre_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE id = ?
        """,
        (membre_id,)
    )

    membre = cursor.fetchone()

    conn.close()

    return membre

# ==========================================================
# RÉCUPÉRER L'ID DU MEMBRE CONNECTÉ
# ==========================================================

def get_current_member_id(email):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM membres
        WHERE email = ?
        """,
        (email,)
    )

    membre = cursor.fetchone()

    conn.close()

    if membre:
        return membre[0]

    return None


# ==========================================================
# VÉRIFIER LES DROITS DE MODIFICATION D'UN MEMBRE
# ==========================================================

def member_can_modify(membre_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT peut_modifier
        FROM membres
        WHERE id = ?
        """,
        (membre_id,)
    )

    membre = cursor.fetchone()

    conn.close()

    if membre:
        return bool(membre[0])

    return False

# ==========================================================
# RÉCUPÉRER LE MEMBRE ET SA FAMILLE
# ==========================================================

def get_member_family(membre_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            membres.id,
            membres.famille_id,
            membres.prenom,
            membres.nom,
            membres.email,
            membres.peut_modifier,
            famille.admin_id
        FROM membres
        JOIN famille
            ON membres.famille_id = famille.id
        WHERE membres.id = ?
        """,
        (membre_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result

# ==========================================================
# VÉRIFIER SI UN MEMBRE EST ADMINISTRATEUR
# ==========================================================

def is_family_admin(admin_id, famille_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM famille
        WHERE id = ?
        AND admin_id = ?
        """,
        (
            famille_id,
            admin_id
        )
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None



# ==========================================================
# INITIALISATION SQLITE
# ==========================================================

def init_database():

    conn = get_connection()

    cursor = conn.cursor()

    # ======================================================
    # ADMINISTRATEUR
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            conjoint INTEGER DEFAULT 0,
            garde_partagee INTEGER DEFAULT 0,
            frequence_garde TEXT,
            prochaine_date_garde TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # ======================================================
    # FAMILLE
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS famille (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            nom_famille TEXT,
            FOREIGN KEY(admin_id)
                REFERENCES admin(id)
        )
        """
    )

    # ======================================================
    # MEMBRES
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS membres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER,
            prenom TEXT,
            nom TEXT,
            email TEXT,
            password TEXT,
            role TEXT,
            peut_modifier INTEGER DEFAULT 1,
            FOREIGN KEY(famille_id)
                REFERENCES famille(id)
        )
        """
    )

    # ======================================================
    # ÉVÉNEMENTS
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS evenements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER,
            membre_id INTEGER,
            titre TEXT,
            description TEXT,
            date TEXT,
            heure TEXT,
            calendrier TEXT,
            rappel INTEGER DEFAULT 1,
            type_evenement TEXT,
            created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(famille_id)
                REFERENCES famille(id),
            FOREIGN KEY(membre_id)
                REFERENCES membres(id)
        )
        """
    )

    # ======================================================
    # CONNEXION GOOGLE CALENDAR
    # ======================================================

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS google_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER NOT NULL UNIQUE,
            google_email TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(famille_id)
                REFERENCES famille(id)
        )
        """
    )
    # ======================================================
    # COMPATIBILITÉ AVEC UNE BASE EXISTANTE
    # ======================================================

    cursor.execute(
        "PRAGMA table_info(admin)"
    )

    colonnes_admin = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

    colonnes_a_ajouter = {
        "conjoint":
            "INTEGER DEFAULT 0",

        "garde_partagee":
            "INTEGER DEFAULT 0",

        "frequence_garde":
            "TEXT",

        "prochaine_date_garde":
            "TEXT"
    }

    for colonne, definition in colonnes_a_ajouter.items():

        if colonne not in colonnes_admin:

            cursor.execute(
                f"""
                ALTER TABLE admin
                ADD COLUMN {colonne}
                {definition}
                """
            )

    cursor.execute(
        "PRAGMA table_info(evenements)"
    )

    colonnes_evenements = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

    if "type_evenement" not in colonnes_evenements:

        cursor.execute(
            """
            ALTER TABLE evenements
            ADD COLUMN type_evenement TEXT
            """
        )
    # ======================================================
    # DROITS ET CONFIDENTIALITÉ DES ÉVÉNEMENTS
    # ======================================================

    cursor.execute(
        "PRAGMA table_info(famille)"
    )

    colonnes_famille = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

    if "mode_confidentialite" not in colonnes_famille:

        cursor.execute(
            """
            ALTER TABLE famille
            ADD COLUMN mode_confidentialite TEXT
            DEFAULT 'private'
            """
        )

    cursor.execute(
        "PRAGMA table_info(evenements)"
    )

    colonnes_evenements = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

        colonnes_evenements_a_ajouter = {
        "created_by_admin_id": "INTEGER",
        "created_by_membre_id": "INTEGER",
        "membre_peut_modifier": "INTEGER DEFAULT 0",
        "membre_peut_supprimer": "INTEGER DEFAULT 0",
        "statut_validation": "TEXT DEFAULT 'accepte'",
        "valide_par_admin_id": "INTEGER",
        "valide_par_membre_id": "INTEGER",
        "concerne_admin": "INTEGER DEFAULT 0"
    }

    for colonne, definition in colonnes_evenements_a_ajouter.items():

        if colonne not in colonnes_evenements:

            cursor.execute(
                f"""
                ALTER TABLE evenements
                ADD COLUMN {colonne} {definition}
                """
            )
    # Autorisation spécifique d'accès à l'agenda administrateur

    cursor.execute(
        "PRAGMA table_info(membres)"
    )

    colonnes_membres = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

    if "acces_agenda_admin" not in colonnes_membres:

        cursor.execute(
            """
            ALTER TABLE membres
            ADD COLUMN acces_agenda_admin INTEGER DEFAULT 0
            """
        )
    # Les anciens rendez-vous attribués à un membre
    # sont considérés comme créés par ce membre.

    cursor.execute(
        """
        UPDATE evenements
        SET created_by_membre_id = membre_id
        WHERE membre_id IS NOT NULL
        AND created_by_membre_id IS NULL
        """
    )
    conn.commit()

    conn.close()

# ==========================================================
# INITIALISATION DE LA BASE
# ==========================================================

init_database()

# ==========================================================
# INSCRIPTION ADMINISTRATEUR
# ==========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "GET":
        return render_template("register.html")

    prenom = request.form.get(
        "prenom",
        ""
    ).strip()

    nom = request.form.get(
        "nom",
        ""
    ).strip()

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    password2 = request.form.get(
        "password2",
        ""
    )

    conjoint = request.form.get(
        "conjoint",
        "non"
    )

    garde_partagee = request.form.get(
        "garde_partagee",
        "non"
    )

    frequence_garde = request.form.get(
        "frequence_garde",
        ""
    ).strip()

    prochaine_date_garde = request.form.get(
        "prochaine_date_garde",
        ""
    ).strip()

    # ======================================================
    # VÉRIFICATIONS
    # ======================================================

    if prenom == "":

        flash(
            "Veuillez saisir votre prénom."
        )

        return redirect(
            url_for("home")
        )

    if nom == "":

        flash(
            "Veuillez saisir votre nom."
        )

        return redirect(
            url_for("home")
        )

    if email == "":

        flash(
            "Veuillez saisir votre adresse e-mail."
        )

        return redirect(
            url_for("home")
        )

    if password == "":

        flash(
            "Veuillez saisir un mot de passe."
        )

        return redirect(
            url_for("home")
        )

    if password != password2:

        flash(
            "Les mots de passe sont différents."
        )

        return redirect(
            url_for("home")
        )

    # ======================================================
    # GARDE PARTAGÉE
    # ======================================================

    if garde_partagee == "oui":

        frequences_autorisees = [
            "1 semaine sur deux",
            "Tous les quinze jours",
            "Tous les mois"
        ]

        if frequence_garde not in frequences_autorisees:

            flash(
                "Veuillez sélectionner la fréquence de la garde."
            )

            return redirect(
                url_for("home")
            )

        if prochaine_date_garde == "":

            flash(
                "Veuillez indiquer la prochaine date de garde."
            )

            return redirect(
                url_for("home")
            )

    else:

        frequence_garde = ""

        prochaine_date_garde = ""

    # ======================================================
    # VÉRIFICATION E-MAIL
    # ======================================================

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM admin
        WHERE email = ?
        """,
        (email,)
    )

    existe = cursor.fetchone()

    if existe:

        conn.close()

        flash(
            "Cette adresse e-mail existe déjà."
        )

        return redirect(
            url_for("home")
        )

    # ======================================================
    # CRÉATION DU COMPTE
    # ======================================================

    hashed = hash_password(password)

    cursor.execute(
        """
        INSERT INTO admin
        (
            nom,
            prenom,
            email,
            password,
            conjoint,
            garde_partagee,
            frequence_garde,
            prochaine_date_garde
        )
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            nom,
            prenom,
            email,
            hashed,
            1 if conjoint == "oui" else 0,
            1 if garde_partagee == "oui" else 0,
            frequence_garde,
            prochaine_date_garde
        )
    )

    conn.commit()

    conn.close()

    # ======================================================
    # WEBHOOK MAKE
    # ======================================================

    send_to_webhook(email)

    flash(
        "Compte créé avec succès."
    )

    return redirect(
        TRIAL_LINK
    )

# ==========================================================
# CRÉATION DES ÉVÉNEMENTS DE GARDE PARTAGÉE
# ==========================================================

def create_shared_custody_events(
    famille_id,
    prochaine_date,
    frequence
):

    if not prochaine_date:
        return

    try:
        date_depart = datetime.strptime(
            prochaine_date,
            "%Y-%m-%d"
        ).date()
    except ValueError:
        return

    conn = get_connection()
    cursor = conn.cursor()

    # Une année de gardes prévisionnelles
    nombre_evenements = 24

    if frequence == "1 semaine sur deux":
        intervalle = 7

    elif frequence == "Tous les quinze jours":
        intervalle = 15

    elif frequence == "Tous les mois":
        intervalle = None

    else:
        conn.close()
        return

    date_garde = date_depart

    for numero in range(nombre_evenements):

        date_str = date_garde.strftime("%Y-%m-%d")

        cursor.execute(
            """
            SELECT id
            FROM evenements
            WHERE famille_id = ?
            AND date = ?
            AND type_evenement = ?
            """,
            (
                famille_id,
                date_str,
                "garde_partagee"
            )
        )

        existe = cursor.fetchone()

        if not existe:

            cursor.execute(
                """
                INSERT INTO evenements
                (
                    famille_id,
                    membre_id,
                    titre,
                    description,
                    date,
                    heure,
                    calendrier,
                    rappel,
                    type_evenement
                )
                VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    famille_id,
                    None,
                    "Garde partagée",
                    "Garde chez l'autre personne",
                    date_str,
                    "",
                    "Famille",
                    1,
                    "garde_partagee"
                )
            )

        if frequence == "Tous les mois":

            mois = date_garde.month
            annee = date_garde.year

            if mois == 12:
                mois_suivant = 1
                annee_suivante = annee + 1
            else:
                mois_suivant = mois + 1
                annee_suivante = annee

            try:
                date_garde = date_garde.replace(
                    year=annee_suivante,
                    month=mois_suivant
                )
            except ValueError:
                # Pour les dates comme le 31
                # lorsque le mois suivant n'a pas ce jour
                date_garde = date_garde.replace(
                    year=annee_suivante,
                    month=mois_suivant,
                    day=1
                )

        else:

            from datetime import timedelta

            date_garde = (
                date_garde
                + timedelta(days=intervalle)
            )

    conn.commit()
    conn.close()

# ==========================================================
# CRÉATION FAMILLE
# ==========================================================

@app.route(
    "/create_family",
    methods=["POST"]
)
def create_family():

    if not admin_required():
    
        flash("Seul l'administrateur peut créer une famille.")
    
        return redirect(
            url_for("home")
        )

    nom_famille = request.form.get(
        "nom_famille",
        ""
    ).strip()

    if nom_famille == "":

        flash(
            "Veuillez saisir un nom."
        )

        return redirect(
            url_for("dashboard")
        )

    admin_id = session.get(
        "admin_id"
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO famille
        (
            admin_id,
            nom_famille
        )
        VALUES
        (?, ?)
        """,
        (
            admin_id,
            nom_famille
        )
    )

    famille_id = cursor.lastrowid

    cursor.execute(
        """
        SELECT
            garde_partagee,
            frequence_garde,
            prochaine_date_garde
        FROM admin
        WHERE id = ?
        """,
        (admin_id,)
    )

    admin = cursor.fetchone()

    conn.commit()
    conn.close()

    session["famille_id"] = famille_id

    if admin and admin["garde_partagee"]:

        create_shared_custody_events(
            famille_id,
            admin["prochaine_date_garde"],
            admin["frequence_garde"]
        )

    flash(
        "Famille créée."
    )

    return redirect(
        url_for("dashboard")
    )

# ==========================================================
# CRÉATION D'UN MEMBRE
# ==========================================================


@app.route(
    "/add_member",
    methods=["POST"]
)
def add_member():

    if not admin_required():
        return redirect(url_for("home"))

    famille_id = get_family_id(session["admin_id"])

    if famille_id is None:
        flash("Veuillez d'abord créer votre famille.")
        return redirect(url_for("dashboard"))

    if count_members(famille_id) >= 6:
        flash("Votre famille contient déjà 6 membres.")
        return redirect(url_for("members"))

    prenom = request.form.get("prenom", "").strip()
    nom = request.form.get("nom", "").strip()
    email = request.form.get("email", "").strip().lower()

    peut_modifier = request.form.get("peut_modifier") == "oui"

    role = request.form.get("role", "enfant").strip().lower()

    roles_autorises = ["enfant", "conjoint", "ex-conjoint"]

    if role not in roles_autorises:
        role = "enfant"

    if not prenom:
        flash("Veuillez saisir le prénom.")
        return redirect(url_for("members"))

    if not nom:
        flash("Veuillez saisir le nom.")
        return redirect(url_for("members"))

    if email and member_email_exists(email):
        flash("Cette adresse e-mail est déjà utilisée.")
        return redirect(url_for("members"))

    create_member(
        famille_id,
        prenom,
        nom,
        email,
        peut_modifier,
        role
    )

    flash("Membre ajouté avec succès. Il pourra choisir son mot de passe lors de sa première connexion.")

    return redirect(url_for("members"))


# ==========================================================
# SUPPRIMER UN MEMBRE
# ==========================================================

@app.route(
    "/delete_member/<int:membre_id>",
    methods=["POST"]
)
def delete_member_route(membre_id):

    if not admin_required():

        return redirect(
            url_for("home")
        )

    famille_id = get_family_id(
        session["admin_id"]
    )

    if famille_id is None:

        return redirect(
            url_for("dashboard")
        )

    if not member_belongs_to_family(
        membre_id,
        famille_id
    ):

        flash(
            "Membre introuvable."
        )

        return redirect(
            url_for("members")
        )

    delete_member(
        membre_id,
        famille_id
    )

    flash(
        "Membre supprimé."
    )

    return redirect(
        url_for("members")
    )

# ==========================================================
# TABLEAU DE BORD ADMINISTRATEUR
# ==========================================================

@app.route("/dashboard")
def dashboard():

    if not admin_required():

        if session.get("logged") and session.get("membre_id"):
            return redirect(url_for("member_dashboard"))

        return redirect(url_for("home"))

    admin_id = session.get("admin_id")

    conn = get_connection()
    admin = conn.execute(
        "SELECT email FROM admin WHERE id = ?",
        (admin_id,)
    ).fetchone()
    conn.close()

    if not admin:
        session.clear()
        return redirect(url_for("login"))

    result = check_login(admin["email"])

    if result != "ok":
        session.clear()
        return redirect(url_for("login", abonnement="expire"))


    result = check_login(session.get("email", ""))

    if result != "ok":
        session.clear()

        if result == "trial_expired":
            flash(
                "Votre essai gratuit est terminé. Choisissez votre abonnement pour continuer."
            )
        else:
            flash(
                "Votre abonnement est expiré. Choisissez votre formule pour renouveler votre accès."
            )

        return redirect(url_for("home"))

    admin_id = session.get(
        "admin_id"
    )

    famille = get_family(
        admin_id
    )

    if famille:

        session["famille_id"] = famille["id"]

        membres = get_members(
            famille["id"]
        )

        total_membres = count_members(
            famille["id"]
        )

    else:

        membres = []

        total_membres = 0

    return render_template(
        "dashboard_admin.html",
        famille=famille,
        membres=membres,
        total_membres=total_membres
    )
# ==========================================================
# CONNEXION MEMBRE
# ==========================================================

@app.route("/member_login", methods=["POST"])
def member_login():

    identifiant = request.form.get(
        "identifiant", ""
    ).strip()

    password = request.form.get(
        "password", ""
    )

    if not identifiant or not password:
        flash("Veuillez saisir vos identifiants.")
        return redirect(url_for("login"))

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE LOWER(email) = LOWER(?)
        OR LOWER(TRIM(prenom || ' ' || nom)) = LOWER(?)
        """,
        (identifiant, identifiant)
    )

    candidats = cursor.fetchall()

    if len(candidats) != 1:
        conn.close()
        flash("Membre introuvable ou identifiant non unique.")
        return redirect(url_for("login"))

    membre = candidats[0]

    # Première connexion : le membre choisit son mot de passe
    if not membre["password"]:

        if len(password) < 8:
            conn.close()
            flash("Le mot de passe doit contenir au moins 8 caractères.")
            return redirect(url_for("login"))

        hashed_password = hash_password(password)

        cursor.execute(
            """
            UPDATE membres
            SET password = ?
            WHERE id = ?
            AND password IS NULL
            """,
            (hashed_password, membre["id"])
        )

        if cursor.rowcount != 1:
            conn.close()
            flash("Impossible de créer le mot de passe. Réessayez.")
            return redirect(url_for("login"))

        conn.commit()

    # Connexions suivantes : vérification du mot de passe
    elif not verify_password(password, membre["password"]):

        conn.close()
        flash("Mot de passe incorrect.")
        return redirect(url_for("login"))

    conn.close()

    session.clear()

    session["logged"] = True
    session["admin_logged"] = False
    session["membre_id"] = membre["id"]
    session["famille_id"] = membre["famille_id"]
    session["user_name"] = (
        f"{membre['prenom']} {membre['nom']}"
    )

    return redirect(url_for("member_dashboard"))
# ==========================================================
# TABLEAU DE BORD MEMBRE
# ==========================================================

# ==========================================================
# TABLEAU DE BORD MEMBRE
# ==========================================================

@app.route("/member_dashboard")
def member_dashboard():

    if not session.get("logged") or session.get("admin_logged"):
        return redirect(url_for("home"))

    membre_id = session.get("membre_id")

    if membre_id is None:
        return redirect(url_for("home"))

    membre = get_member_by_id(membre_id)

    if membre is None:
        session.clear()
        return redirect(url_for("home"))

    if membre["famille_id"] != session.get("famille_id"):
        session.clear()
        return redirect(url_for("home"))

    famille = get_member_family(membre_id)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE famille_id = ?
        AND (
            membre_id = ?
            OR calendrier = 'Famille'
        )
        ORDER BY date, heure
        """,
        (
            membre["famille_id"],
            membre_id
        )
    )

    evenements = cursor.fetchall()
    conn.close()

    return render_template(
        "member_dashboard.html",
        membre=membre,
        famille=famille,
        evenements=evenements
    )
# ==========================================================
# ESPACE FAMILLE
# ==========================================================

@app.route("/family")
def family():

    if not session.get("logged"):
        return redirect(url_for("home"))

    est_admin = session.get("admin_logged") is True

    if est_admin:
        admin_id = session.get("admin_id")
    else:
        membre_id = session.get("membre_id")

        if membre_id is None:
            return redirect(url_for("home"))

        membre_connecte = get_member_family(membre_id)

        if membre_connecte is None:
            return redirect(url_for("home"))

        admin_id = membre_connecte["admin_id"]

    famille = get_family(admin_id)

    admin = None
    membres = []
    total_membres = 0

    conn = get_connection()

    admin = conn.execute(
        """
        SELECT prenom, nom
        FROM admin
        WHERE id = ?
        """,
        (admin_id,)
    ).fetchone()

    conn.close()

    if famille:
        membres = get_members(famille["id"])
        total_membres = count_members(famille["id"])

    return render_template(
        "family.html",
        famille=famille,
        admin=admin,
        membres=membres,
        total_membres=total_membres,
        est_admin=est_admin
    )

# ==========================================================
# AJOUT D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/add_event",
    methods=["POST"]
)
def add_event():

    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")

    if famille_id is None:
        flash("Famille introuvable.")
        return redirect(url_for("dashboard"))

    titre = request.form.get("titre", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    heure = request.form.get("heure", "").strip()
    calendrier = request.form.get("calendrier", "Famille").strip()

    if not titre or not date:
        flash("Veuillez saisir un titre et une date.")
        return redirect(url_for("calendar"))

    rappel = (
        1 if request.form.get("rappel") == "oui" else 0
    )

    admin_logged = bool(session.get("admin_logged"))
    admin_id = session.get("admin_id")
    auteur_membre_id = session.get("membre_id")

    membre_id_form = request.form.get("membre_id", "").strip()
    concerne_admin = request.form.get("concerne_admin") == "oui"

    conn = get_connection()
    cursor = conn.cursor()

    role_auteur = None

    if not admin_logged:

        cursor.execute(
            """
            SELECT id, famille_id, role
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (auteur_membre_id, famille_id)
        )

        auteur = cursor.fetchone()

        if auteur is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))

        role_auteur = (auteur["role"] or "enfant").lower()

    # ------------------------------------------------------
    # DÉTERMINER LE DESTINATAIRE
    # ------------------------------------------------------

    destinataire_id = None

    if membre_id_form:

        try:
            destinataire_id = int(membre_id_form)
        except ValueError:
            conn.close()
            flash("Membre destinataire invalide.")
            return redirect(url_for("calendar"))

        cursor.execute(
            """
            SELECT id
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (destinataire_id, famille_id)
        )

        if cursor.fetchone() is None:
            conn.close()
            flash("Ce membre n'appartient pas à votre famille.")
            return redirect(url_for("calendar"))

    # ------------------------------------------------------
    # IDENTIFIER LE CRÉATEUR ET LE STATUT
    # ------------------------------------------------------

    created_by_admin_id = admin_id if admin_logged else None
    created_by_membre_id = None if admin_logged else auteur_membre_id

    statut_validation = "accepte"
    valide_par_admin_id = None
    valide_par_membre_id = None

    # Un administrateur valide directement ses propres créations.
    if admin_logged:
        statut_validation = "accepte"
        valide_par_admin_id = admin_id

    # Un membre crée son propre rendez-vous.
    elif destinataire_id == auteur_membre_id and not concerne_admin:
        statut_validation = "accepte"
        valide_par_membre_id = auteur_membre_id

    # Toute proposition destinée à une autre personne
    # reste en attente de validation.
    else:
        statut_validation = "en_attente"

    # ------------------------------------------------------
    # RENDEZ-VOUS DESTINÉ À L'ADMINISTRATEUR
    # ------------------------------------------------------

    if concerne_admin:

        if admin_logged:
            conn.close()
            flash("Vous ne pouvez pas créer une proposition destinée à votre propre agenda.")
            return redirect(url_for("calendar"))

        # L'accès à l'agenda administrateur ne donne pas
        # automatiquement le droit d'y ajouter un rendez-vous.
        statut_validation = "en_attente"
        destinataire_id = None

    # ------------------------------------------------------
    # ENREGISTREMENT
    # ------------------------------------------------------

    cursor.execute(
        """
        INSERT INTO evenements
        (
            famille_id,
            membre_id,
            titre,
            description,
            date,
            heure,
            calendrier,
            rappel,
            type_evenement,
            created_by_admin_id,
            created_by_membre_id,
            statut_validation,
            valide_par_admin_id,
            valide_par_membre_id,
            concerne_admin
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            famille_id,
            destinataire_id,
            titre,
            description,
            date,
            heure,
            calendrier,
            rappel,
            "normal",
            created_by_admin_id,
            created_by_membre_id,
            statut_validation,
            valide_par_admin_id,
            valide_par_membre_id,
            1 if concerne_admin else 0
        )
    )

    conn.commit()
    conn.close()

    if statut_validation == "en_attente":
        flash("Votre proposition de rendez-vous a été envoyée pour validation.")
    else:
        flash("Événement ajouté.")

    return redirect(url_for("calendar"))
    
# ==========================================================
# MODIFICATION D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/edit_event/<int:event_id>",
    methods=["POST"]
)
def edit_event(event_id):

    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    admin_logged = session.get("admin_logged") is True
    membre_id = session.get("membre_id")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (event_id, famille_id)
    )

    evenement = cursor.fetchone()

    if evenement is None:
        conn.close()
        flash("Événement introuvable.")
        return redirect(url_for("calendar"))

    if evenement["type_evenement"] == "garde_partagee":
        conn.close()
        flash("Les gardes partagées sont générées automatiquement.")
        return redirect(url_for("calendar"))

    autorise = False

    if admin_logged:
        autorise = True

    else:
        cursor.execute(
            """
            SELECT role
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        membre = cursor.fetchone()

        if membre is None:
            conn.close()
            return redirect(url_for("home"))

        role = (membre["role"] or "enfant").lower()

        # Un membre peut modifier son propre rendez-vous.
        if (
            evenement["membre_id"] == membre_id
            and evenement["created_by_membre_id"] == membre_id
        ):
            autorise = True

        # Seul le conjoint actuel peut déplacer
        # un rendez-vous créé par l'administrateur.
        elif (
            role == "conjoint"
            and evenement["created_by_admin_id"] is not None
        ):
            autorise = True

    if not autorise:
        conn.close()
        flash("Vous n'avez pas l'autorisation de modifier ce rendez-vous.")
        return redirect(url_for("calendar"))

    titre = request.form.get("titre", "").strip()
    description = request.form.get("description", "").strip()
    date = request.form.get("date", "").strip()
    heure = request.form.get("heure", "").strip()

    if not titre or not date:
        conn.close()
        flash("Veuillez saisir un titre et une date.")
        return redirect(url_for("calendar"))

    cursor.execute(
        """
        UPDATE evenements
        SET
            titre = ?,
            description = ?,
            date = ?,
            heure = ?
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            titre,
            description,
            date,
            heure,
            event_id,
            famille_id
        )
    )

    conn.commit()
    conn.close()

    flash("Événement modifié.")
    return redirect(url_for("calendar"))
    
# ==========================================================
# SUPPRESSION D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/delete_event/<int:event_id>",
    methods=["POST"]
)
def delete_event(event_id):

    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    admin_logged = session.get("admin_logged") is True
    membre_id = session.get("membre_id")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (event_id, famille_id)
    )

    evenement = cursor.fetchone()

    if evenement is None:
        conn.close()
        flash("Événement introuvable.")
        return redirect(url_for("calendar"))

    if evenement["type_evenement"] == "garde_partagee":
        conn.close()
        flash("Les gardes partagées automatiques ne peuvent pas être supprimées ici.")
        return redirect(url_for("calendar"))

    autorise = False

    # L'administrateur peut supprimer les événements de sa famille.
    if admin_logged:
        autorise = True

    else:
        cursor.execute(
            """
            SELECT id, role
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        membre = cursor.fetchone()

        if membre is None:
            conn.close()
            return redirect(url_for("home"))

        # Un membre peut supprimer uniquement un rendez-vous
        # qu'il a lui-même créé.
        if evenement["created_by_membre_id"] == membre_id:
            autorise = True

    if not autorise:
        conn.close()
        flash("Vous ne pouvez pas supprimer ce rendez-vous.")
        return redirect(url_for("calendar"))

    cursor.execute(
        """
        DELETE FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (event_id, famille_id)
    )

    conn.commit()
    conn.close()

    flash("Événement supprimé.")
    return redirect(url_for("calendar"))

# ==========================================================
# VALIDATION DES RENDEZ-VOUS PROPOSÉS
# ==========================================================

@app.route(
    "/validate_event/<int:event_id>",
    methods=["POST"]
)
def validate_event(event_id):

    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    admin_logged = session.get("admin_logged") is True
    membre_id = session.get("membre_id")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        AND statut_validation = 'en_attente'
        """,
        (event_id, famille_id)
    )

    evenement = cursor.fetchone()

    if evenement is None:
        conn.close()
        flash("Cette proposition est introuvable ou déjà traitée.")
        return redirect(url_for("calendar"))

    autorise = False

    if admin_logged:
        autorise = True

    elif evenement["concerne_admin"] == 0:
        if evenement["membre_id"] == membre_id:
            autorise = True

    if not autorise:
        conn.close()
        flash("Vous n'êtes pas autorisé à valider cette proposition.")
        return redirect(url_for("calendar"))

    if admin_logged:
        cursor.execute(
            """
            UPDATE evenements
            SET statut_validation = 'accepte',
                valide_par_admin_id = ?
            WHERE id = ?
            """,
            (session.get("admin_id"), event_id)
        )
    else:
        cursor.execute(
            """
            UPDATE evenements
            SET statut_validation = 'accepte',
                valide_par_membre_id = ?
            WHERE id = ?
            """,
            (membre_id, event_id)
        )

    conn.commit()
    conn.close()

    flash("Rendez-vous accepté.")
    return redirect(url_for("calendar"))


@app.route(
    "/refuse_event/<int:event_id>",
    methods=["POST"]
)
def refuse_event(event_id):

    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    admin_logged = session.get("admin_logged") is True
    membre_id = session.get("membre_id")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        AND statut_validation = 'en_attente'
        """,
        (event_id, famille_id)
    )

    evenement = cursor.fetchone()

    if evenement is None:
        conn.close()
        flash("Cette proposition est introuvable ou déjà traitée.")
        return redirect(url_for("calendar"))

    autorise = False

    if admin_logged:
        autorise = True

    elif evenement["concerne_admin"] == 0:
        if evenement["membre_id"] == membre_id:
            autorise = True

    if not autorise:
        conn.close()
        flash("Vous n'êtes pas autorisé à refuser cette proposition.")
        return redirect(url_for("calendar"))

    cursor.execute(
        """
        UPDATE evenements
        SET statut_validation = 'refuse'
        WHERE id = ?
        """,
        (event_id,)
    )

    conn.commit()
    conn.close()

    flash("Proposition refusée.")
    return redirect(url_for("calendar"))
    
# ==========================================================
# CALENDRIER
# ==========================================================

@app.route("/calendar")
def calendar():

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    famille_id = session.get(
        "famille_id"
    )

    if famille_id is None:

        return redirect(
            url_for("dashboard")
        )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            e.*,
            m.prenom,
            m.nom
        FROM evenements e
        LEFT JOIN membres m
            ON e.membre_id = m.id
        WHERE e.famille_id = ?
        ORDER BY e.date, e.heure
        """,
        (
            famille_id,
        )
    )

    evenements = cursor.fetchall()

    cursor.execute(
        """
        SELECT id, prenom, nom
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom, nom
        """,
        (
            famille_id,
        )
    )

    membres = cursor.fetchall()

    conn.close()

    return render_template(
        "calendar.html",
        evenements=evenements,
        membres=membres
    )

# ==========================================================
# PROFIL
# ==========================================================

@app.route("/profile")
def profile():

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    admin_id = session.get(
        "admin_id"
    )

    admin = None

    if admin_id:

        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT *
            FROM admin
            WHERE id = ?
            """,
            (admin_id,)
        )

        admin = cursor.fetchone()

        conn.close()

    return render_template(
        "profile.html",
        admin=admin
    )

# ==========================================================
# PARAMÈTRES
# ==========================================================

@app.route("/settings")
def settings():

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    return render_template(
        "settings.html"
    )

# ==========================================================
# VÉRIFICATION ACCÈS CALENDRIER
# ==========================================================

def calendar_access_required():

    return (
        session.get("logged") is True
        and session.get("famille_id") is not None
    )

# ==========================================================
# CONNEXION GOOGLE CALENDAR
# ==========================================================

@app.route("/google/connect")
def google_connect():

    if not calendar_access_required():
        return redirect(url_for("home"))

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        flash("La connexion Google n'est pas encore configurée.")
        return redirect(url_for("calendar_import"))

    state = secrets.token_urlsafe(32)

    session["google_oauth_state"] = state

    auth_url = get_google_auth_url(
        GOOGLE_CLIENT_ID,
        GOOGLE_REDIRECT_URI,
        state
    )

    return redirect(auth_url)

# ==========================================================
# RETOUR APRÈS AUTORISATION GOOGLE
# ==========================================================

@app.route("/google/callback")
def google_callback():

    if not calendar_access_required():
        return redirect(url_for("home"))

    state = request.args.get("state", "")
    saved_state = session.pop("google_oauth_state", "")

    if not state or not saved_state:
        flash("Connexion Google invalide. Veuillez recommencer.")
        return redirect(url_for("calendar_import"))

    if not secrets.compare_digest(state, saved_state):
        flash("Vérification de sécurité Google échouée.")
        return redirect(url_for("calendar_import"))

    code = request.args.get("code")

    if not code:
        flash("L'autorisation Google a été annulée ou refusée.")
        return redirect(url_for("calendar_import"))

    token_data = get_google_token(
        GOOGLE_CLIENT_ID,
        GOOGLE_CLIENT_SECRET,
        GOOGLE_REDIRECT_URI,
        code
    )

    if not token_data or not token_data.get("access_token"):
        flash("Impossible de récupérer la connexion Google.")
        return redirect(url_for("calendar_import"))

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")

    profil = get_google_profile(access_token)

    google_email = profil.get("email", "") if profil else ""

    expires_at = datetime.now().timestamp() + int(
        token_data.get("expires_in", 3600)
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT refresh_token
        FROM google_connections
        WHERE famille_id = ?
        """,
        (session["famille_id"],)
    )

    ancienne_connexion = cursor.fetchone()

    if not refresh_token and ancienne_connexion:
        refresh_token = ancienne_connexion["refresh_token"]

    cursor.execute(
        """
        INSERT INTO google_connections
        (
            famille_id,
            google_email,
            access_token,
            refresh_token,
            expires_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(famille_id)
        DO UPDATE SET
            google_email = excluded.google_email,
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at
        """,
        (
            session["famille_id"],
            google_email,
            access_token,
            refresh_token,
            datetime.fromtimestamp(expires_at).isoformat()
        )
    )

    conn.commit()
    conn.close()

    flash("Compte Google connecté avec succès.")

    return redirect(url_for("calendar_import"))
    
# ==========================================================
# IMPORT CALENDRIER EXTERNE
# ==========================================================

@app.route("/calendar/import", methods=["GET", "POST"])
def calendar_import():

    if not calendar_access_required():
        return redirect(url_for("home"))

    if request.method == "POST":
        famille_id = session.get("famille_id")
        membre_id = session.get("membre_id")

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT google_email, access_token, refresh_token, expires_at
            FROM google_connections
            WHERE famille_id = ?
            """,
            (famille_id,)
        )
        connection = cursor.fetchone()
        conn.close()

        if not connection:
            flash("Connectez d'abord votre compte Google.")
            return redirect(url_for("calendar_import"))

        access_token = connection["access_token"]
        refresh_token = connection["refresh_token"]

        # Renouveler le token lorsqu'il expire dans moins de 2 minutes.
        try:
            expiration = datetime.fromisoformat(connection["expires_at"])
        except (TypeError, ValueError):
            expiration = datetime.min

        if expiration.timestamp() <= datetime.now().timestamp() + 120:
            if not refresh_token:
                flash("La connexion Google doit être renouvelée. Reconnectez votre compte.")
                return redirect(url_for("google_connect"))

            refreshed = refresh_google_token(
                GOOGLE_CLIENT_ID,
                GOOGLE_CLIENT_SECRET,
                refresh_token
            )
            if not refreshed or not refreshed.get("access_token"):
                flash("Impossible de renouveler la connexion Google. Reconnectez votre compte.")
                return redirect(url_for("google_connect"))

            access_token = refreshed["access_token"]
            new_expiry = datetime.now().timestamp() + int(
                refreshed.get("expires_in", 3600)
            )

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE google_connections
                SET access_token = ?, expires_at = ?
                WHERE famille_id = ?
                """,
                (
                    access_token,
                    datetime.fromtimestamp(new_expiry).isoformat(),
                    famille_id
                )
            )
            conn.commit()
            conn.close()

        try:
            calendars = get_google_calendars(access_token)
        except Exception as exc:
            flash(f"Impossible de récupérer les agendas Google : {exc}")
            return redirect(url_for("calendar_import"))

        if not calendars:
            flash("Aucun agenda Google accessible n'a été trouvé.")
            return redirect(url_for("calendar_import"))

        total_imported = 0
        total_skipped = 0
        errors = []

        for calendar in calendars:
            calendar_id = calendar.get("id")
            calendar_name = calendar.get("nom") or "Agenda Google"
            if not calendar_id:
                continue

            # Le suffixe d'identifiant évite les collisions entre agendas
            # qui portent le même nom dans Google.
            source_name = f"Google | {calendar_name} [{calendar_id}]"

            try:
                result = sync_google_calendar_by_id(
                    access_token=access_token,
                    calendar_id=calendar_id,
                    famille_id=famille_id,
                    membre_id=membre_id,
                    calendar_name=source_name
                )
                if result.get("error"):
                    errors.append(f"{calendar_name}: {result['error']}")
                else:
                    total_imported += result.get("imported", 0)
                    total_skipped += result.get("skipped", 0)
            except Exception as exc:
                errors.append(f"{calendar_name}: {exc}")

        if errors:
            flash(
                f"Import partiel : {total_imported} événement(s) ajouté(s), "
                f"{total_skipped} déjà présent(s). Erreurs : {' | '.join(errors)}"
            )
        else:
            flash(
                f"Import Google terminé : {total_imported} événement(s) ajouté(s), "
                f"{total_skipped} déjà présent(s), sur {len(calendars)} agenda(s)."
            )

        return redirect(url_for("calendar_import"))

    return redirect(url_for("calendar"))


# ==========================================================
# IMPORT APPLE CALENDAR (CALDAV ICLOUD)
# ==========================================================

@app.route("/apple/import", methods=["POST"])
def apple_import():

    if not calendar_access_required():
        return redirect(url_for("home"))

    apple_id = request.form.get("apple_id", "").strip()
    app_password = request.form.get("apple_app_password", "").strip()

    if not apple_id or not app_password:
        flash("Saisissez votre identifiant Apple et votre mot de passe spécifique d’application.")
        return redirect(url_for("calendar_import"))

    try:
        result = sync_apple_calendar(
            username=apple_id,
            password=app_password,
            famille_id=session.get("famille_id"),
            membre_id=session.get("membre_id"),
            calendar_name="Apple Calendar"
        )
        flash(
            f"Import Apple terminé : {result.get('imported', 0)} événement(s) ajouté(s), "
            f"{result.get('skipped', 0)} déjà présent(s)."
        )
    except Exception as exc:
        flash(f"Import Apple impossible : {exc}")

    return redirect(url_for("calendar_import"))

# ==========================================================
# REDIRECTION ADMIN APRÈS CONNEXION
# ==========================================================

@app.route("/admin")
def admin_dashboard():

    if not admin_required():

        return redirect(
            url_for("home")
        )

    return redirect(
        url_for("dashboard")
    )

# ==========================================================
# RÉCUPÉRER UN UTILISATEUR PAR SON ID
# ==========================================================

def get_user_by_id(user_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    conn.close()

    return user

# ==========================================================
# LANCEMENT FLASK
# ==========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )



