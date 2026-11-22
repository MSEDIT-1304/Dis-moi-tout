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
    jsonify,
    send_file
)
    

import os
import json
import sqlite3
import pandas as pd
import requests
import bcrypt
import secrets
import base64
import tempfile
import uuid
import io
from werkzeug.utils import secure_filename

from datetime import datetime, timedelta
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
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024  # formulaire + pièce jointe (10 Mo max)

app.secret_key = SECRET_KEY

# Connexion persistante : cookie sécurisé, valable 365 jours.
# La durée est renouvelée à chaque utilisation d'une session connectée.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_PATH"] = "/"


# Service worker à la racine : nécessaire pour couvrir toute l’application.
@app.route("/service-worker.js")
def service_worker_file():
    response = app.send_static_file("service-worker.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.route("/app-version", methods=["GET"])
def app_version():
    """Retourne l'identifiant du déploiement Render pour le contrôle quotidien."""
    version = os.environ.get("RENDER_GIT_COMMIT", "").strip()
    if not version:
        return jsonify({"error": "Version du déploiement indisponible"}), 503
    response = jsonify({"version": version})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response

@app.before_request
def keep_authenticated_session_permanent():
    # Garantit le caractère permanent à chaque requête authentifiée.
    if session.get("logged"):
        session.permanent = True

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
# PAGES PUBLIQUES — INFORMATIONS LÉGALES
# ==========================================================

@app.route("/politique-confidentialite")
def politique_confidentialite():
    return render_template("politique_confidentialite.html")


@app.route("/conditions-utilisation")
def conditions_utilisation():
    return render_template("conditions_utilisation.html")


# ==========================================================
# ACCUEIL
# ==========================================================


@app.route("/")
def home():
    # Un utilisateur déjà connecté retrouve directement son espace.
    if session.get("logged"):
        if session.get("admin_logged") is True and not session.get("famille_id"):
            return redirect(url_for("dashboard"))
        return redirect(url_for("calendar"))

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
    session.permanent = True

    session["admin_logged"] = True

    session["admin_id"] = admin["id"]
    session["email"] = email

    session["user_name"] = (
        f"{admin['prenom']} {admin['nom']}"
    )

    famille_id = get_family_id(admin["id"])
    if famille_id is not None:
        session["famille_id"] = famille_id
        return redirect(url_for("calendar"))

    # Première connexion : l'administrateur doit encore créer sa famille.
    return redirect(url_for("dashboard"))# ==========================================================
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
            UNIQUE(owner_type, owner_id, google_email COLLATE NOCASE)
        )
    """)
    # Migration non destructive : autoriser plusieurs comptes Google par propriétaire.
    index_rows = cursor.execute("PRAGMA index_list(google_connections_users)").fetchall()
    old_constraint = False
    for index_row in index_rows:
        if index_row[2]:
            index_name = index_row[1].replace('"', '""')
            index_columns = [
                info[2] for info in cursor.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            ]
            if index_columns == ["owner_type", "owner_id"]:
                old_constraint = True
                break

    if old_constraint:
        cursor.execute("ALTER TABLE google_connections_users RENAME TO google_connections_users_old")
        cursor.execute("""
            CREATE TABLE google_connections_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                famille_id INTEGER NOT NULL,
                owner_type TEXT NOT NULL CHECK(owner_type IN ('admin', 'membre')),
                owner_id INTEGER NOT NULL,
                google_email TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_type, owner_id, google_email COLLATE NOCASE)
            )
        """)
        cursor.execute("""
            INSERT INTO google_connections_users
                (id, famille_id, owner_type, owner_id, google_email,
                 access_token, refresh_token, expires_at, created_at)
            SELECT id, famille_id, owner_type, owner_id, google_email,
                   access_token, refresh_token, expires_at, created_at
            FROM google_connections_users_old
        """)
        cursor.execute("DROP TABLE google_connections_users_old")

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
    "type_agenda": "TEXT DEFAULT 'legacy'",
    "rappel_delai_minutes": "INTEGER DEFAULT 60"
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

    
    if "pseudo" not in colonnes_membres:
        cursor.execute("ALTER TABLE membres ADD COLUMN pseudo TEXT")
        colonnes_membres.add("pseudo")

    # Photo facultative ou avatar choisi par le membre (migration sans perte).
    if "photo_data" not in colonnes_membres:
        cursor.execute("ALTER TABLE membres ADD COLUMN photo_data BLOB")
        colonnes_membres.add("photo_data")
    if "photo_mime" not in colonnes_membres:
        cursor.execute("ALTER TABLE membres ADD COLUMN photo_mime TEXT")
        colonnes_membres.add("photo_mime")
    if "avatar" not in colonnes_membres:
        cursor.execute("ALTER TABLE membres ADD COLUMN avatar TEXT")
        colonnes_membres.add("avatar")

    colonnes_partage = {
        "partage_vers_admin_pro": "INTEGER DEFAULT 0",
        "partage_vers_admin_perso": "INTEGER DEFAULT 0",
        "acces_agenda_admin_pro": "INTEGER DEFAULT 0",
        "acces_agenda_admin_perso": "INTEGER DEFAULT 0",
        "garde_partagee": "INTEGER DEFAULT 0",
        "frequence_garde": "TEXT",
        "prochaine_date_garde": "TEXT"
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

    # ======================================================
    # MESSAGERIE INTERNE
    # ======================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_participants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            user_type TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            last_read_at TIMESTAMP,
            UNIQUE(conversation_id, user_type, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_type TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            contenu TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
    """)
    # Masquage individuel : on conserve participants, messages et pièces jointes.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_hidden (
            conversation_id INTEGER NOT NULL,
            user_type TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            hidden_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (conversation_id, user_type, user_id),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_conversation_hidden_user
        ON conversation_hidden(user_type, user_id, conversation_id)
    """)
    # Surnoms privés : chaque membre choisit le nom qu'il donne aux autres.
    # Ces choix ne modifient jamais le pseudo attribué par l'administratrice.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messagerie_surnoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            famille_id INTEGER NOT NULL,
            owner_type TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            surnom TEXT NOT NULL,
            UNIQUE(famille_id, owner_type, owner_id, target_type, target_id)
        )
    """)
    # Champs facultatifs pour les pièces jointes de la messagerie (migration sans perte).
    message_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()
    }
    if "fichier_nom" not in message_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN fichier_nom TEXT")
    if "fichier_stockage" not in message_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN fichier_stockage TEXT")
    if "fichier_data" not in message_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN fichier_data BLOB")
    if "reply_to_message_id" not in message_columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN reply_to_message_id INTEGER")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_participants_user ON conversation_participants(user_type, user_id)")

    # Appareils autorisés à recevoir les notifications Web Push.
    # Une ligne par navigateur/appareil ; les conversations restent privées.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL UNIQUE,
            subscription_json TEXT NOT NULL,
            user_type TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            famille_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user
        ON push_subscriptions(user_type, user_id, famille_id)
    """)

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
    frequence,
    membre_id=None
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
            AND membre_id IS ?
            """,
            (
                famille_id,
                date_str,
                "garde_partagee",
                membre_id
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
                    membre_id,
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
# PHOTO / AVATAR DES MEMBRES
# ==========================================================

def read_member_photo_upload(upload):
    """Retourne (octets, mime) pour une image sûre et légère, ou (None, None)."""
    if not upload or not upload.filename:
        return None, None
    mime = (upload.mimetype or "").lower()
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if mime not in allowed:
        raise ValueError("Choisissez une image JPG, PNG, WEBP ou GIF.")
    data = upload.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError("La photo doit faire 5 Mo maximum.")
    # Vérification légère des signatures, sans dépendance supplémentaire.
    signatures = {
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    }
    if not signatures.get(mime, False):
        raise ValueError("Le fichier choisi n'est pas une image valide.")
    return data, mime


@app.route("/membre/photo/<int:membre_id>")
def membre_photo(membre_id):
    identity = current_messaging_identity()
    if identity is None:
        if not admin_required():
            return "Accès refusé", 403
        famille_id = get_family_id(session.get("admin_id"))
    else:
        _, _, famille_id = identity
    conn = get_connection()
    row = conn.execute(
        "SELECT photo_data, photo_mime FROM membres WHERE id=? AND famille_id=?",
        (membre_id, famille_id)
    ).fetchone()
    conn.close()
    if not row or not row["photo_data"]:
        return "Photo introuvable", 404
    return send_file(io.BytesIO(row["photo_data"]), mimetype=row["photo_mime"] or "image/jpeg", max_age=300)


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

    if count_members(famille_id) >= 8:
        flash("Votre famille contient déjà 8 membres.")
        return redirect(url_for("members"))

    prenom = request.form.get("prenom", "").strip()
    nom = request.form.get("nom", "").strip()
    pseudo = request.form.get("pseudo", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "enfant").strip().lower()

    peut_modifier = request.form.get("peut_modifier") == "oui"

    roles_autorises = ["enfant", "conjoint", "ex-conjoint", "grands-parents-autre"]

    if role not in roles_autorises:
        flash("Catégorie de membre invalide.")
        return redirect(url_for("members"))

    if not prenom or not nom:
        flash("Veuillez renseigner le prénom et le nom.")
        return redirect(url_for("members"))

    if email and member_email_exists(email):
        flash("Cette adresse e-mail est déjà utilisée.")
        return redirect(url_for("members"))

    try:
        photo_data, photo_mime = read_member_photo_upload(request.files.get("photo"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("members"))
    avatar = request.form.get("avatar", "").strip()
    avatars_autorises = {"", "😊", "👩", "👨", "👵", "👴", "🧒", "👶", "🐱", "🐶", "🌸", "⭐"}
    if avatar not in avatars_autorises:
        avatar = ""

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

    conn_pseudo = get_connection()
    nouveau_membre = conn_pseudo.execute(
        "SELECT id FROM membres WHERE famille_id=? AND email=? ORDER BY id DESC LIMIT 1",
        (famille_id, email)
    ).fetchone() if email else conn_pseudo.execute(
        "SELECT id FROM membres WHERE famille_id=? AND prenom=? AND nom=? ORDER BY id DESC LIMIT 1",
        (famille_id, prenom, nom)
    ).fetchone()
    if nouveau_membre:
        conn_pseudo.execute(
            "UPDATE membres SET pseudo=?, photo_data=?, photo_mime=?, avatar=? WHERE id=? AND famille_id=?",
            (pseudo or None, photo_data, photo_mime, avatar or None, nouveau_membre["id"], famille_id)
        )
        conn_pseudo.commit()
    conn_pseudo.close()

    flash(
        "Membre ajouté avec succès. "
        "Il pourra choisir son mot de passe lors de sa première connexion."
    )

    return redirect(url_for("members"))

# ==========================================================
# MODIFIER UN MEMBRE — ADMINISTRATEUR UNIQUEMENT
# ==========================================================

@app.route("/edit_member/<int:membre_id>", methods=["POST"])
def edit_member_route(membre_id):

    if not admin_required():
        return redirect(url_for("home"))

    famille_id = get_family_id(session.get("admin_id"))
    if famille_id is None or not member_belongs_to_family(membre_id, famille_id):
        flash("Membre introuvable.")
        return redirect(url_for("members"))

    prenom = request.form.get("prenom", "").strip()
    nom = request.form.get("nom", "").strip()
    pseudo = request.form.get("pseudo", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "enfant").strip().lower()
    peut_modifier = 1 if request.form.get("peut_modifier") == "oui" else 0
    # Même règle que lors de l'ajout : les accès PRO/PERSO
    # ne sont accordés qu'à un membre de catégorie « conjoint ».
    acces_agenda_admin_pro = (
        1 if request.form.get("partage_agenda_pro") == "oui" and role == "conjoint" else 0
    )
    acces_agenda_admin_perso = (
        1 if request.form.get("partage_agenda_perso") == "oui" and role == "conjoint" else 0
    )

    if not prenom or not nom:
        flash("Veuillez renseigner le prénom et le nom.")
        return redirect(url_for("members"))

    if role not in ("enfant", "conjoint", "ex-conjoint", "grands-parents-autre"):
        flash("Catégorie de membre invalide.")
        return redirect(url_for("members"))

    try:
        photo_data, photo_mime = read_member_photo_upload(request.files.get("photo"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("members"))
    avatar = request.form.get("avatar", "").strip()
    avatars_autorises = {"", "😊", "👩", "👨", "👵", "👴", "🧒", "👶", "🐱", "🐶", "🌸", "⭐"}
    if avatar not in avatars_autorises:
        avatar = ""
    supprimer_photo = request.form.get("supprimer_photo") == "oui"

    conn = get_connection()
    cursor = conn.cursor()
    if email:
        cursor.execute(
            "SELECT id FROM membres WHERE lower(email) = lower(?) AND id != ?",
            (email, membre_id)
        )
        email_existant = cursor.fetchone()
        cursor.execute(
            "SELECT id FROM admin WHERE lower(email) = lower(?)",
            (email,)
        )
        email_admin = cursor.fetchone()
        if email_existant or email_admin:
            conn.close()
            flash("Cette adresse e-mail est déjà utilisée.")
            return redirect(url_for("members"))

    garde_partagee = request.form.get("garde_partagee") == "oui" and role == "enfant"
    frequence_garde = request.form.get("frequence_garde", "").strip() if garde_partagee else ""
    prochaine_date_garde = request.form.get("prochaine_date_garde", "").strip() if garde_partagee else ""

    frequences_autorisees = ("1 semaine sur deux", "Tous les quinze jours", "Tous les mois")
    if garde_partagee and frequence_garde not in frequences_autorisees:
        conn.close()
        flash("Veuillez sélectionner la fréquence de la garde partagée.")
        return redirect(url_for("members"))
    if garde_partagee and not prochaine_date_garde:
        conn.close()
        flash("Veuillez indiquer la prochaine date de garde.")
        return redirect(url_for("members"))
    if garde_partagee:
        try:
            datetime.strptime(prochaine_date_garde, "%Y-%m-%d")
        except ValueError:
            conn.close()
            flash("La date de la prochaine garde est invalide.")
            return redirect(url_for("members"))

    cursor.execute(
        """
        UPDATE membres
        SET prenom = ?, nom = ?, pseudo = ?, email = ?, role = ?, peut_modifier = ?,
            acces_agenda_admin_pro = ?, acces_agenda_admin_perso = ?,
            garde_partagee = ?, frequence_garde = ?, prochaine_date_garde = ?,
            photo_data = CASE WHEN ? THEN NULL WHEN ? IS NOT NULL THEN ? ELSE photo_data END,
            photo_mime = CASE WHEN ? THEN NULL WHEN ? IS NOT NULL THEN ? ELSE photo_mime END,
            avatar = ?
        WHERE id = ? AND famille_id = ?
        """,
        (
            prenom, nom, pseudo or None, email, role, peut_modifier,
            acces_agenda_admin_pro, acces_agenda_admin_perso,
            1 if garde_partagee else 0, frequence_garde, prochaine_date_garde,
            1 if supprimer_photo else 0, photo_data, photo_data,
            1 if supprimer_photo else 0, photo_mime, photo_mime,
            avatar or None,
            membre_id, famille_id
        )
    )
    modifie = cursor.rowcount > 0

    if modifie:
        # Conserver toutes les gardes passées. Seules les gardes futures
        # générées pour cet enfant sont remplacées.
        cursor.execute(
            """
            DELETE FROM evenements
            WHERE famille_id = ? AND membre_id = ?
              AND type_evenement = 'garde_partagee'
              AND date >= date('now', 'localtime')
            """,
            (famille_id, membre_id)
        )
        conn.commit()

    conn.close()

    if modifie and garde_partagee:
        create_shared_custody_events(
            famille_id, prochaine_date_garde, frequence_garde, membre_id=membre_id
        )

    flash("Les informations du membre ont été mises à jour." if modifie else "Aucune modification effectuée.")
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
        return redirect(url_for("calendar"))

    # Première utilisation uniquement : écran de création de la famille.
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

    # L'accès des membres dépend de l'abonnement de l'administrateur familial.
    famille = get_member_family(membre["id"])
    if famille is None:
        conn.close()
        flash("Famille introuvable. Veuillez contacter l'administrateur.")
        return redirect(url_for("login"))

    admin_abonnement = conn.execute(
        "SELECT email FROM admin WHERE id = ?",
        (famille["admin_id"],)
    ).fetchone()

    if admin_abonnement is None:
        conn.close()
        flash("Compte administrateur introuvable.")
        return redirect(url_for("login"))

    statut_abonnement = check_login(admin_abonnement["email"])
    if statut_abonnement != "ok":
        conn.close()
        if statut_abonnement == "trial_expired":
            flash("L'essai gratuit de la famille est terminé. L'administrateur doit choisir un abonnement.")
        else:
            flash("L'abonnement de la famille est expiré. L'administrateur doit le renouveler.")
        return redirect(url_for("login", abonnement="expire"))

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
    session.permanent = True

    session["logged"] = True
    session["admin_logged"] = False
    session["membre_id"] = membre["id"]
    session["famille_id"] = membre["famille_id"]
    session["user_name"] = (
        f"{membre['prenom']} {membre['nom']}"
    )

    return redirect(url_for("calendar"))


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
    if membre is None or membre["famille_id"] != session.get("famille_id"):
        session.clear()
        return redirect(url_for("home"))

    return redirect(url_for("calendar"))


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
# MESSAGERIE INTERNE
# ==========================================================
def current_messaging_identity():
    if not session.get("logged"):
        return None
    if session.get("admin_logged"):
        return ("admin", session.get("admin_id"), session.get("famille_id"))
    if session.get("membre_id"):
        return ("membre", session.get("membre_id"), session.get("famille_id"))
    return None


def messaging_name_for_viewer(conn, viewer_identity, target_type, target_id, fallback):
    """Retourne le surnom privé choisi par le membre connecté, sinon le nom habituel."""
    if viewer_identity and viewer_identity[0] == "membre":
        row = conn.execute("""
            SELECT surnom FROM messagerie_surnoms
            WHERE famille_id=? AND owner_type=? AND owner_id=?
              AND target_type=? AND target_id=?
        """, (viewer_identity[2], viewer_identity[0], viewer_identity[1], target_type, target_id)).fetchone()
        if row and row["surnom"]:
            return row["surnom"]
    return fallback


def conversation_is_accessible(conn, conversation_id, identity):
    kind, user_id, family_id = identity
    return conn.execute("""
        SELECT c.id FROM conversations c
        JOIN conversation_participants p ON p.conversation_id = c.id
        WHERE c.id = ? AND c.famille_id = ?
          AND p.user_type = ? AND p.user_id = ?
    """, (conversation_id, family_id, kind, user_id)).fetchone() is not None


def send_message_push_notifications(conn, conversation_id, sender_identity):
    """Notifie uniquement les participants de la conversation, sauf l'expéditeur."""
    public_key = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    if not public_key or not private_key:
        app.logger.info("Notifications Push non configurées : clés VAPID absentes.")
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        app.logger.exception("Dépendance pywebpush absente.")
        return

    sender_type, sender_id, family_id = sender_identity
    rows = conn.execute("""
        SELECT DISTINCT s.id, s.user_type, s.user_id, s.endpoint, s.subscription_json
        FROM push_subscriptions s
        JOIN conversation_participants p
          ON p.user_type = s.user_type
         AND p.user_id = s.user_id
         AND p.conversation_id = ?
        WHERE s.famille_id = ?
          AND NOT (s.user_type = ? AND s.user_id = ?)
    """, (conversation_id, family_id, sender_type, sender_id)).fetchall()

    if not rows:
        app.logger.warning(
            "Push : aucun appareil enregistré pour les destinataires de la conversation %s (famille %s).",
            conversation_id, family_id
        )
        return

    app.logger.info(
        "Push : tentative d'envoi pour %s appareil(s), conversation %s.",
        len(rows), conversation_id
    )

    for row in rows:
        try:
            # Le compteur est calculé pour le destinataire de CET appareil,
            # sur toutes ses conversations, et non pour l'expéditeur.
            unread = conn.execute("""
                SELECT COUNT(*) AS total
                FROM messages m
                JOIN conversation_participants p ON p.conversation_id=m.conversation_id
                JOIN conversations c ON c.id=m.conversation_id
                WHERE c.famille_id=? AND p.user_type=? AND p.user_id=?
                  AND (p.last_read_at IS NULL OR m.created_at > p.last_read_at)
                  AND NOT (m.sender_type=? AND m.sender_id=?)
            """, (family_id, row["user_type"], row["user_id"],
                  row["user_type"], row["user_id"])).fetchone()
            payload = json.dumps({
                "title": "Dis-moi tout",
                "body": "Vous avez reçu un nouveau message.",
                "url": url_for("messagerie", conversation=conversation_id),
                "badgeCount": int(unread["total"] if unread else 1)
            })
            # Render stocke la clé privée dans une variable d'environnement.
            # pywebpush attend ici un chemin vers un fichier PEM (ou une clé DER
            # encodée), pas le texte PEM brut passé directement à from_string().
            # On écrit donc temporairement le PEM dans un fichier sécurisé,
            # puis on transmet son chemin à pywebpush.
            private_key_pem = private_key.replace("\\n", "\n").strip()
            key_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".pem",
                    prefix="dismoitout-vapid-",
                    delete=False
                ) as key_file:
                    key_file.write(private_key_pem)
                    key_path = key_file.name

                webpush(
                    subscription_info=json.loads(row["subscription_json"]),
                    data=payload,
                    vapid_private_key=key_path,
                    vapid_claims={
                        "sub": os.environ.get(
                            "VAPID_SUBJECT", "mailto:contact@dismoi-tout.fr"
                        ).strip()
                    }
                )
            finally:
                if key_path and os.path.exists(key_path):
                    os.remove(key_path)
            app.logger.info(
                "Push : notification envoyée à un appareil (type %s, utilisateur %s).",
                row["user_type"], row["user_id"]
            )
        except WebPushException as exc:
            status_code = getattr(
                getattr(exc, "response", None), "status_code", None
            )
            app.logger.warning(
                "Échec Web Push pour l'abonnement %s (HTTP %s) : %s",
                row["id"], status_code, exc
            )
            if status_code in (404, 410):
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (row["id"],))
                conn.commit()
        except Exception:
            app.logger.exception(
                "Erreur lors de l'envoi Push pour l'abonnement %s.", row["id"]
            )


@app.route("/push/vapid-public-key", methods=["GET"])
def push_vapid_public_key():
    if not session.get("logged"):
        return jsonify({"error": "Authentification requise"}), 401
    public_key = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if not public_key:
        return jsonify({"error": "Notifications Push non configurées"}), 503
    return jsonify({"publicKey": public_key})


@app.route("/push/subscribe", methods=["POST"])
def push_subscribe():
    identity = current_messaging_identity()
    if identity is None:
        return jsonify({"success": False}), 401

    subscription = request.get_json(silent=True)
    if not isinstance(subscription, dict):
        return jsonify({"success": False, "error": "Données invalides"}), 400

    endpoint = subscription.get("endpoint")
    keys = subscription.get("keys")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return jsonify({"success": False, "error": "Adresse invalide"}), 400
    if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"success": False, "error": "Clés de notification manquantes"}), 400

    kind, user_id, family_id = identity
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO push_subscriptions
                (endpoint, subscription_json, user_type, user_id, famille_id, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(endpoint) DO UPDATE SET
                subscription_json = excluded.subscription_json,
                user_type = excluded.user_type,
                user_id = excluded.user_id,
                famille_id = excluded.famille_id,
                updated_at = CURRENT_TIMESTAMP
        """, (endpoint, json.dumps(subscription), kind, user_id, family_id))
        conn.commit()
        app.logger.info(
            "Push : appareil enregistré/actualisé pour %s %s (famille %s).",
            kind, user_id, family_id
        )
        return jsonify({"success": True})
    except Exception:
        conn.rollback()
        app.logger.exception("Push : impossible d'enregistrer l'appareil.")
        return jsonify({"success": False, "error": "Enregistrement impossible"}), 500
    finally:
        conn.close()


@app.route("/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    identity = current_messaging_identity()
    if identity is None:
        return jsonify({"success": False}), 401
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str):
        return jsonify({"success": False}), 400
    kind, user_id, family_id = identity
    conn = get_connection()
    conn.execute("""
        DELETE FROM push_subscriptions
        WHERE endpoint = ? AND user_type = ? AND user_id = ? AND famille_id = ?
    """, (endpoint, kind, user_id, family_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/messagerie", methods=["GET", "POST"])
def messagerie():
    identity = current_messaging_identity()
    if identity is None:
        return redirect(url_for("login"))
    kind, user_id, family_id = identity
    conn = get_connection()

    if request.method == "POST":
        action = request.form.get("action", "create")

        if action == "create":
            selected = request.form.getlist("participants")
            all_members = request.form.get("all_members") == "1"

            family_members = conn.execute(
                "SELECT id FROM membres WHERE famille_id = ? ORDER BY prenom, nom",
                (family_id,)
            ).fetchall()
            allowed_member_ids = {str(row["id"]) for row in family_members}
            family_row = conn.execute(
                "SELECT admin_id FROM famille WHERE id = ?", (family_id,)
            ).fetchone()
            admin_id = int(family_row["admin_id"]) if family_row and family_row["admin_id"] else None

            # L'expéditeur est ajouté automatiquement. Un membre peut choisir
            # l'administrateur et/ou les autres membres, jamais lui-même.
            if all_members:
                selected = list(allowed_member_ids)
                if kind == "membre" and admin_id is not None:
                    selected.append("admin")
            valid_selected = []
            for value in selected:
                if value == "admin":
                    if admin_id is not None and kind != "admin":
                        valid_selected.append(("admin", admin_id))
                elif value in allowed_member_ids and not (kind == "membre" and value == str(user_id)):
                    valid_selected.append(("membre", int(value)))

            # Retire les doublons tout en conservant l'ordre.
            valid_selected = list(dict.fromkeys(valid_selected))
            if not valid_selected:
                flash("Choisissez au moins un destinataire.")
                conn.close()
                return redirect(url_for("messagerie"))

            conn.execute("INSERT INTO conversations (famille_id) VALUES (?)", (family_id,))
            conversation_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO conversation_participants (conversation_id,user_type,user_id) VALUES (?,?,?)",
                (conversation_id, kind, user_id)
            )
            for participant_type, participant_id in valid_selected:
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_participants (conversation_id,user_type,user_id) VALUES (?,?,?)",
                    (conversation_id, participant_type, participant_id)
                )
            conn.commit()
            conn.close()
            return redirect(url_for("messagerie", conversation=conversation_id))

        elif action == "send":
            conversation_id = request.form.get("conversation_id", type=int)
            contenu = request.form.get("contenu", "").strip()
            reply_to_message_id = request.form.get("reply_to_message_id", type=int)
            uploaded = request.files.get("fichier")
            has_file = uploaded is not None and bool(uploaded.filename)

            if not conversation_id or (not contenu and not has_file):
                flash("Écrivez un message ou choisissez un fichier.")
            elif len(contenu) > 5000:
                flash("Le message est trop long (5 000 caractères maximum).")
            elif request.content_length and request.content_length > 12 * 1024 * 1024:
                flash("La pièce jointe dépasse la limite autorisée de 10 Mo.")
                conn.close()
                return redirect(url_for("messagerie", conversation=conversation_id))
            elif not conversation_is_accessible(conn, conversation_id, identity):
                flash("Cette conversation n'est pas accessible.")
            else:
                fichier_nom = None
                fichier_stockage = None
                if has_file:
                    original_name = secure_filename(uploaded.filename) or "piece_jointe"
                    extension = os.path.splitext(original_name)[1].lower()
                    if uploaded.content_length and uploaded.content_length > 10 * 1024 * 1024:
                        flash("Le fichier dépasse la limite de 10 Mo.")
                        conn.close()
                        return redirect(url_for("messagerie", conversation=conversation_id))
                    fichier_nom = original_name[:200]
                    fichier_stockage = f"{uuid.uuid4().hex}{extension}"
                    fichier_data = uploaded.read()
                    if len(fichier_data) > 10 * 1024 * 1024:
                        flash("Le fichier dépasse la limite de 10 Mo.")
                        conn.close()
                        return redirect(url_for("messagerie", conversation=conversation_id))

                else:
                    fichier_data = None

                # Une réponse ne peut référencer qu'un message de cette conversation.
                # Une référence invalide est ignorée sans bloquer l'envoi du message.
                if reply_to_message_id:
                    reply_target = conn.execute("""
                        SELECT id FROM messages
                        WHERE id=? AND conversation_id=?
                    """, (reply_to_message_id, conversation_id)).fetchone()
                    if not reply_target:
                        reply_to_message_id = None

                conn.execute("""
                    INSERT INTO messages
                    (conversation_id,sender_type,sender_id,contenu,fichier_nom,fichier_stockage,fichier_data,reply_to_message_id)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (conversation_id, kind, user_id, contenu, fichier_nom, fichier_stockage, fichier_data, reply_to_message_id))
                conn.commit()
                conn.execute("""
                    UPDATE conversation_participants
                    SET last_read_at = CURRENT_TIMESTAMP
                    WHERE conversation_id = ? AND user_type = ? AND user_id = ?
                """, (conversation_id, kind, user_id))
                # Réafficher chez les destinataires si un nouveau message arrive.
                conn.execute("""
                    DELETE FROM conversation_hidden
                    WHERE conversation_id=? AND NOT (user_type=? AND user_id=?)
                """, (conversation_id, kind, user_id))
                conn.execute("""
                    DELETE FROM conversation_hidden
                    WHERE conversation_id=? AND user_type=? AND user_id=?
                """, (conversation_id, kind, user_id))
                conn.commit()
                send_message_push_notifications(conn, conversation_id, identity)

            conn.close()
            return redirect(url_for("messagerie", conversation=conversation_id))

        elif action == "set_nickname":
            # Fonction réservée aux membres : leurs surnoms restent personnels.
            if kind != "membre":
                conn.close()
                return redirect(url_for("messagerie"))
            target_type = request.form.get("target_type", "").strip()
            target_id = request.form.get("target_id", type=int)
            surnom = request.form.get("surnom", "").strip()[:80]
            valid_target = False
            if target_type == "admin":
                family_row = conn.execute("SELECT admin_id FROM famille WHERE id=?", (family_id,)).fetchone()
                valid_target = bool(family_row and family_row["admin_id"] and target_id == int(family_row["admin_id"]))
            elif target_type == "membre" and target_id and target_id != user_id:
                valid_target = conn.execute(
                    "SELECT 1 FROM membres WHERE id=? AND famille_id=?", (target_id, family_id)
                ).fetchone() is not None
            if valid_target:
                if surnom:
                    conn.execute("""
                        INSERT INTO messagerie_surnoms
                            (famille_id, owner_type, owner_id, target_type, target_id, surnom)
                        VALUES (?, 'membre', ?, ?, ?, ?)
                        ON CONFLICT(famille_id, owner_type, owner_id, target_type, target_id)
                        DO UPDATE SET surnom=excluded.surnom
                    """, (family_id, user_id, target_type, target_id, surnom))
                    flash("Votre surnom a été enregistré.")
                else:
                    conn.execute("""
                        DELETE FROM messagerie_surnoms
                        WHERE famille_id=? AND owner_type='membre' AND owner_id=?
                          AND target_type=? AND target_id=?
                    """, (family_id, user_id, target_type, target_id))
                    flash("Le surnom personnel a été réinitialisé.")
                conn.commit()
            else:
                flash("Ce membre n'est pas un destinataire valide.")
            conn.close()
            return redirect(url_for("messagerie"))

        elif action == "delete_conversation":
            conversation_id = request.form.get("conversation_id", type=int)
            if conversation_id and conversation_is_accessible(conn, conversation_id, identity):
                # Effacement personnel : on masque uniquement cette conversation
                # dans la messagerie de l'utilisateur connecté. AUCUN message,
                # participant ou conversation partagée n'est supprimé.
                conn.execute("""
                    INSERT OR IGNORE INTO conversation_hidden
                        (conversation_id, user_type, user_id)
                    VALUES (?, ?, ?)
                """, (conversation_id, kind, user_id))
                conn.commit()
                flash("Conversation effacée uniquement de votre messagerie.")
            else:
                flash("Cette conversation n'est pas accessible.")
            conn.close()
            return redirect(url_for("messagerie"))

        elif action == "delete":
            message_id = request.form.get("message_id", type=int)
            conversation_id = request.form.get("conversation_id", type=int)
            if message_id and conversation_id and conversation_is_accessible(conn, conversation_id, identity):
                msg = conn.execute("""
                    SELECT id, sender_type, sender_id, fichier_stockage
                    FROM messages WHERE id=? AND conversation_id=?
                """, (message_id, conversation_id)).fetchone()
                # Un utilisateur peut supprimer uniquement ses propres messages.
                if msg and msg["sender_type"] == kind and msg["sender_id"] == user_id:
                    conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
                    conn.commit()
                else:
                    flash("Vous ne pouvez supprimer que vos propres messages.")
            conn.close()
            return redirect(url_for("messagerie", conversation=conversation_id))

    # Liste des conversations accessibles à l'utilisateur connecté.
    conversations = conn.execute("""
        SELECT c.id, c.created_at,
               (SELECT m.contenu FROM messages m WHERE m.conversation_id=c.id ORDER BY m.id DESC LIMIT 1) AS dernier_message,
               (SELECT COUNT(*) FROM messages m
                WHERE m.conversation_id=c.id
                  AND (p.last_read_at IS NULL OR m.created_at > p.last_read_at)
                  AND NOT (m.sender_type=? AND m.sender_id=?)) AS non_lus
        FROM conversations c
        JOIN conversation_participants p ON p.conversation_id=c.id
        WHERE c.famille_id=? AND p.user_type=? AND p.user_id=?
          AND NOT EXISTS (
              SELECT 1 FROM conversation_hidden h
              WHERE h.conversation_id=c.id
                AND h.user_type=p.user_type AND h.user_id=p.user_id
          )
        ORDER BY COALESCE((SELECT MAX(m2.id) FROM messages m2 WHERE m2.conversation_id=c.id),0) DESC, c.id DESC
    """, (kind, user_id, family_id, kind, user_id)).fetchall()

    # Nommer chaque conversation par les correspondants (pas par un numéro).
    conversations_named = []
    for conv in conversations:
        others = conn.execute("""
            SELECT p.user_type, p.user_id,
                   CASE WHEN p.user_type='admin'
                        THEN (SELECT prenom || ' ' || nom FROM admin WHERE id=p.user_id)
                        ELSE (SELECT COALESCE(NULLIF(pseudo,''), prenom) FROM membres WHERE id=p.user_id)
                   END AS participant_name
            FROM conversation_participants p
            WHERE p.conversation_id=?
              AND NOT (p.user_type=? AND p.user_id=?)
            ORDER BY participant_name
        """, (conv["id"], kind, user_id)).fetchall()
        conv_dict = dict(conv)
        names = [
            messaging_name_for_viewer(conn, identity, row["user_type"], row["user_id"], row["participant_name"])
            for row in others if row["participant_name"]
        ]
        conv_dict["titre"] = "Conversation avec " + (", ".join(names) if names else "correspondant")
        conversations_named.append(conv_dict)

    active_id = request.args.get("conversation", type=int)
    active = None
    messages = []
    active_title = None
    if active_id and conversation_is_accessible(conn, active_id, identity):
        active = conn.execute(
            "SELECT id FROM conversations WHERE id=? AND famille_id=?",
            (active_id, family_id)
        ).fetchone()
        participants = conn.execute("""
            SELECT p.user_type, p.user_id,
                   CASE WHEN p.user_type='admin'
                        THEN (SELECT prenom || ' ' || nom FROM admin WHERE id=p.user_id)
                        ELSE (SELECT COALESCE(NULLIF(pseudo,''), prenom) FROM membres WHERE id=p.user_id)
                   END AS participant_name
            FROM conversation_participants p
            WHERE p.conversation_id=?
              AND NOT (p.user_type=? AND p.user_id=?)
            ORDER BY participant_name
        """, (active_id, kind, user_id)).fetchall()
        active_names = [
            messaging_name_for_viewer(conn, identity, row["user_type"], row["user_id"], row["participant_name"])
            for row in participants if row["participant_name"]
        ]
        active_title = "Conversation avec " + (", ".join(active_names) if active_names else "correspondant")

        messages_raw = conn.execute("""
            SELECT m.*,
            CASE WHEN m.sender_type='admin' THEN
                (SELECT prenom || ' ' || nom FROM admin WHERE id=m.sender_id)
            ELSE (SELECT COALESCE(NULLIF(pseudo,''), prenom) FROM membres WHERE id=m.sender_id) END AS sender_name,
            CASE WHEN m.sender_type='membre' THEN
                (SELECT avatar FROM membres WHERE id=m.sender_id) ELSE NULL END AS sender_avatar,
            CASE WHEN m.sender_type='membre' THEN
                (SELECT photo_data IS NOT NULL FROM membres WHERE id=m.sender_id) ELSE 0 END AS sender_has_photo,
            rm.id AS reply_id,
            rm.contenu AS reply_contenu,
            rm.fichier_nom AS reply_fichier_nom,
            rm.fichier_stockage AS reply_fichier_stockage,
            rm.sender_type AS reply_sender_type,
            rm.sender_id AS reply_sender_id,
            CASE WHEN rm.sender_type='admin' THEN
                (SELECT prenom || ' ' || nom FROM admin WHERE id=rm.sender_id)
            WHEN rm.sender_type='membre' THEN
                (SELECT COALESCE(NULLIF(pseudo,''), prenom) FROM membres WHERE id=rm.sender_id)
            ELSE NULL END AS reply_sender_name
            FROM messages m
            LEFT JOIN messages rm ON rm.id=m.reply_to_message_id AND rm.conversation_id=m.conversation_id
            WHERE m.conversation_id=? ORDER BY m.id
        """, (active_id,)).fetchall()
        messages = []
        for message in messages_raw:
            message_dict = dict(message)
            message_dict["sender_name"] = messaging_name_for_viewer(
                conn, identity, message["sender_type"], message["sender_id"], message["sender_name"]
            )
            if message_dict.get("reply_id") and message_dict.get("reply_sender_name"):
                message_dict["reply_sender_name"] = messaging_name_for_viewer(
                    conn, identity, message["reply_sender_type"],
                    message["reply_sender_id"], message["reply_sender_name"]
                )
            messages.append(message_dict)
        conn.execute("""
            UPDATE conversation_participants SET last_read_at=CURRENT_TIMESTAMP
            WHERE conversation_id=? AND user_type=? AND user_id=?
        """, (active_id, kind, user_id))
        conn.commit()

    # Les membres voient les autres membres et l'administrateur comme destinataires.
    members = conn.execute(
        "SELECT id, prenom, nom, pseudo FROM membres WHERE famille_id=? ORDER BY prenom, nom",
        (family_id,)
    ).fetchall()
    admin_contact = None
    if kind == "membre":
        admin_contact = conn.execute(
            "SELECT id, prenom, nom FROM admin WHERE id=(SELECT admin_id FROM famille WHERE id=?)",
            (family_id,)
        ).fetchone()

    nickname_targets = []
    if kind == "membre":
        if admin_contact:
            alias = conn.execute("""
                SELECT surnom FROM messagerie_surnoms
                WHERE famille_id=? AND owner_type='membre' AND owner_id=?
                  AND target_type='admin' AND target_id=?
            """, (family_id, user_id, admin_contact["id"])).fetchone()
            nickname_targets.append({"type": "admin", "id": admin_contact["id"],
                                     "label": "Administratrice — " + admin_contact["prenom"],
                                     "surnom": alias["surnom"] if alias else ""})
        for member in members:
            if member["id"] == user_id:
                continue
            alias = conn.execute("""
                SELECT surnom FROM messagerie_surnoms
                WHERE famille_id=? AND owner_type='membre' AND owner_id=?
                  AND target_type='membre' AND target_id=?
            """, (family_id, user_id, member["id"])).fetchone()
            nickname_targets.append({"type": "membre", "id": member["id"],
                                     "label": member["pseudo"] or member["prenom"],
                                     "surnom": alias["surnom"] if alias else ""})

    conn.close()
    return render_template(
        "messagerie.html",
        conversations=conversations_named,
        active=active,
        active_title=active_title,
        messages=messages,
        members=members,
        admin_contact=admin_contact,
        active_id=active_id,
        current_kind=kind,
        current_user_id=user_id,
        nickname_targets=nickname_targets
    )


