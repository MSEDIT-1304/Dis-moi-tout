import sqlite3
from datetime import datetime
import pandas as pd
import requests
import streamlit as st
import bcrypt

# ==========================================================
# CONFIGURATION
# ==========================================================

st.set_page_config(
    page_title="Dis-moi tout",
    page_icon="🗓️",
    layout="wide"
)

WEBHOOK_URL = "https://hook.eu1.make.com/942mf8fk2jehv637xc3s0tsjsxrad0gu"
SHEET_ID = "1JWwwLP3IKaG-ELsC3li84eouOFVFnv_C5MxBDQSfz3M"

PRICE_TVAC = 15

TRIAL_LINK = "https://buy.stripe.com/00w28s2wifc1cmn7PW9fW0g"
STRIPE_LINK = "https://buy.stripe.com/3cI3cw6My0h7gCD3zG9fW0h"

DATABASE = "database/dismoitout.db"

# ==========================================================
# BASE DE DONNÉES SQLITE
# ==========================================================

def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================================
# CRÉATION DES TABLES
# ==========================================================

def init_database():
    conn = get_connection()
    cursor = conn.cursor()

    # Administrateur
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Famille
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS famille (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        nom_famille TEXT,
        FOREIGN KEY(admin_id) REFERENCES admin(id)
    )
    """)

    # Membres
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS membres (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        famille_id INTEGER,
        prenom TEXT,
        nom TEXT,
        email TEXT,
        password TEXT,
        role TEXT,
        peut_modifier INTEGER DEFAULT 1,
        FOREIGN KEY(famille_id) REFERENCES famille(id)
    )
    """)

    # Événements
    cursor.execute("""
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(famille_id) REFERENCES famille(id),
        FOREIGN KEY(membre_id) REFERENCES membres(id)
    )
    """)

    conn.commit()
    conn.close()


init_database()

# ==========================================================
# GOOGLE SHEETS / WEBHOOK / VÉRIFICATION D'ACCÈS
# ==========================================================

def load_users():
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

    df = pd.read_csv(url)

    df["username"] = df["username"].astype(str).str.strip()
    df["password"] = df["password"].astype(str).str.strip()
    df["expire"] = pd.to_datetime(df["expire"], errors="coerce")

    return df


def check_login(username):

    df = load_users()

    user = df[
        df["username"].astype(str).str.strip()
        == str(username).strip()
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

        if str(user.iloc[0]["trial"]).strip().upper() == "TRUE":
            return "trial_expired"

        return "subscription_expired"

    return "ok"


def send_to_webhook(username):

    data = {
        "username": username,
        "trial": True,
        "price": 0
    }

    try:
        requests.post(WEBHOOK_URL, json=data, timeout=10)
    except:
        pass

  # ==========================================================
# VARIABLES DE SESSION
# ==========================================================

if "logged" not in st.session_state:
    st.session_state.logged = False

if "admin_logged" not in st.session_state:
    st.session_state.admin_logged = False

if "admin_id" not in st.session_state:
    st.session_state.admin_id = None

if "famille_id" not in st.session_state:
    st.session_state.famille_id = None

if "membre_id" not in st.session_state:
    st.session_state.membre_id = None

if "user_type" not in st.session_state:
    st.session_state.user_type = None

if "user_name" not in st.session_state:
    st.session_state.user_name = ""

if "reset_id" not in st.session_state:
    st.session_state.reset_id = 0

if st.session_state.admin_logged:
    st.session_state.logged = True

# ==========================================================
# AUTHENTIFICATION SQLITE
# ==========================================================

def hash_password(password):
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    )


def verify_password(password, hashed_password):
    return bcrypt.checkpw(
        password.encode("utf-8"),
        hashed_password
    )


def get_admin_by_email(email):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM admin WHERE email = ?",
        (email,)
    )

    admin = cursor.fetchone()

    conn.close()

    return admin


def create_admin(nom, prenom, email, password):

    conn = get_connection()
    cursor = conn.cursor()

    hashed = hash_password(password)

    try:

        cursor.execute(
            """
            INSERT INTO admin
            (nom, prenom, email, password)
            VALUES (?, ?, ?, ?)
            """,
            (
                nom,
                prenom,
                email,
                hashed
            )
        )

        conn.commit()

        return True

    except sqlite3.IntegrityError:

        return False

    finally:

        conn.close()

# ==========================================================
# CRÉATION DU COMPTE ADMINISTRATEUR
# ==========================================================

