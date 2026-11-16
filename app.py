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

GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()


def get_google_redirect_uri():
    """Utilise l'URI Render configurée, ou construit l'URL publique courante."""
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    host = request.headers.get("X-Forwarded-Host", request.host).split(",")[0].strip()
    return f"{scheme}://{host}{url_for('google_callback')}"


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

    # Connexions Google séparées par utilisateur. L'ancienne table familiale
    # est conservée intacte pour ne perdre aucun token déjà enregistré.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS google_connections_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER NOT NULL,
            owner_type TEXT NOT NULL CHECK(owner_type IN ('admin', 'membre')),
            owner_id INTEGER NOT NULL,
            google_email TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(owner_type, owner_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_google_connections_family
        ON google_connections_users(famille_id)
    """)
    # Migration non destructive : l'ancienne connexion familiale devient
    # la connexion de l'administrateur de cette famille.
    cursor.execute("""
        INSERT OR IGNORE INTO google_connections_users
            (famille_id, owner_type, owner_id, google_email,
             access_token, refresh_token, expires_at, created_at)
        SELECT f.id, 'admin', f.admin_id, g.google_email,
               g.access_token, g.refresh_token, g.expires_at, g.created_at
        FROM google_connections g
        JOIN famille f ON f.id = g.famille_id
        WHERE f.admin_id IS NOT NULL
    """)
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
            "TEXT",
        "partage_agenda_pro": "INTEGER DEFAULT 0",
        "partage_agenda_perso": "INTEGER DEFAULT 0",
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
    "concerne_admin": "INTEGER DEFAULT 0",
    "type_agenda": "TEXT DEFAULT 'legacy'"
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
        
    # Consentement du conjoint ou ex-conjoint
    # pour partager ses agendas avec l'administratrice

    cursor.execute("PRAGMA table_info(membres)")

    colonnes_membres = {
        ligne[1]
        for ligne in cursor.fetchall()
    }

    
    colonnes_partage = {
        "partage_vers_admin_pro": "INTEGER DEFAULT 0",
        "partage_vers_admin_perso": "INTEGER DEFAULT 0",
        "acces_agenda_admin_pro": "INTEGER DEFAULT 0",
        "acces_agenda_admin_perso": "INTEGER DEFAULT 0"
    }

    for colonne, definition in colonnes_partage.items():
        if colonne not in colonnes_membres:
            cursor.execute(
                f"ALTER TABLE membres ADD COLUMN {colonne} {definition}"
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
                    type_evenement,
                    created_by_admin_id,
                    statut_validation,
                    valide_par_admin_id,
                    concerne_admin,
                    type_agenda
                )
                VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    famille_id,
                    None,
                    "Garde partagée",
                    "Garde chez l'autre personne",
                    date_str,
                    "",
                    "Agenda personnel",
                    1,
                    "garde_partagee",
                    get_family_admin(famille_id),
                    "accepte",
                    get_family_admin(famille_id),
                    1,
                    "admin_perso"
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

def create_member(
    famille_id,
    prenom,
    nom,
    email,
    peut_modifier=True,
    role="enfant",
    acces_agenda_admin=False,
    acces_agenda_admin_pro=False,
    acces_agenda_admin_perso=False
):

    conn = get_connection()
    cursor = conn.cursor()

    autorise = role == "conjoint"

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
            peut_modifier,
            acces_agenda_admin,
            acces_agenda_admin_pro,
            acces_agenda_admin_perso
        )
        VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            famille_id,
            prenom,
            nom,
            email,
            role,
            1 if peut_modifier else 0,
            1 if acces_agenda_admin and autorise else 0,
            1 if acces_agenda_admin_pro and autorise else 0,
            1 if acces_agenda_admin_perso and autorise else 0
        )
    )

    conn.commit()
    conn.close()

    return True


# ==========================================================
# ROUTE AJOUT D'UN MEMBRE
# ==========================================================

@app.route("/add_member", methods=["POST"])
def add_member():

    if not admin_required():
        return redirect(url_for("home"))

    famille_id = get_family_id(session.get("admin_id"))

    if famille_id is None:
        flash("Veuillez d'abord créer votre famille.")
        return redirect(url_for("dashboard"))

    if count_members(famille_id) >= 6:
        flash("Votre famille contient déjà 6 membres.")
        return redirect(url_for("members"))

    prenom = request.form.get("prenom", "").strip()
    nom = request.form.get("nom", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "enfant").strip().lower()

    peut_modifier = request.form.get("peut_modifier") == "oui"

    roles_autorises = ["enfant", "conjoint", "ex-conjoint"]

    if role not in roles_autorises:
        flash("Catégorie de membre invalide.")
        return redirect(url_for("members"))

    if not prenom or not nom:
        flash("Veuillez renseigner le prénom et le nom.")
        return redirect(url_for("members"))

    if email and member_email_exists(email):
        flash("Cette adresse e-mail est déjà utilisée.")
        return redirect(url_for("members"))

    
    acces_agenda_admin_pro = (
        request.form.get("partage_agenda_pro") == "oui"
        and role == "conjoint"
    )

    acces_agenda_admin_perso = (
        request.form.get("partage_agenda_perso") == "oui"
        and role == "conjoint"
    )

    create_member(
        famille_id=famille_id,
        prenom=prenom,
        nom=nom,
        email=email,
        peut_modifier=peut_modifier,
        role=role,
        acces_agenda_admin_pro=acces_agenda_admin_pro,
        acces_agenda_admin_perso=acces_agenda_admin_perso
    )


    flash(
        "Membre ajouté avec succès. "
        "Il pourra choisir son mot de passe lors de sa première connexion."
    )

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
        return redirect(url_for("home"))

    famille_id = get_family_id(session["admin_id"])

    if famille_id is None:
        return redirect(url_for("dashboard"))

    if not member_belongs_to_family(membre_id, famille_id):
        flash("Membre introuvable.")
        return redirect(url_for("members"))

    conn = get_connection()
    cursor = conn.cursor()

    # Supprimer les événements et la connexion Google propres au membre.
    cursor.execute(
        """
        DELETE FROM evenements
        WHERE famille_id = ?
        AND membre_id = ?
        """,
        (famille_id, membre_id)
    )
    cursor.execute(
        """
        DELETE FROM google_connections_users
        WHERE owner_type = 'membre' AND owner_id = ?
        """,
        (membre_id,)
    )
    cursor.execute(
        """
        DELETE FROM membres
        WHERE id = ?
        AND famille_id = ?
        """,
        (membre_id, famille_id)
    )

    conn.commit()
    conn.close()

    flash("Membre et rendez-vous associés supprimés.")
    return redirect(url_for("members"))
    
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

    autorise_admin_pro = (
        membre["role"] == "conjoint"
        and membre["acces_agenda_admin_pro"] == 1
    )

    autorise_admin_perso = (
        membre["role"] == "conjoint"
        and membre["acces_agenda_admin_perso"] == 1
    )

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE famille_id = ?
        AND statut_validation = 'accepte'
        ORDER BY date, heure
        """,
        (membre["famille_id"],)
    )

    tous_evenements = cursor.fetchall()
    evenements = []

    for evenement in tous_evenements:

        if evenement["concerne_admin"] == 1:

            if (
                evenement["type_agenda"] == "admin_pro"
                and autorise_admin_pro
            ) or (
                evenement["type_agenda"] == "admin_perso"
                and autorise_admin_perso
            ):
                evenements.append(evenement)

            continue

        # Un membre voit uniquement les événements de son propre agenda.
        if evenement["membre_id"] == membre_id:
            evenements.append(evenement)

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

