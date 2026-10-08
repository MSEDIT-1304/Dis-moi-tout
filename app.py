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
    WEBHOOK_URL,
    SHEET_ID,
    PRICE_TVAC,
    TRIAL_LINK,
    STRIPE_LINK,
    SECRET_KEY
)

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

def send_to_webhook(username):

    data = {

        "username": username,

        "trial": True,

        "price": 0

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