if not st.session_state.logged:

    st.title("Dis-moi tout")

    st.subheader("Créer un compte")

    prenom = st.text_input("Prénom")
    nom = st.text_input("Nom")
    email = st.text_input("Adresse e-mail")
    password = st.text_input("Mot de passe", type="password")
    password2 = st.text_input("Confirmer le mot de passe", type="password")

    if st.button("Créer mon compte"):

        if not prenom.strip():
            st.error("Veuillez saisir votre prénom.")

        elif not nom.strip():
            st.error("Veuillez saisir votre nom.")

        elif not email.strip():
            st.error("Veuillez saisir votre adresse e-mail.")

        elif password == "":
            st.error("Veuillez choisir un mot de passe.")

        elif password != password2:
            st.error("Les mots de passe sont différents.")

        elif get_admin_by_email(email):

            st.error("Cette adresse e-mail existe déjà.")

        else:

            if create_admin(
                nom,
                prenom,
                email,
                password
            ):

                send_to_webhook(email)

                st.success("Compte créé avec succès.")

                st.markdown(
                    f"[👉 Cliquez ici pour activer votre essai gratuit de 7 jours]({TRIAL_LINK})"
                )

                st.stop()

# ==========================================================
# CONNEXION
# ==========================================================

    st.markdown("---")

    st.subheader("Connexion")

    login_email = st.text_input("Adresse e-mail")

    login_password = st.text_input(
        "Mot de passe",
        type="password",
        key="login_password"
    )

    if st.button("Se connecter"):

        result = check_login(login_email)

        if result == "error":
            st.error("Compte introuvable.")

        elif result == "trial_expired":

            st.error("Votre essai gratuit est terminé.")

            st.markdown(
                f"[👉 Souscrire un abonnement]({STRIPE_LINK})"
            )

        elif result == "subscription_expired":

            st.error("Votre abonnement est expiré.")

            st.markdown(
                f"[👉 Renouveler votre abonnement]({STRIPE_LINK})"
            )

        elif result == "ok":

            admin = get_admin_by_email(login_email)

            if admin is None:

                st.error("Compte introuvable.")

            elif verify_password(
                login_password,
                admin["password"]
            ):

                st.session_state.logged = True
                st.session_state.admin_logged = True
                st.session_state.admin_id = admin["id"]
                st.session_state.user_name = (
                    f"{admin['prenom']} {admin['nom']}"
                )

                st.rerun()

            else:

                st.error("Mot de passe incorrect.")

# ==========================================================
# TABLEAU DE BORD ADMINISTRATEUR
# ==========================================================

if st.session_state.logged and st.session_state.admin_logged:

    st.title("Dis-moi tout")

    st.success(
        f"Bienvenue {st.session_state.user_name}"
    )

    st.markdown("---")

    st.subheader("Votre famille")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM famille
        WHERE admin_id = ?
        """,
        (st.session_state.admin_id,)
    )

    famille = cursor.fetchone()

    if famille is None:

        nom_famille = st.text_input(
            "Nom de votre famille"
        )

        if st.button("Créer ma famille"):

            if nom_famille.strip() == "":

                st.error("Veuillez saisir un nom.")

            else:

                cursor.execute(
                    """
                    INSERT INTO famille
                    (admin_id, nom_famille)
                    VALUES (?, ?)
                    """,
                    (
                        st.session_state.admin_id,
                        nom_famille
                    )
                )

                conn.commit()

                st.success("Famille créée.")

                st.rerun()

    else:

        st.success(
            f"Famille : {famille['nom_famille']}"
        )

        st.session_state.famille_id = famille["id"]

    conn.close()

# ==========================================================
# GESTION DES MEMBRES DE LA FAMILLE
# ==========================================================

if st.session_state.logged and st.session_state.admin_logged:

    st.markdown("---")
    st.subheader("Membres de la famille")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom
        """,
        (st.session_state.famille_id,)
    )

    membres = cursor.fetchall()

    st.write(f"Nombre de membres : {len(membres)} / 6")

    if len(membres) < 6:

        with st.expander("➕ Ajouter un membre"):

            prenom = st.text_input("Prénom", key="new_prenom")
            nom = st.text_input("Nom", key="new_nom")
            email = st.text_input("Adresse e-mail (facultatif)", key="new_email")
            password = st.text_input(
                "Mot de passe",
                type="password",
                key="new_password"
            )

            peut_modifier = st.checkbox(
                "Autoriser ce membre à modifier/supprimer ses événements",
                value=True
            )

            if st.button("Ajouter le membre"):

                if not prenom.strip():
                    st.error("Veuillez saisir un prénom.")

                elif not nom.strip():
                    st.error("Veuillez saisir un nom.")

                elif password == "":
                    st.error("Veuillez saisir un mot de passe.")

                else:

                    hashed = hash_password(password)

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
                            st.session_state.famille_id,
                            prenom,
                            nom,
                            email,
                            hashed,
                            "membre",
                            1 if peut_modifier else 0
                        )
                    )

                    conn.commit()

                    st.success("Membre ajouté.")

                    st.rerun()

    st.markdown("---")

    if membres:

        for membre in membres:

            col1, col2, col3 = st.columns([4,2,1])

            with col1:

                st.write(
                    f"**{membre['prenom']} {membre['nom']}**"
                )

                if membre["email"]:
                    st.caption(membre["email"])

            with col2:

                if membre["peut_modifier"]:
                    st.success("Modification autorisée")
                else:
                    st.warning("Lecture seule")

            with col3:

                if st.button(
                    "Supprimer",
                    key=f"delete_{membre['id']}"
                ):

                    cursor.execute(
                        "DELETE FROM membres WHERE id=?",
                        (membre["id"],)
                    )

                    conn.commit()

                    st.rerun()

    conn.close()

