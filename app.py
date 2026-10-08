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
    flash
)

import os
import sqlite3
import pandas as pd
import requests
import bcrypt

from datetime import datetime

from config import (
    DATABASE,
    SECRET_KEY
)


# ==========================================================
# CONFIGURATION GOOGLE SHEETS
# ==========================================================

SHEET_ID = "1JWwwLP3IKaG-ELsC3li84eouOFVFnv_C5MxBDQSfz3M"


# ==========================================================
# CONFIGURATION MAKE
# ==========================================================

WEBHOOK_URL = "https://hook.eu1.make.com/942mf8fk2jehv637xc3s0tsjsxrad0gu"


# ==========================================================
# CONFIGURATION STRIPE — DIS-MOI TOUT
# ==========================================================

# Essai gratuit : 7 jours
TRIAL_DAYS = 7

TRIAL_LINK = "https://buy.stripe.com/00w28s2wifc1cmn7PW9fW0g"

# Abonnement : 10 € TTC / mois
PRICE_TVAC = 10

STRIPE_LINK = "https://buy.stripe.com/aFa14odaWd3Tdqrc6c9fW0n"


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
# INITIALISATION SQLITE
# ==========================================================

def init_database():

    conn = get_connection()

    cursor = conn.cursor()

    conn.commit()

    conn.close()

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
# DÉMARRAGE
# ==========================================================

init_database()

# ==========================================================
# ACCUEIL
# ==========================================================

@app.route("/")
def home():

    return render_template(
        "index.html",
        price=PRICE_TVAC,
        trial_link=TRIAL_LINK,
        stripe_link=STRIPE_LINK
    )

# ==========================================================
# INSCRIPTION
# ==========================================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

    prenom = request.form.get("prenom", "").strip()

    nom = request.form.get("nom", "").strip()

    email = request.form.get("email", "").strip().lower()

    password = request.form.get("password", "")

    password2 = request.form.get("password2", "")

    if prenom == "":

        flash("Veuillez saisir votre prénom.")

        return redirect(url_for("home"))

    if nom == "":

        flash("Veuillez saisir votre nom.")

        return redirect(url_for("home"))

    if email == "":

        flash("Veuillez saisir votre adresse e-mail.")

        return redirect(url_for("home"))

    if password == "":

        flash("Veuillez saisir un mot de passe.")

        return redirect(url_for("home"))

    if password != password2:

        flash("Les mots de passe sont différents.")

        return redirect(url_for("home"))

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

        flash("Cette adresse e-mail existe déjà.")

        return redirect(url_for("home"))

    hashed = hash_password(password)

    cursor.execute(

        """
        INSERT INTO admin
        (
            nom,
            prenom,
            email,
            password
        )

        VALUES
        (?, ?, ?, ?)
        """,

        (
            nom,
            prenom,
            email,
            hashed
        )

    )

    conn.commit()

    conn.close()

    send_to_webhook(email)

    flash(
        "Compte créé avec succès."
    )

    return redirect(TRIAL_LINK)

# ==========================================================
# CONNEXION
# ==========================================================

@app.route(
    "/login",
    methods=["POST"]
)
def login():

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

        return redirect(url_for("home"))

    if result == "trial_expired":

        flash(
            "Votre essai gratuit est terminé."
        )

        return redirect(STRIPE_LINK)

    if result == "subscription_expired":

        flash(
            "Votre abonnement est expiré."
        )

        return redirect(STRIPE_LINK)

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

    session["user_name"] = (
        f"{admin['prenom']} {admin['nom']}"
    )

    return redirect(
        url_for("dashboard")
    )

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
# CRÉATION FAMILLE
# ==========================================================

@app.route(
    "/create_family",
    methods=["POST"]
)
def create_family():

    if not session.get("logged"):

        return redirect(url_for("home"))

    nom_famille = request.form.get(
        "nom_famille",
        ""
    ).strip()

    if nom_famille == "":

        flash("Veuillez saisir un nom.")

        return redirect(url_for("dashboard"))

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
            session["admin_id"],
            nom_famille
        )
    )

    conn.commit()

    conn.close()

    flash("Famille créée.")

    return redirect(url_for("dashboard"))