@app.route("/messagerie/fichier/<int:message_id>")
def messagerie_fichier(message_id):
    identity = current_messaging_identity()
    if identity is None:
        return redirect(url_for("login"))
    kind, user_id, family_id = identity
    conn = get_connection()
    row = conn.execute("""
        SELECT m.fichier_nom, m.fichier_stockage, m.fichier_data, m.conversation_id
        FROM messages m
        JOIN conversations c ON c.id=m.conversation_id
        WHERE m.id=? AND c.famille_id=?
    """, (message_id, family_id)).fetchone()
    if not row or not row["fichier_stockage"] or not conversation_is_accessible(conn, row["conversation_id"], identity):
        conn.close()
        return "Fichier introuvable", 404
    file_data = row["fichier_data"]
    download_name = row["fichier_nom"] or row["fichier_stockage"] or "piece_jointe"
    conn.close()
    if file_data is None:
        return "Fichier introuvable", 404
    extension = os.path.splitext(download_name)[1].lower()
    audio_mimes = {
        ".webm": "audio/webm", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
        ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "audio/mp4"
    }
    if extension in audio_mimes:
        return send_file(io.BytesIO(file_data), mimetype=audio_mimes[extension], as_attachment=False,
                         download_name=download_name, conditional=True)
    return send_file(io.BytesIO(file_data), as_attachment=True, download_name=download_name)