@app.route("/add_event", methods=["POST"])
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
    couleur = request.form.get("couleur_evenement", "#4f8fc9").strip()
    couleurs = {"#4f8fc9", "#d94f9b", "#54a66b", "#e2a43b"}
    if couleur not in couleurs:
        couleur = "#4f8fc9"
    if not titre or not date:
        flash("Veuillez saisir un titre et une date.")
        return redirect(url_for("calendar"))

    rappel = 1 if request.form.get("rappel") == "oui" else 0
    est_admin = session.get("admin_logged") is True
    admin_id = session.get("admin_id")
    membre_connecte_id = session.get("membre_id")
    membre_form = request.form.get("membre_id", "").strip()
    agenda = request.form.get("type_agenda", "perso").strip().lower()
    if agenda not in ("pro", "perso"):
        flash("Agenda invalide.")
        return redirect(url_for("calendar"))

    conn = get_connection()
    cursor = conn.cursor()
    destinataire_id = None
    role_destinataire = None

    if est_admin:
        if membre_form:
            try:
                destinataire_id = int(membre_form)
            except ValueError:
                conn.close()
                flash("Membre destinataire invalide.")
                return redirect(url_for("calendar"))
            cursor.execute(
                "SELECT id, role FROM membres WHERE id = ? AND famille_id = ?",
                (destinataire_id, famille_id)
            )
            destinataire = cursor.fetchone()
            if destinataire is None:
                conn.close()
                flash("Ce membre n'appartient pas à votre famille.")
                return redirect(url_for("calendar"))
            role_destinataire = (destinataire["role"] or "enfant").lower()
            # L'administratrice peut uniquement déposer un rendez-vous
            # dans le PERSO du conjoint, de l'ex-conjoint ou d'un enfant.
            if agenda != "perso":
                conn.close()
                flash("L'administratrice peut ajouter des rendez-vous uniquement dans les agendas PERSO des membres.")
                return redirect(url_for("calendar"))
            proprietaire = "membre"
            type_agenda = "membre_perso"
            concerne_admin = 0
        else:
            proprietaire = "admin"
            type_agenda = f"admin_{agenda}"
            concerne_admin = 1
    else:
        cursor.execute(
            "SELECT id, role FROM membres WHERE id = ? AND famille_id = ?",
            (membre_connecte_id, famille_id)
        )
        auteur = cursor.fetchone()
        if auteur is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))
        role_auteur = (auteur["role"] or "enfant").lower()
        if role_auteur != "conjoint" and agenda == "pro":
            conn.close()
            flash("Seul le conjoint actuel dispose d'un agenda PRO.")
            return redirect(url_for("calendar"))
        destinataire_id = membre_connecte_id
        proprietaire = "membre"
        type_agenda = f"membre_{agenda}"
        concerne_admin = 0

    created_by_admin_id = admin_id if est_admin else None
    created_by_membre_id = None if est_admin else membre_connecte_id
    statut_validation = "accepte"

    cursor.execute(
        """
        INSERT INTO evenements (
            famille_id, membre_id, titre, description, date, heure,
            calendrier, rappel, type_evenement, created_by_admin_id,
            created_by_membre_id, statut_validation, valide_par_admin_id,
            valide_par_membre_id, concerne_admin, type_agenda
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            famille_id, destinataire_id, titre, description, date, heure,
            couleur, rappel, "normal", created_by_admin_id,
            created_by_membre_id, statut_validation,
            admin_id if est_admin else None,
            None if est_admin else membre_connecte_id,
            concerne_admin, type_agenda
        )
    )
    conn.commit()
    conn.close()
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
    admin_id = session.get("admin_id")
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
        autorise = evenement["created_by_admin_id"] == admin_id
    else:
        autorise = (
            evenement["membre_id"] == membre_id
            and evenement["created_by_membre_id"] == membre_id
        )

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
        SET titre = ?,
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
    admin_id = session.get("admin_id")
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

    if admin_logged:
        autorise = evenement["created_by_admin_id"] == admin_id
    else:
        autorise = (
            evenement["membre_id"] == membre_id
            and evenement["created_by_membre_id"] == membre_id
        )

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

    # L'administrateur peut valider toutes les propositions
    if admin_logged:

        autorise = True

    # Le membre concerné peut accepter sa propre proposition
    elif membre_id is not None:

        cursor.execute(
            """
            SELECT id
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        membre = cursor.fetchone()

        if (
            membre
            and evenement["concerne_admin"] == 0
            and evenement["membre_id"] == membre_id
        ):
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
            AND famille_id = ?
            AND statut_validation = 'en_attente'
            """,
            (session.get("admin_id"), event_id, famille_id)
        )

    else:

        cursor.execute(
            """
            UPDATE evenements
            SET statut_validation = 'accepte',
                valide_par_membre_id = ?
            WHERE id = ?
            AND famille_id = ?
            AND statut_validation = 'en_attente'
            """,
            (membre_id, event_id, famille_id)
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

    # L'administrateur peut refuser toutes les propositions
    if admin_logged:

        autorise = True

    # Le membre concerné peut refuser sa propre proposition
    elif membre_id is not None:

        cursor.execute(
            """
            SELECT id
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        membre = cursor.fetchone()

        if (
            membre
            and evenement["concerne_admin"] == 0
            and evenement["membre_id"] == membre_id
        ):
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
        AND famille_id = ?
        AND statut_validation = 'en_attente'
        """,
        (event_id, famille_id)
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
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    if famille_id is None:
        return redirect(url_for("dashboard"))

    est_admin = session.get("admin_logged") is True
    membre_id = session.get("membre_id")
    admin_id = session.get("admin_id")
    conn = get_connection()
    cursor = conn.cursor()
    membre = None

    if not est_admin:
        cursor.execute(
            "SELECT * FROM membres WHERE id = ? AND famille_id = ?",
            (membre_id, famille_id)
        )
        membre = cursor.fetchone()
        if membre is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))

    cursor.execute(
        """
        SELECT e.*, m.prenom, m.nom, m.role,
               m.partage_vers_admin_pro, m.partage_vers_admin_perso
        FROM evenements e
        LEFT JOIN membres m ON e.membre_id = m.id
        WHERE e.famille_id = ? AND e.statut_validation = 'accepte'
        ORDER BY e.date, e.heure
        """,
        (famille_id,)
    )
    tous_evenements = cursor.fetchall()
    evenements = []

    for ev in tous_evenements:
        ev_dict = dict(ev)
        owner_type = (ev_dict.get("type_agenda") or "legacy").lower()

        if est_admin:
            # Ses propres rendez-vous et uniquement ceux qu'elle a créés
            # dans les agendas des membres.
            if ev_dict.get("concerne_admin") == 1 and ev_dict.get("created_by_admin_id") == admin_id:
                evenements.append(ev_dict)
                continue

            if ev_dict.get("membre_id") is not None and ev_dict.get("created_by_admin_id") == admin_id:
                evenements.append(ev_dict)
                continue

            # Exception de confidentialité : seul le conjoint actuel peut
            # partager explicitement ses agendas avec l'administratrice.
            if (
                ev_dict.get("membre_id") is not None
                and ev_dict.get("role") == "conjoint"
                and (
                    (owner_type == "membre_pro" and ev_dict.get("partage_vers_admin_pro") == 1)
                    or (owner_type == "membre_perso" and ev_dict.get("partage_vers_admin_perso") == 1)
                )
            ):
                evenements.append(ev_dict)
            continue

        # Le membre voit ses propres rendez-vous.
        if ev_dict.get("membre_id") == membre_id:
            # Le membre voit aussi les rendez-vous que l'administratrice
            # a explicitement ajoutés dans son agenda.
            evenements.append(ev_dict)
            continue

        # Seul le conjoint actuel peut voir l'agenda de l'administratrice,
        # et uniquement si elle lui a accordé l'autorisation.
        if ev_dict.get("concerne_admin") == 1 and membre["role"] == "conjoint":
            if (
                owner_type == "admin_pro" and membre["acces_agenda_admin_pro"] == 1
            ) or (
                owner_type == "admin_perso" and membre["acces_agenda_admin_perso"] == 1
            ):
                evenements.append(ev_dict)

    # Propositions en attente : chaque membre voit les siennes;
    # l'administratrice voit les propositions qui lui sont adressées.
    if est_admin:
        cursor.execute(
            """
            SELECT e.*, m.prenom, m.nom
            FROM evenements e LEFT JOIN membres m ON e.membre_id = m.id
            WHERE e.famille_id = ? AND e.statut_validation = 'en_attente'
              AND e.concerne_admin = 1
            ORDER BY e.date, e.heure
            """,
            (famille_id,)
        )
    else:
        cursor.execute(
            """
            SELECT e.*, m.prenom, m.nom
            FROM evenements e LEFT JOIN membres m ON e.membre_id = m.id
            WHERE e.famille_id = ? AND e.statut_validation = 'en_attente'
              AND e.membre_id = ?
            ORDER BY e.date, e.heure
            """,
            (famille_id, membre_id)
        )
    propositions = cursor.fetchall()

    # L'administratrice peut créer un rendez-vous dans le PERSO de
    # chaque membre. Le serveur interdit toute destination PRO membre.
    if est_admin:
        cursor.execute(
            """
            SELECT id, prenom, nom, role
            FROM membres
            WHERE famille_id = ?
            ORDER BY prenom, nom
            """,
            (famille_id,)
        )
        membres = cursor.fetchall()
    else:
        membres = []

    conn.close()

    evenements_pro = []
    evenements_perso = []
    for ev in evenements:
        kind = (ev.get("type_agenda") or "legacy").lower()
        if kind.endswith("_pro"):
            evenements_pro.append(ev)
        else:
            evenements_perso.append(ev)

    # Un enfant et un ex-conjoint n'ont qu'un agenda PERSO.
    afficher_pro = est_admin or (membre and membre["role"] == "conjoint")

    # Connexion Google propre à chaque propriétaire (admin ou membre).
    owner_type, owner_id = get_calendar_owner()
    google_email = None
    google_calendars = []
    google_error = None
    google_conn = get_connection()
    google_cursor = google_conn.cursor()
    google_cursor.execute(
        """
        SELECT google_email, access_token, refresh_token, expires_at
        FROM google_connections_users
        WHERE famille_id = ? AND owner_type = ? AND owner_id = ?
        """,
        (famille_id, owner_type, owner_id)
    )
    google_connection = google_cursor.fetchone()
    google_conn.close()

    if google_connection:
        google_email = google_connection["google_email"]
        google_access_token = google_connection["access_token"]
        try:
            expiry = datetime.fromisoformat(google_connection["expires_at"])
        except (TypeError, ValueError):
            expiry = datetime.min

        if expiry.timestamp() <= datetime.now().timestamp() + 120:
            if google_connection["refresh_token"]:
                try:
                    refreshed = refresh_google_token(
                        GOOGLE_CLIENT_ID,
                        GOOGLE_CLIENT_SECRET,
                        google_connection["refresh_token"]
                    )
                    if refreshed and refreshed.get("access_token"):
                        google_access_token = refreshed["access_token"]
                        new_expiry = datetime.now().timestamp() + int(refreshed.get("expires_in", 3600))
                        update_conn = get_connection()
                        update_conn.execute(
                            """
                            UPDATE google_connections_users
                            SET access_token = ?, expires_at = ?
                            WHERE famille_id = ? AND owner_type = ? AND owner_id = ?
                            """,
                            (
                                google_access_token,
                                datetime.fromtimestamp(new_expiry).isoformat(),
                                famille_id, owner_type, owner_id
                            )
                        )
                        update_conn.commit()
                        update_conn.close()
                    else:
                        google_error = "La connexion Google doit être renouvelée."
                except Exception:
                    app.logger.exception("Renouvellement Google depuis le calendrier impossible")
                    google_error = "Impossible de renouveler Google. Reconnectez votre compte."
            else:
                google_error = "La connexion Google doit être renouvelée."

        if not google_error:
            try:
                google_calendars = get_google_calendars(google_access_token)
            except Exception:
                app.logger.exception("Lecture des calendriers Google impossible")
                google_error = "Google est connecté, mais la liste des agendas est inaccessible."

    return render_template(
        "calendar.html",
        evenements=evenements,
        evenements_pro=evenements_pro,
        evenements_perso=evenements_perso,
        membres=membres,
        propositions=propositions,
        est_admin=est_admin,
        afficher_pro=afficher_pro,
        google_email=google_email,
        google_calendars=google_calendars,
        google_error=google_error
    )


# ==========================================================
# PROFIL
# ==========================================================

@app.route("/profile")
def profile():

    if not session.get("logged"):
        return redirect(url_for("home"))

    admin = None
    membre = None

    conn = get_connection()
    cursor = conn.cursor()

    if session.get("admin_logged") is True:

        admin_id = session.get("admin_id")

        cursor.execute(
            """
            SELECT *
            FROM admin
            WHERE id = ?
            """,
            (admin_id,)
        )

        admin = cursor.fetchone()

        if admin is None:
            conn.close()
            session.clear()
            return redirect(url_for("login"))

    else:

        membre_id = session.get("membre_id")
        famille_id = session.get("famille_id")

        cursor.execute(
            """
            SELECT *
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        membre = cursor.fetchone()

        if membre is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))

    conn.close()

    return render_template(
        "profile.html",
        admin=admin,
        membre=membre
    )