# ==========================================================
# CALENDRIER - AJOUT D'ÉVÉNEMENTS
# ==========================================================

if st.session_state.logged:

    st.markdown("---")
    st.header("📅 Calendrier")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, prenom, nom
        FROM membres
        WHERE famille_id = ?
        ORDER BY prenom
    """, (st.session_state.famille_id,))

    membres = cursor.fetchall()

    liste_membres = {
        f"{m['prenom']} {m['nom']}": m["id"]
        for m in membres
    }

    destinataire = st.selectbox(
        "Attribuer l'événement à",
        list(liste_membres.keys())
    )

    titre = st.text_input("Titre")

    description = st.text_area("Description")

    date = st.date_input("Date")

    heure = st.time_input("Heure")

    calendrier = st.selectbox(
        "Calendrier",
        [
            "Personnel",
            "Famille",
            "Professionnel"
        ]
    )

    rappel = st.checkbox(
        "Activer un rappel",
        value=True
    )

    if st.button("Ajouter l'événement"):

        cursor.execute("""
            INSERT INTO evenements
            (
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
        """,
        (
            st.session_state.famille_id,
            liste_membres[destinataire],
            titre,
            description,
            str(date),
            str(heure),
            calendrier,
            1 if rappel else 0
        ))

        conn.commit()

        st.success("Événement enregistré.")

        st.rerun()

    st.markdown("---")
    st.subheader("Événements enregistrés")

    cursor.execute("""
        SELECT
            e.*,
            m.prenom,
            m.nom
        FROM evenements e
        JOIN membres m
            ON e.membre_id = m.id
        WHERE e.famille_id = ?
        ORDER BY e.date, e.heure
    """,
    (st.session_state.famille_id,))

    evenements = cursor.fetchall()

    if not evenements:

        st.info("Aucun événement.")

    else:

        for evt in evenements:

            st.markdown(f"""
**{evt['titre']}**

👤 {evt['prenom']} {evt['nom']}

📅 {evt['date']} à {evt['heure']}

🗂️ {evt['calendrier']}

📝 {evt['description']}

---
""")

    conn.close()

# ==========================================================
# CONNEXION MEMBRE
# ==========================================================

def get_member(identifier):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE email = ?
        """,
        (identifier,)
    )

    membre = cursor.fetchone()

    if membre is None:

        morceaux = identifier.strip().split()

        if len(morceaux) >= 2:

            prenom = morceaux[0]
            nom = " ".join(morceaux[1:])

            cursor.execute(
                """
                SELECT *
                FROM membres
                WHERE prenom = ?
                AND nom = ?
                """,
                (prenom, nom)
            )

            membre = cursor.fetchone()

    conn.close()

    return membre


def login_member(identifier, password):

    membre = get_member(identifier)

    if membre is None:
        return None

    if verify_password(
        password,
        membre["password"]
    ):

        return membre

    return None


st.markdown("---")
st.subheader("Connexion membre")

member_login = st.text_input(
    "Adresse e-mail ou Prénom Nom",
    key="member_login"
)

member_password = st.text_input(
    "Mot de passe",
    type="password",
    key="member_password"
)