@app.route("/messagerie/non-lus")
def messagerie_non_lus():
    identity = current_messaging_identity()
    if identity is None:
        return jsonify({"count": 0}), 401
    kind, user_id, family_id = identity
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS total FROM messages m
        JOIN conversation_participants p ON p.conversation_id=m.conversation_id
        JOIN conversations c ON c.id=m.conversation_id
        WHERE c.famille_id=? AND p.user_type=? AND p.user_id=?
          AND NOT EXISTS (
              SELECT 1 FROM conversation_hidden h
              WHERE h.conversation_id=c.id
                AND h.user_type=p.user_type AND h.user_id=p.user_id
          )
          AND (p.last_read_at IS NULL OR m.created_at > p.last_read_at)
          AND NOT (m.sender_type=? AND m.sender_id=?)
    """, (family_id, kind, user_id, kind, user_id)).fetchone()
    conn.close()
    return jsonify({"count": row["total"] if row else 0})


# ==========================================================
# AJOUT D'UN ÉVÉNEMENT
# ==========================================================



@app.route("/add-event", methods=["GET"])
def add_event_page():
    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    if famille_id is None:
        return redirect(url_for("dashboard"))

    est_admin = session.get("admin_logged") is True
    membre_id = session.get("membre_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, prenom, nom, role FROM membres WHERE famille_id = ? ORDER BY prenom, nom",
        (famille_id,)
    )
    membres = cursor.fetchall()

    afficher_pro = est_admin
    destinataire_par_defaut = "admin" if est_admin else str(membre_id)
    if not est_admin:
        membre = next((m for m in membres if m["id"] == membre_id), None)
        if membre is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))
        afficher_pro = (membre["role"] or "").lower() == "conjoint"

    conn.close()
    return render_template(
        "add_event.html",
        est_admin=est_admin,
        afficher_pro=afficher_pro,
        membres=membres,
        membre_connecte_id=membre_id,
        destinataire_par_defaut=destinataire_par_defaut
    )


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
    if couleur not in {"#4f8fc9", "#d94f9b", "#54a66b", "#e2a43b"}:
        couleur = "#4f8fc9"
    if not titre or not date:
        flash("Veuillez saisir un titre et une date.")
        return redirect(url_for("add_event_page"))

    rappel = 1 if request.form.get("rappel") == "oui" else 0
    delais_autorises = {60, 4320, 10080, 21600}
    try:
        rappel_delai_minutes = int(request.form.get("rappel_delai_minutes", "60"))
    except (TypeError, ValueError):
        rappel_delai_minutes = 60
    if rappel_delai_minutes not in delais_autorises:
        rappel_delai_minutes = 60
    est_admin = session.get("admin_logged") is True
    admin_id = session.get("admin_id")
    auteur_membre_id = session.get("membre_id")
    agenda = request.form.get("type_agenda", "perso").strip().lower()
    if agenda not in ("pro", "perso"):
        flash("Agenda invalide.")
        return redirect(url_for("add_event_page"))

    destinataires = list(dict.fromkeys(request.form.getlist("destinataire_ids")))
    if not destinataires:
        flash("Sélectionnez au moins un agenda destinataire.")
        return redirect(url_for("add_event_page"))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, role FROM membres WHERE famille_id = ?",
        (famille_id,)
    )
    membres_famille = {str(row["id"]): row for row in cursor.fetchall()}

    # Contrôle strict des destinataires : seuls l'administrateur et les
    # membres de la famille peuvent recevoir une proposition.
    membres_cibles = []
    cible_admin = "admin" in destinataires
    for valeur in destinataires:
        if valeur == "admin":
            continue
        if not valeur.isdigit() or valeur not in membres_famille:
            conn.close()
            flash("Un destinataire sélectionné n'appartient pas à votre famille.")
            return redirect(url_for("add_event_page"))
        if int(valeur) not in membres_cibles:
            membres_cibles.append(int(valeur))

    if est_admin:
        if agenda == "pro" and membres_cibles:
            conn.close()
            flash("L'agenda PRO de l'administratrice ne peut pas être proposé dans l'agenda d'un membre.")
            return redirect(url_for("add_event_page"))
        auteur_role = None
        auteur_id = admin_id
    else:
        auteur = membres_famille.get(str(auteur_membre_id))
        if auteur is None:
            conn.close()
            session.clear()
            return redirect(url_for("home"))
        auteur_role = (auteur["role"] or "enfant").lower()
        auteur_id = auteur_membre_id
        if agenda == "pro" and auteur_role != "conjoint":
            conn.close()
            flash("Seul le conjoint actuel dispose d'un agenda PRO.")
            return redirect(url_for("add_event_page"))
        if agenda == "pro" and any(mid != auteur_membre_id for mid in membres_cibles):
            conn.close()
            flash("Un membre peut proposer un rendez-vous PRO uniquement dans son propre agenda ou celui de l'administratrice.")
            return redirect(url_for("add_event_page"))

    if not cible_admin and not membres_cibles:
        conn.close()
        flash("Sélectionnez au moins un destinataire.")
        return redirect(url_for("add_event_page"))

    created_by_admin_id = admin_id if est_admin else None
    created_by_membre_id = None if est_admin else auteur_membre_id
    nombre_attente = 0

    # Une ligne indépendante par agenda destinataire : chaque personne
    # accepte ou refuse uniquement sa propre demande.
    cibles = []
    if cible_admin:
        cibles.append(("admin", None, 1, f"admin_{agenda}"))
    for mid in membres_cibles:
        cibles.append(("membre", mid, 0, "membre_perso"))

    for type_cible, destinataire_id, concerne_admin, type_agenda in cibles:
        est_son_propre_agenda = (
            (est_admin and type_cible == "admin")
            or (not est_admin and type_cible == "membre" and destinataire_id == auteur_membre_id)
        )
        statut = "accepte" if est_son_propre_agenda else "en_attente"
        valide_admin = admin_id if (est_admin and est_son_propre_agenda) else None
        valide_membre = auteur_membre_id if (not est_admin and est_son_propre_agenda) else None

        cursor.execute(
            """
            INSERT INTO evenements (
                famille_id, membre_id, titre, description, date, heure,
                calendrier, rappel, rappel_delai_minutes, type_evenement, created_by_admin_id,
                created_by_membre_id, statut_validation, valide_par_admin_id,
                valide_par_membre_id, concerne_admin, type_agenda
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                famille_id, destinataire_id, titre, description, date, heure,
                couleur, rappel, rappel_delai_minutes, "normal", created_by_admin_id,
                created_by_membre_id, statut, valide_admin,
                valide_membre, concerne_admin, type_agenda
            )
        )
        if statut == "en_attente":
            nombre_attente += 1

    conn.commit()
    conn.close()
    if nombre_attente:
        flash(f"Proposition envoyée. {nombre_attente} destinataire(s) doivent accepter l'événement.")
    else:
        flash("Événement ajouté dans votre agenda.")
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

    # Un utilisateur peut gérer les événements présents dans son propre
    # agenda. Les agendas consultés uniquement par partage restent en lecture seule.
    if admin_logged:
        autorise = (
            evenement["concerne_admin"] == 1
            or evenement["created_by_admin_id"] == admin_id
        )
    else:
        autorise = (
            evenement["concerne_admin"] == 0
            and evenement["membre_id"] == membre_id
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

    # Un utilisateur peut gérer les événements présents dans son propre
    # agenda. Les agendas consultés uniquement par partage restent en lecture seule.
    if admin_logged:
        autorise = (
            evenement["concerne_admin"] == 1
            or evenement["created_by_admin_id"] == admin_id
        )
    else:
        autorise = (
            evenement["concerne_admin"] == 0
            and evenement["membre_id"] == membre_id
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

@app.route("/validate_event/<int:event_id>", methods=["POST"])
def validate_event(event_id):
    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    est_admin = session.get("admin_logged") is True
    membre_id = session.get("membre_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM evenements WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente'",
        (event_id, famille_id)
    )
    evenement = cursor.fetchone()
    if evenement is None:
        conn.close()
        flash("Cette proposition est introuvable ou déjà traitée.")
        return redirect(url_for("calendar"))

    # Seul le destinataire de CETTE ligne peut répondre.
    autorise = (
        (est_admin and evenement["concerne_admin"] == 1)
        or (not est_admin and evenement["concerne_admin"] == 0 and evenement["membre_id"] == membre_id)
    )
    if not autorise:
        conn.close()
        flash("Vous n'êtes pas le destinataire de cette proposition.")
        return redirect(url_for("calendar"))

    if est_admin:
        cursor.execute(
            "UPDATE evenements SET statut_validation = 'accepte', valide_par_admin_id = ? WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente' AND concerne_admin = 1",
            (session.get("admin_id"), event_id, famille_id)
        )
    else:
        cursor.execute(
            "UPDATE evenements SET statut_validation = 'accepte', valide_par_membre_id = ? WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente' AND concerne_admin = 0 AND membre_id = ?",
            (membre_id, event_id, famille_id, membre_id)
        )
    conn.commit()
    conn.close()
    flash("Événement accepté : il est maintenant ajouté à votre agenda.")
    return redirect(url_for("calendar"))


@app.route("/refuse_event/<int:event_id>", methods=["POST"])
def refuse_event(event_id):
    if not session.get("logged"):
        return redirect(url_for("home"))

    famille_id = session.get("famille_id")
    est_admin = session.get("admin_logged") is True
    membre_id = session.get("membre_id")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM evenements WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente'",
        (event_id, famille_id)
    )
    evenement = cursor.fetchone()
    if evenement is None:
        conn.close()
        flash("Cette proposition est introuvable ou déjà traitée.")
        return redirect(url_for("calendar"))

    autorise = (
        (est_admin and evenement["concerne_admin"] == 1)
        or (not est_admin and evenement["concerne_admin"] == 0 and evenement["membre_id"] == membre_id)
    )
    if not autorise:
        conn.close()
        flash("Vous n'êtes pas le destinataire de cette proposition.")
        return redirect(url_for("calendar"))

    if est_admin:
        cursor.execute(
            "UPDATE evenements SET statut_validation = 'refuse' WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente' AND concerne_admin = 1",
            (event_id, famille_id)
        )
    else:
        cursor.execute(
            "UPDATE evenements SET statut_validation = 'refuse' WHERE id = ? AND famille_id = ? AND statut_validation = 'en_attente' AND concerne_admin = 0 AND membre_id = ?",
            (event_id, famille_id, membre_id)
        )
    conn.commit()
    conn.close()
    flash("Proposition refusée. Rien n'a été ajouté à votre agenda.")
    return redirect(url_for("calendar"))
    
# ==========================================================
# VÉRIFICATION AUTOMATIQUE DE L'ABONNEMENT
# Appelée par base.html toutes les heures lorsque la session est ouverte.
# ==========================================================

@app.route("/subscription/check", methods=["GET"])
def subscription_check():

    if not session.get("logged"):
        return jsonify({
            "success": False,
            "redirect": url_for("login")
        }), 401

    email = None

    if session.get("admin_logged") is True:
        conn = get_connection()
        admin = conn.execute(
            "SELECT email FROM admin WHERE id = ?",
            (session.get("admin_id"),)
        ).fetchone()
        conn.close()

        if admin is not None:
            email = admin["email"]

    else:
        membre_id = session.get("membre_id")
        if membre_id is not None:
            membre_famille = get_member_family(membre_id)
            if membre_famille is not None:
                conn = get_connection()
                admin = conn.execute(
                    "SELECT email FROM admin WHERE id = ?",
                    (membre_famille["admin_id"],)
                ).fetchone()
                conn.close()

                if admin is not None:
                    email = admin["email"]

    if not email:
        session.clear()
        return jsonify({
            "success": False,
            "redirect": url_for("login")
        }), 401

    statut = check_login(email)

    if statut == "ok":
        return jsonify({"success": True, "status": "ok"}), 200

    if statut == "trial_expired":
        flash("Votre essai gratuit est terminé. Choisissez votre abonnement pour continuer.")
    elif statut == "subscription_expired":
        flash("Votre abonnement est expiré. Choisissez votre formule pour renouveler votre accès.")
    else:
        flash("Votre compte doit être vérifié. Veuillez vous reconnecter.")

    session.clear()
    return jsonify({
        "success": False,
        "status": statut,
        "redirect": url_for("login", abonnement="expire")
    }), 403


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
            ev_dict["peut_gerer"] = bool(
                ev_dict.get("concerne_admin") == 1
                or ev_dict.get("created_by_admin_id") == admin_id
            )
        else:
            ev_dict["peut_gerer"] = bool(
                ev_dict.get("concerne_admin") == 0
                and ev_dict.get("membre_id") == membre_id
            )

        if est_admin:
            # Ses propres rendez-vous et uniquement ceux qu'elle a créés
            # dans les agendas des membres.
            if ev_dict.get("concerne_admin") == 1:
                # Agenda de l'administratrice : ses événements personnels
                # et les propositions qu'elle a elle-même acceptées.
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

    # Demandes reçues : chaque destinataire ne voit que les propositions
    # qui concernent son propre agenda.
    if est_admin:
        cursor.execute(
            """
            SELECT e.*,
                   CASE
                     WHEN e.created_by_membre_id IS NOT NULL THEN
                       (SELECT m.prenom || ' ' || m.nom FROM membres m WHERE m.id = e.created_by_membre_id)
                     ELSE
                       (SELECT a.prenom || ' ' || a.nom FROM admin a WHERE a.id = e.created_by_admin_id)
                   END AS proposeur
            FROM evenements e
            WHERE e.famille_id = ? AND e.statut_validation = 'en_attente'
              AND e.concerne_admin = 1
            ORDER BY e.date, e.heure, e.id
            """,
            (famille_id,)
        )
    else:
        cursor.execute(
            """
            SELECT e.*,
                   CASE
                     WHEN e.created_by_membre_id IS NOT NULL THEN
                       (SELECT m.prenom || ' ' || m.nom FROM membres m WHERE m.id = e.created_by_membre_id)
                     ELSE
                       (SELECT a.prenom || ' ' || a.nom FROM admin a WHERE a.id = e.created_by_admin_id)
                   END AS proposeur
            FROM evenements e
            WHERE e.famille_id = ? AND e.statut_validation = 'en_attente'
              AND e.concerne_admin = 0 AND e.membre_id = ?
            ORDER BY e.date, e.heure, e.id
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

    # Plusieurs comptes Google peuvent rester connectés pour un même propriétaire.
    owner_type, owner_id = get_calendar_owner()
    google_email = None
    google_calendars = []
    google_error = None
    google_conn = get_connection()
    google_cursor = google_conn.cursor()
    google_cursor.execute(
        """
        SELECT id, google_email, access_token, refresh_token, expires_at
        FROM google_connections_users
        WHERE famille_id = ? AND owner_type = ? AND owner_id = ?
        ORDER BY id
        """,
        (famille_id, owner_type, owner_id)
    )
    google_connections = [dict(row) for row in google_cursor.fetchall()]
    google_conn.close()

    requested_google_id = request.args.get("google_account_id", type=int)
    selected_google_connection = next(
        (item for item in google_connections if item["id"] == requested_google_id),
        None
    )
    if selected_google_connection is None and google_connections:
        selected_google_connection = google_connections[0]

    google_connection_id = (
        selected_google_connection["id"] if selected_google_connection else None
    )

    if selected_google_connection:
        google_email = selected_google_connection["google_email"]
        google_access_token = selected_google_connection["access_token"]
        try:
            expiry = datetime.fromisoformat(selected_google_connection["expires_at"])
        except (TypeError, ValueError):
            expiry = datetime.min

        if expiry.timestamp() <= datetime.now().timestamp() + 120:
            if selected_google_connection["refresh_token"]:
                try:
                    refreshed = refresh_google_token(
                        GOOGLE_CLIENT_ID,
                        GOOGLE_CLIENT_SECRET,
                        selected_google_connection["refresh_token"]
                    )
                    if refreshed and refreshed.get("access_token"):
                        google_access_token = refreshed["access_token"]
                        new_expiry = datetime.now().timestamp() + int(refreshed.get("expires_in", 3600))
                        update_conn = get_connection()
                        update_conn.execute(
                            """
                            UPDATE google_connections_users
                            SET access_token = ?, expires_at = ?
                            WHERE id = ? AND famille_id = ? AND owner_type = ? AND owner_id = ?
                            """,
                            (
                                google_access_token,
                                datetime.fromtimestamp(new_expiry).isoformat(),
                                google_connection_id, famille_id, owner_type, owner_id
                            )
                        )
                        update_conn.commit()
                        update_conn.close()
                    else:
                        google_error = "La connexion Google doit être renouvelée."
                except Exception:
                    app.logger.exception("Renouvellement Google depuis le calendrier impossible")
                    google_error = "Impossible de renouveler Google. Reconnectez ce compte."
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
        google_error=google_error,
        google_connections=google_connections,
        google_connection_id=google_connection_id
    )


# ==========================================================
# PROFIL
# ==========================================================

@app.route("/profile/photo", methods=["POST"])
def update_member_photo():
    identity = current_messaging_identity()
    if identity is None or identity[0] != "membre":
        return redirect(url_for("profile"))
    _, membre_id, famille_id = identity
    try:
        photo_data, photo_mime = read_member_photo_upload(request.files.get("photo"))
    except ValueError as error:
        flash(str(error))
        return redirect(url_for("profile"))
    avatar = request.form.get("avatar", "").strip()
    avatars_autorises = {"", "😊", "👩", "👨", "👵", "👴", "🧒", "👶", "🐱", "🐶", "🌸", "⭐"}
    if avatar not in avatars_autorises:
        avatar = ""
    supprimer = request.form.get("supprimer_photo") == "oui"
    conn = get_connection()
    conn.execute("""
        UPDATE membres
        SET photo_data=CASE WHEN ? THEN NULL WHEN ? IS NOT NULL THEN ? ELSE photo_data END,
            photo_mime=CASE WHEN ? THEN NULL WHEN ? IS NOT NULL THEN ? ELSE photo_mime END,
            avatar=?
        WHERE id=? AND famille_id=?
    """, (1 if supprimer else 0, photo_data, photo_data,
          1 if supprimer else 0, photo_mime, photo_mime, avatar or None,
          membre_id, famille_id))
    conn.commit()
    conn.close()
    flash("Votre photo ou avatar a été mis à jour.")
    return redirect(url_for("profile"))


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
        WHERE owner_type = ? AND owner_id = ? AND LOWER(google_email) = LOWER(?)
        """,
        (owner_type, owner_id, google_email)
    )
    ancienne_connexion = cursor.fetchone()

    # Conserver le refresh_token du même compte si Google ne le renvoie pas.
    if not refresh_token and ancienne_connexion:
        refresh_token = ancienne_connexion["refresh_token"]

    cursor.execute(
        """
        INSERT INTO google_connections_users
            (famille_id, owner_type, owner_id, google_email,
             access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_type, owner_id, google_email COLLATE NOCASE) DO UPDATE SET
            famille_id = excluded.famille_id,
            access_token = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, google_connections_users.refresh_token),
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

        selected_connection_id = request.form.get("google_connection_id", type=int)
        conn = get_connection()
        cursor = conn.cursor()
        if selected_connection_id:
            cursor.execute(
                """
                SELECT id, google_email, access_token, refresh_token, expires_at
                FROM google_connections_users
                WHERE id = ? AND famille_id = ? AND owner_type = ? AND owner_id = ?
                """,
                (selected_connection_id, famille_id, owner_type, owner_id)
            )
        else:
            cursor.execute(
                """
                SELECT id, google_email, access_token, refresh_token, expires_at
                FROM google_connections_users
                WHERE famille_id = ? AND owner_type = ? AND owner_id = ?
                ORDER BY id LIMIT 1
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
                WHERE id = ? AND owner_type = ? AND owner_id = ? AND famille_id = ?
                """,
                (
                    access_token,
                    datetime.fromtimestamp(new_expiry).isoformat(),
                    connection["id"], owner_type, owner_id, famille_id
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

        # Déconnexion automatique du compte Google utilisé. Seuls les jetons
        # de connexion sont retirés : les événements déjà importés restent
        # enregistrés dans la table evenements.
        try:
            jeton_a_revoquer = refresh_token or access_token
            if jeton_a_revoquer:
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": jeton_a_revoquer},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=8
                )
        except Exception:
            app.logger.exception("Révocation Google impossible après import")

        cleanup_conn = get_connection()
        cleanup_conn.execute(
            """
            DELETE FROM google_connections_users
            WHERE id = ? AND famille_id = ? AND owner_type = ? AND owner_id = ?
            """,
            (connection["id"], famille_id, owner_type, owner_id)
        )
        cleanup_conn.commit()
        cleanup_conn.close()

        if errors:
            flash(
                f"Import partiel : {total_imported} événement(s) ajouté(s), "
                f"{total_skipped} déjà présent(s). Le compte Google a été déconnecté. "
                f"Erreurs : {' | '.join(errors)}"
            )
        else:
            flash(
                f"Import Google terminé : {total_imported} événement(s) ajouté(s), "
                f"{total_skipped} déjà présent(s), sur {len(calendars)} agenda(s). "
                "Le compte Google a été déconnecté. Vos événements restent dans Dis-moi tout."
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