# ==========================================================
# SUPPRESSION DU COMPTE ET DES DONNÉES ASSOCIÉES
# ==========================================================

@app.route("/delete_account", methods=["POST"])
def delete_account():

    if not session.get("logged"):
        return redirect(url_for("home"))

    confirmation = request.form.get("confirmation", "").strip()
    if confirmation != "SUPPRIMER":
        flash("Pour confirmer, saisissez exactement SUPPRIMER.")
        return redirect(url_for("profile"))

    conn = get_connection()
    cursor = conn.cursor()

    try:
        if session.get("admin_logged") is True:
            admin_id = session.get("admin_id")
            cursor.execute(
                "SELECT id FROM admin WHERE id = ?",
                (admin_id,)
            )
            if cursor.fetchone() is None:
                conn.rollback()
                session.clear()
                return redirect(url_for("login"))

            cursor.execute(
                "SELECT id FROM famille WHERE admin_id = ?",
                (admin_id,)
            )
            familles = [row["id"] for row in cursor.fetchall()]

            for famille_id in familles:
                # Tous les événements de la famille appartiennent aux données
                # supprimées avec le compte administrateur.
                cursor.execute(
                    "DELETE FROM evenements WHERE famille_id = ?",
                    (famille_id,)
                )
                cursor.execute(
                    "DELETE FROM google_connections_users WHERE famille_id = ?",
                    (famille_id,)
                )
                cursor.execute(
                    "DELETE FROM google_connections WHERE famille_id = ?",
                    (famille_id,)
                )
                cursor.execute(
                    "DELETE FROM membres WHERE famille_id = ?",
                    (famille_id,)
                )

            cursor.execute(
                "DELETE FROM famille WHERE admin_id = ?",
                (admin_id,)
            )
            cursor.execute(
                "DELETE FROM admin WHERE id = ?",
                (admin_id,)
            )
            message = "Le compte administrateur, la famille, les membres et les données associées ont été supprimés."

        else:
            membre_id = session.get("membre_id")
            famille_id = session.get("famille_id")

            cursor.execute(
                """
                SELECT id FROM membres
                WHERE id = ? AND famille_id = ?
                """,
                (membre_id, famille_id)
            )
            if cursor.fetchone() is None:
                conn.rollback()
                session.clear()
                return redirect(url_for("home"))

            # Supprime les événements liés à ce membre, y compris ceux
            # qu'il a créés ou validés pour un autre agenda.
            colonnes = {
                row["name"] for row in
                cursor.execute("PRAGMA table_info(evenements)").fetchall()
            }
            conditions = ["membre_id = ?"]
            params = [membre_id]
            for colonne in ("created_by_membre_id", "valide_par_membre_id"):
                if colonne in colonnes:
                    conditions.append(f"{colonne} = ?")
                    params.append(membre_id)

            cursor.execute(
                "DELETE FROM evenements WHERE " + " OR ".join(conditions),
                tuple(params)
            )
            cursor.execute(
                """
                DELETE FROM google_connections_users
                WHERE owner_type = 'membre' AND owner_id = ?
                """,
                (membre_id,)
            )
            cursor.execute(
                "DELETE FROM membres WHERE id = ? AND famille_id = ?",
                (membre_id, famille_id)
            )
            message = "Votre compte, vos événements et votre connexion Google ont été supprimés."

        conn.commit()
        session.clear()
        flash(message)
        return redirect(url_for("home"))

    except Exception:
        conn.rollback()
        app.logger.exception("Erreur lors de la suppression d'un compte")
        flash("La suppression a échoué. Aucune modification n'a été validée.")
        return redirect(url_for("profile"))

    finally:
        conn.close()