# ==========================================================
# AJOUT MEMBRE
# ==========================================================

@app.route(
    "/add_member",
    methods=["POST"]
)
def add_member():

    if not session.get("logged"):

        return redirect(url_for("home"))

    return render_template(
        "add_member.html"
    )

# ==========================================================
# LISTE MEMBRES
# ==========================================================

@app.route("/members")
def members():

    if not session.get("logged"):

        return redirect(url_for("home"))

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom
        """,
        (
            session.get("famille_id"),
        )
    )

    membres = cursor.fetchall()

    conn.close()

    return render_template(
        "members.html",
        membres=membres
    )

# ==========================================================
# CALENDRIER
# ==========================================================

@app.route("/calendar")
def calendar():

    if not session.get("logged"):

        return redirect(url_for("home"))

    return render_template(
        "calendar.html"
    )

# ==========================================================
# PARAMÈTRES
# ==========================================================

@app.route("/settings")
def settings():

    if not session.get("logged"):

        return redirect(url_for("home"))

    return render_template(
        "settings.html"
    )

# ==========================================================
# PROFIL
# ==========================================================

@app.route("/profile")
def profile():

    if not session.get("logged"):

        return redirect(url_for("home"))

    return render_template(
        "profile.html"
    )

# ==========================================================
# LANCEMENT
# ==========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
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
    password,
    peut_modifier=True
):

    hashed = hash_password(password)

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
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            famille_id,
            prenom,
            nom,
            email,
            hashed,
            "membre",
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

    conn.commit()

    conn.close()

# ==========================================================
# INSCRIPTION ADMINISTRATEUR
# ==========================================================

@app.route(
    "/register",
    methods=["POST"]
)
def register():

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

    if not session.get("logged"):

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

        return redirect(
            url_for("home")
        )

    famille_id = get_family_id(
        session["admin_id"]
    )

    if famille_id is None:

        flash(
            "Veuillez d'abord créer votre famille."
        )

        return redirect(
            url_for("dashboard")
        )

    if count_members(famille_id) >= 6:

        flash(
            "Votre famille contient déjà 6 membres."
        )

        return redirect(
            url_for("members")
        )

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

    peut_modifier = request.form.get(
        "peut_modifier"
    ) == "oui"

    if not prenom:

        flash(
            "Veuillez saisir le prénom."
        )

        return redirect(
            url_for("members")
        )

    if not nom:

        flash(
            "Veuillez saisir le nom."
        )

        return redirect(
            url_for("members")
        )

    if not password:

        flash(
            "Veuillez saisir un mot de passe."
        )

        return redirect(
            url_for("members")
        )

    if email and member_email_exists(email):

        flash(
            "Cette adresse e-mail est déjà utilisée."
        )

        return redirect(
            url_for("members")
        )

    create_member(
        famille_id,
        prenom,
        nom,
        email,
        password,
        peut_modifier
    )

    flash(
        "Membre ajouté avec succès."
    )

    return redirect(
        url_for("members")
    )

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

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

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
        "dashboard.html",
        famille=famille,
        membres=membres,
        total_membres=total_membres
    )

# ==========================================================
# CONNEXION MEMBRE
# ==========================================================

@app.route(
    "/member_login",
    methods=["POST"]
)
def member_login():

    email = request.form.get(
        "email",
        ""
    ).strip().lower()

    password = request.form.get(
        "password",
        ""
    )

    membre = get_member_by_email(
        email
    )

    if membre is None:

        flash(
            "Identifiants incorrects."
        )

        return redirect(
            url_for("home")
        )

    if not verify_password(
        password,
        membre["password"]
    ):

        flash(
            "Identifiants incorrects."
        )

        return redirect(
            url_for("home")
        )

    session.clear()

    session["logged"] = True
    session["admin_logged"] = False
    session["membre_id"] = membre["id"]
    session["famille_id"] = membre["famille_id"]
    session["user_name"] = (
        f"{membre['prenom']} {membre['nom']}"
    )

    return redirect(
        url_for("member_dashboard")
    )

# ==========================================================
# TABLEAU DE BORD MEMBRE
# ==========================================================

@app.route("/member_dashboard")
def member_dashboard():

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    membre_id = session.get(
        "membre_id"
    )

    if membre_id is None:

        return redirect(
            url_for("home")
        )

    membre = get_member_by_id(
        membre_id
    )

    if membre is None:

        session.clear()

        return redirect(
            url_for("home")
        )

    famille = get_member_family(
        membre_id
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE famille_id = ?
        ORDER BY date, heure
        """,
        (
            membre["famille_id"],
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
# AJOUT D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/add_event",
    methods=["POST"]
)
def add_event():

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    famille_id = session.get(
        "famille_id"
    )

    if famille_id is None:

        flash(
            "Famille introuvable."
        )

        return redirect(
            url_for("dashboard")
        )

    membre_id = request.form.get(
        "membre_id"
    )

    titre = request.form.get(
        "titre",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    date = request.form.get(
        "date",
        ""
    ).strip()

    heure = request.form.get(
        "heure",
        ""
    ).strip()

    calendrier = request.form.get(
        "calendrier",
        "Famille"
    ).strip()

    rappel = (
        1
        if request.form.get("rappel") == "oui"
        else 0
    )

    if not titre:

        flash(
            "Veuillez saisir un titre."
        )

        return redirect(
            url_for("calendar")
        )

    conn = get_connection()
    cursor = conn.cursor()

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
            membre_id or None,
            titre,
            description,
            date,
            heure,
            calendrier,
            rappel,
            "normal"
        )
    )

    conn.commit()
    conn.close()

    flash(
        "Événement ajouté."
    )

    return redirect(
        url_for("calendar")
    )

