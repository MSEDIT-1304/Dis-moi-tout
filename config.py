
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Base locale ou base persistante Render Disk
LOCAL_DATABASE = os.path.join(
    BASE_DIR,
    "database",
    "dismoitout.db"
)

DATABASE = os.environ.get(
    "DATABASE_PATH",
    LOCAL_DATABASE
)

os.makedirs(os.path.dirname(DATABASE), exist_ok=True)

# Clé secrète Flask
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "cle-provisoire-de-developpement"
)