if st.button("Connexion membre"):

    membre = login_member(
        member_login,
        member_password
    )

    if membre:

        st.session_state.logged = True
        st.session_state.admin_logged = False
        st.session_state.user_type = "member"
        st.session_state.membre_id = membre["id"]
        st.session_state.famille_id = membre["famille_id"]
        st.session_state.user_name = (
            f"{membre['prenom']} {membre['nom']}"
        )

        st.rerun()

    else:

        st.error(
            "Identifiants incorrects."
        )

# ==========================================================
# TABLEAU DE BORD MEMBRE
# ==========================================================

if (
    st.session_state.logged
    and st.session_state.user_type == "member"
):

    st.title("Dis-moi tout")

    st.success(
        f"Bienvenue {st.session_state.user_name}"
    )

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM membres
        WHERE id = ?
        """,
        (st.session_state.membre_id,)
    )

    membre = cursor.fetchone()

    st.markdown("---")
    st.subheader("Mes événements")

    cursor.execute(
        """
        SELECT *
        FROM evenements
        WHERE membre_id = ?
        ORDER BY date, heure
        """,
        (st.session_state.membre_id,)
    )

    evenements = cursor.fetchall()

    if not evenements:

        st.info("Aucun événement.")

    else:

        for evt in evenements:

            st.markdown(f"""
### {evt['titre']}

📅 {evt['date']} à {evt['heure']}

🗂️ {evt['calendrier']}

📝 {evt['description']}
""")

            if membre["peut_modifier"]:

                if st.button(
                    f"Supprimer",
                    key=f"sup_evt_{evt['id']}"
                ):

                    cursor.execute(
                        """
                        DELETE FROM evenements
                        WHERE id = ?
                        """,
                        (evt["id"],)
                    )

                    conn.commit()

                    st.rerun()

            st.markdown("---")

    conn.close()

# ==========================================================
# AJOUT RAPIDE D'UN ÉVÉNEMENT
# ==========================================================

if st.session_state.logged:

    st.markdown("---")
    st.subheader("⚡ Ajout rapide")

    texte = st.text_input(
        "Exemple : Léa mardi 15h dentiste",
        key="quick_event"
    )

    if st.button("Analyser", key="analyse_evenement"):

        if texte.strip() == "":

            st.warning("Veuillez saisir une phrase.")

        else:

            st.info(
                "L'analyse intelligente sera disponible dans le module IA."
            )

            st.write("Texte reçu :")
            st.code(texte)

# ==========================================================
# DÉCONNEXION
# ==========================================================

if st.session_state.logged:

    st.markdown("---")

    if st.button("🚪 Se déconnecter"):

        st.session_state.logged = False
        st.session_state.admin_logged = False

        st.session_state.admin_id = None
        st.session_state.famille_id = None
        st.session_state.membre_id = None

        st.session_state.user_type = None
        st.session_state.user_name = ""

        st.rerun()

# ==========================================================
# PARAMÈTRES
# ==========================================================

if st.session_state.logged:

    st.markdown("---")
    st.header("⚙️ Paramètres")

    onglet1, onglet2, onglet3 = st.tabs([
        "Profil",
        "Préférences",
        "Sécurité"
    ])

    # ------------------------------------------------------

    with onglet1:

        st.subheader("Mon profil")

        st.write("Nom :", st.session_state.user_name)

        st.write("Type de compte :")

        if st.session_state.admin_logged:
            st.success("Administrateur")
        else:
            st.info("Membre")

    # ------------------------------------------------------

    with onglet2:

        st.subheader("Préférences")

        notifications = st.checkbox(
            "Recevoir les rappels",
            value=True
        )

        st.checkbox(
            "Afficher les anniversaires",
            value=True
        )

        st.checkbox(
            "Afficher les tâches",
            value=True
        )

        st.checkbox(
            "Afficher les événements passés",
            value=False
        )

        if st.button(
            "Enregistrer les préférences"
        ):

            st.success(
                "Préférences enregistrées."
            )

    # ------------------------------------------------------

    with onglet3:

        st.subheader("Sécurité")

        ancien = st.text_input(
            "Mot de passe actuel",
            type="password",
            key="old_pwd"
        )

        nouveau = st.text_input(
            "Nouveau mot de passe",
            type="password",
            key="new_pwd"
        )

        confirmation = st.text_input(
            "Confirmer",
            type="password",
            key="confirm_pwd"
        )

        if st.button(
            "Modifier le mot de passe"
        ):

            st.info(
                "Fonction disponible dans le prochain module."
            )

# ==========================================================
# INITIALISATION
# ==========================================================

def main():
    pass


if __name__ == "__main__":
    main()