# ==========================================================
# MODIFICATION DES AUTORISATIONS DE PARTAGE DES AGENDAS
# ==========================================================

@app.route("/update_agenda_sharing", methods=["POST"])
def update_agenda_sharing():

    if not session.get("logged") or session.get("admin_logged"):
        return redirect(url_for("home"))

    membre_id = session.get("membre_id")

    partage_pro = request.form.get("partage_vers_admin_pro") == "oui"
    partage_perso = request.form.get("partage_vers_admin_perso") == "oui"

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT role
        FROM membres
        WHERE id = ?
        AND famille_id = ?
        """,
        (membre_id, session.get("famille_id"))
    )

    membre = cursor.fetchone()

    if membre is None or membre["role"] != "conjoint":
        conn.close()
        return redirect(url_for("profile"))

    cursor.execute(
        """
        UPDATE membres
        SET partage_vers_admin_pro = ?,
            partage_vers_admin_perso = ?
        WHERE id = ?
        """,
        (
            1 if partage_pro else 0,
            1 if partage_perso else 0,
            membre_id
        )
    )

    conn.commit()
    conn.close()

    flash("Vos autorisations de partage ont été mises à jour.")

    return redirect(url_for("profile"))



# ==========================================================
# MODIFICATION DU PROFIL ADMINISTRATEUR
# ==========================================================


@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():

    if not admin_required():
        return redirect(url_for("home"))

    admin_id = session.get("admin_id")

    conn = get_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        prenom = request.form.get("prenom", "").strip()
        nom = request.form.get("nom", "").strip()
        conjoint = request.form.get("conjoint", "non")
        garde_partagee = request.form.get("garde_partagee", "non")
        frequence_garde = request.form.get("frequence_garde", "").strip()
        prochaine_date_garde = request.form.get("prochaine_date_garde", "").strip()

        if not prenom or not nom:
            conn.close()
            flash("Veuillez remplir le prénom et le nom.")
            return redirect(url_for("edit_profile"))

        if garde_partagee != "oui":
            frequence_garde = ""
            prochaine_date_garde = ""

        cursor.execute(
            """
            UPDATE admin
            SET prenom = ?,
                nom = ?,
                conjoint = ?,
                garde_partagee = ?,
                frequence_garde = ?,
                prochaine_date_garde = ?
            WHERE id = ?
            """,
            (
                prenom,
                nom,
                1 if conjoint == "oui" else 0,
                1 if garde_partagee == "oui" else 0,
                frequence_garde,
                prochaine_date_garde,
                admin_id
            )
        )

        conn.commit()
        conn.close()

        session["user_name"] = f"{prenom} {nom}"

        flash("Votre profil a été modifié.")
        return redirect(url_for("profile"))

    cursor.execute(
        "SELECT * FROM admin WHERE id = ?",
        (admin_id,)
    )

    admin = cursor.fetchone()
    conn.close()

    if admin is None:
        session.clear()
        return redirect(url_for("login"))

    return render_template("edit_profile.html", admin=admin)


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
# IDENTITÉ DU PROPRIÉTAIRE DE LA CONNEXION GOOGLE
# ==========================================================

def get_calendar_owner():
    if session.get("admin_logged") is True:
        return "admin", session.get("admin_id")
    return "membre", session.get("membre_id")


# ==========================================================
# VÉRIFICATION ACCÈS CALENDRIER
# ==========================================================

def calendar_access_required():

    if session.get("logged") is not True:
        return False

    famille_id = session.get("famille_id")

    if famille_id is None:
        return False

    conn = get_connection()
    cursor = conn.cursor()

    # Vérification de l'administrateur
    if session.get("admin_logged") is True:

        admin_id = session.get("admin_id")

        cursor.execute(
            """
            SELECT id
            FROM famille
            WHERE id = ?
            AND admin_id = ?
            """,
            (famille_id, admin_id)
        )

        utilisateur = cursor.fetchone()

    else:

        # Vérification de l'appartenance du membre à la famille
        membre_id = session.get("membre_id")

        cursor.execute(
            """
            SELECT id
            FROM membres
            WHERE id = ?
            AND famille_id = ?
            """,
            (membre_id, famille_id)
        )

        utilisateur = cursor.fetchone()

    conn.close()

    return utilisateur is not None

# ==========================================================
# CONNEXION GOOGLE CALENDAR
# ==========================================================

@app.route("/google/connect", methods=["GET", "POST"])
def google_connect():

    if not calendar_access_required():
        return redirect(url_for("home"))

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        flash("La connexion Google n'est pas configurée dans Render.")
        return redirect(url_for("calendar_import"))

    google_email_hint = ""
    if request.method == "POST":
        google_email_hint = request.form.get("google_email", "").strip().lower()
        if not google_email_hint or "@" not in google_email_hint:
            flash("Saisissez une adresse e-mail Google valide.")
            return redirect(url_for("calendar"))

    redirect_uri = get_google_redirect_uri()
    owner_type, owner_id = get_calendar_owner()
    if not owner_id:
        flash("Impossible d'identifier le propriétaire de cet agenda.")
        return redirect(url_for("calendar_import"))

    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    session["google_oauth_owner_type"] = owner_type
    session["google_oauth_owner_id"] = int(owner_id)
    session["google_oauth_family_id"] = int(session["famille_id"])
    session["google_oauth_redirect_uri"] = redirect_uri

    try:
        auth_url = get_google_auth_url(
            GOOGLE_CLIENT_ID,
            redirect_uri,
            state,
            login_hint=google_email_hint or None
        )
    except Exception:
        app.logger.exception("Erreur création URL OAuth Google")
        flash("Impossible de démarrer la connexion Google.")
        return redirect(url_for("calendar_import"))

    return redirect(auth_url)


# ==========================================================
# RETOUR APRÈS AUTORISATION GOOGLE
# ==========================================================

@app.route("/google/callback")
def google_callback():

    # Google peut retourner une erreur avant même de fournir un code.
    google_error = request.args.get("error")
    if google_error:
        session.pop("google_oauth_state", None)
        flash(f"Google a refusé la connexion : {google_error}")
        return redirect(url_for("calendar_import"))

    if not calendar_access_required():
        return redirect(url_for("home"))

    state = request.args.get("state", "")
    saved_state = session.pop("google_oauth_state", "")

    if not state or not saved_state or not secrets.compare_digest(state, saved_state):
        flash("Vérification de sécurité Google échouée. Recommencez la connexion.")
        return redirect(url_for("calendar_import"))

    owner_type = session.pop("google_oauth_owner_type", None)
    owner_id = session.pop("google_oauth_owner_id", None)
    oauth_family_id = session.pop("google_oauth_family_id", None)
    redirect_uri = session.pop("google_oauth_redirect_uri", None)

    current_owner_type, current_owner_id = get_calendar_owner()
    if (
        owner_type != current_owner_type
        or not owner_id
        or int(owner_id) != int(current_owner_id or 0)
        or int(oauth_family_id or 0) != int(session.get("famille_id") or 0)
    ):
        flash("La session a changé pendant la connexion Google. Recommencez.")
        return redirect(url_for("calendar_import"))

    code = request.args.get("code")
    if not code:
        flash("L'autorisation Google n'a pas fourni de code.")
        return redirect(url_for("calendar_import"))

    redirect_uri = redirect_uri or get_google_redirect_uri()

    try:
        token_data = get_google_token(
            GOOGLE_CLIENT_ID,
            GOOGLE_CLIENT_SECRET,
            redirect_uri,
            code
        )
    except Exception:
        app.logger.exception("Échec échange du code OAuth Google")
        flash("Erreur réseau lors de la récupération du jeton Google.")
        return redirect(url_for("calendar_import"))

    if not token_data or not token_data.get("access_token"):
        detail = (token_data or {}).get("error_description") or (token_data or {}).get("error")
        app.logger.error("Google OAuth token error: %s", detail or "réponse vide")
        flash("Google n'a pas délivré de jeton. Vérifiez l'URI de redirection autorisée dans Google Cloud et dans Render.")
        return redirect(url_for("calendar_import"))

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")

    profil = get_google_profile(access_token)
    google_email = profil.get("email", "") if profil else ""

    expires_at = datetime.now().timestamp() + int(token_data.get("expires_in", 3600))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT refresh_token
        FROM google_connections_users
        WHERE owner_type = ? AND owner_id = ?
        """,
        (owner_type, owner_id)
    )
    ancienne_connexion = cursor.fetchone()

    if not refresh_token and ancienne_connexion:
        refresh_token = ancienne_connexion["refresh_token"]

    cursor.execute(
        """
        INSERT INTO google_connections_users
            (famille_id, owner_type, owner_id, google_email,
             access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_type, owner_id) DO UPDATE SET
            famille_id = excluded.famille_id,
            google_email = excluded.google_email,
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            expires_at = excluded.expires_at
        """,
        (
            session["famille_id"], owner_type, owner_id, google_email,
            access_token, refresh_token,
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
        owner_type, owner_id = get_calendar_owner()

        type_choisi = request.form.get("type_agenda", "perso").strip().lower()
        if type_choisi not in ("pro", "perso"):
            flash("Choisissez un agenda PRO ou PERSO.")
            return redirect(url_for("calendar"))
        if owner_type == "membre":
            current_member = get_member_by_id(owner_id)
            if not current_member or current_member["role"] != "conjoint":
                type_choisi = "perso"
        type_agenda = f"{owner_type}_{type_choisi}"

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT google_email, access_token, refresh_token, expires_at
            FROM google_connections_users
            WHERE famille_id = ? AND owner_type = ? AND owner_id = ?
            """,
            (famille_id, owner_type, owner_id)
        )
        connection = cursor.fetchone()
        conn.close()

        if not connection:
            flash("Connectez d'abord votre propre compte Google.")
            return redirect(url_for("google_connect"))

        access_token = connection["access_token"]
        refresh_token = connection["refresh_token"]

        try:
            expiration = datetime.fromisoformat(connection["expires_at"])
        except (TypeError, ValueError):
            expiration = datetime.min

        if expiration.timestamp() <= datetime.now().timestamp() + 120:
            if not refresh_token:
                flash("La connexion Google doit être renouvelée. Reconnectez votre compte.")
                return redirect(url_for("google_connect"))

            try:
                refreshed = refresh_google_token(
                    GOOGLE_CLIENT_ID,
                    GOOGLE_CLIENT_SECRET,
                    refresh_token
                )
            except Exception:
                app.logger.exception("Erreur renouvellement token Google")
                refreshed = None

            if not refreshed or not refreshed.get("access_token"):
                flash("Impossible de renouveler la connexion Google. Reconnectez votre compte.")
                return redirect(url_for("google_connect"))

            access_token = refreshed["access_token"]
            new_expiry = datetime.now().timestamp() + int(refreshed.get("expires_in", 3600))

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE google_connections_users
                SET access_token = ?, expires_at = ?
                WHERE owner_type = ? AND owner_id = ? AND famille_id = ?
                """,
                (
                    access_token,
                    datetime.fromtimestamp(new_expiry).isoformat(),
                    owner_type, owner_id, famille_id
                )
            )
            conn.commit()
            conn.close()

        try:
            calendars = get_google_calendars(access_token)
        except Exception as exc:
            app.logger.exception("Impossible de récupérer les agendas Google")
            flash(f"Impossible de récupérer les agendas Google : {exc}")
            return redirect(url_for("calendar"))

        if not calendars:
            flash("Aucun agenda Google accessible n'a été trouvé.")
            return redirect(url_for("calendar"))

        # Deux agendas maximum par import. Si le formulaire fournit des IDs,
        # seuls ceux sélectionnés sont importés ; sinon les deux premiers.
        selected_ids = (
            request.form.getlist("calendar_ids")
            or request.form.getlist("calendars")
        )
        if selected_ids:
            calendars = [c for c in calendars if c.get("id") in selected_ids]
        calendars = calendars[:2]

        if not calendars:
            flash("Sélectionnez au moins un agenda Google.")
            return redirect(url_for("calendar"))

        total_imported = 0
        total_skipped = 0
        errors = []

        for calendar_item in calendars:
            calendar_id = calendar_item.get("id")
            calendar_name = calendar_item.get("nom") or "Agenda Google"
            if not calendar_id:
                continue

            source_name = f"Google | {calendar_name} [{calendar_id}]"
            try:
                result = sync_google_calendar_by_id(
                    access_token=access_token,
                    calendar_id=calendar_id,
                    famille_id=famille_id,
                    membre_id=owner_id if owner_type == "membre" else None,
                    calendar_name=source_name,
                    type_agenda=type_agenda,
                    created_by_admin_id=owner_id if owner_type == "admin" else None,
                    created_by_membre_id=owner_id if owner_type == "membre" else None
                )
                if result.get("error"):
                    errors.append(f"{calendar_name}: {result['error']}")
                else:
                    total_imported += result.get("imported", 0)
                    total_skipped += result.get("skipped", 0)
            except Exception as exc:
                app.logger.exception("Erreur import agenda Google %s", calendar_name)
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

        return redirect(url_for("calendar"))

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

    type_choisi = request.form.get("type_agenda", "perso").strip().lower()
    if type_choisi not in ("pro", "perso"):
        flash("Choisissez un agenda PRO ou PERSO.")
        return redirect(url_for("calendar"))

    owner_type, owner_id = get_calendar_owner()
    if owner_type == "membre":
        current_member = get_member_by_id(owner_id)
        if not current_member or current_member["role"] != "conjoint":
            type_choisi = "perso"
    type_agenda = f"{owner_type}_{type_choisi}"

    try:
        result = sync_apple_calendar(
            username=apple_id,
            password=app_password,
            famille_id=session.get("famille_id"),
            membre_id=owner_id if owner_type == "membre" else None,
            calendar_name="Apple Calendar",
            type_agenda=type_agenda,
            created_by_admin_id=owner_id if owner_type == "admin" else None,
            created_by_membre_id=owner_id if owner_type == "membre" else None
        )
        flash(
            f"Import Apple terminé : {result.get('imported', 0)} événement(s) ajouté(s), "
            f"{result.get('skipped', 0)} déjà présent(s)."
        )
    except Exception as exc:
        app.logger.exception("Échec import Apple Calendar")
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