# ==========================================================
# MODIFICATION D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/edit_event/<int:event_id>",
    methods=["POST"]
)
def edit_event(event_id):

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    famille_id = session.get(
        "famille_id"
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            event_id,
            famille_id
        )
    )

    evenement = cursor.fetchone()

    if evenement is None:

        conn.close()

        flash(
            "Événement introuvable."
        )

        return redirect(
            url_for("calendar")
        )

    # Une garde partagée générée
    # automatiquement ne peut pas être
    # modifiée comme un événement normal.

    if evenement["type_evenement"] == "garde_partagee":

        conn.close()

        flash(
            "Les gardes partagées sont générées automatiquement."
        )

        return redirect(
            url_for("calendar")
        )

    titre = request.form.get(
        "titre",
        ""
    ).strip()

    description = request.form.get(
        "description",
        ""
    ).strip()

    date = request.form.get(
        "date",
        ""
    ).strip()

    heure = request.form.get(
        "heure",
        ""
    ).strip()

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

    flash(
        "Événement modifié."
    )

    return redirect(
        url_for("calendar")
    )

# ==========================================================
# SUPPRESSION D'UN ÉVÉNEMENT
# ==========================================================

@app.route(
    "/delete_event/<int:event_id>",
    methods=["POST"]
)
def delete_event(event_id):

    if not session.get("logged"):

        return redirect(
            url_for("home")
        )

    famille_id = session.get(
        "famille_id"
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT type_evenement
        FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            event_id,
            famille_id
        )
    )

    evenement = cursor.fetchone()

    if evenement is None:

        conn.close()

        flash(
            "Événement introuvable."
        )

        return redirect(
            url_for("calendar")
        )

    if evenement["type_evenement"] == "garde_partagee":

        conn.close()

        flash(
            "Les gardes partagées sont générées automatiquement."
        )

        return redirect(
            url_for("calendar")
        )

    cursor.execute(
        """
        DELETE FROM evenements
        WHERE id = ?
        AND famille_id = ?
        """,
        (
            event_id,
            famille_id
        )
    )

    conn.commit()
    conn.close()

    flash(
        "Événement supprimé."
    )

    return redirect(
        url_for("calendar")
    )

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
# IMPORT CALENDRIER EXTERNE
# ==========================================================

@app.route("/calendar/import")
def calendar_import():

    if not calendar_access_required():

        return redirect(
            url_for("home")
        )

    return render_template(
        "calendar_import.html"
    )

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



