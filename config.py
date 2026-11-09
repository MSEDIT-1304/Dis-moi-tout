
import os

# Configuration de l'application
SECRET_KEY = os.environ.get("SECRET_KEY", "cle-secrete-a-remplacer-en-production")

# Base de données SQLite
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database", "dismoitout.db")

# Configuration des sessions
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# Configuration des fichiers envoyés
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 Mo

# Extensions autorisées
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "ics"}

