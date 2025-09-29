import os
from dotenv import load_dotenv

# Carrega o arquivo .env
load_dotenv()

# Flask
PORT = int(os.getenv("PORT", 8080))

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")

# MySQL
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASS", "")   # no .env usamos DB_PASS
DB_NAME = os.getenv("DB_NAME", "Projetos")
DB_PORT = int(os.getenv("DB_PORT", 3306))

# PDF / FAQ
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_PATH = os.getenv("PDF_PATH", os.path.join(BASE_DIR, "data/faq.pdf"))
PDF_TXT_CACHE = os.getenv("PDF_TXT_CACHE", os.path.join(BASE_DIR, "data/faq.txt"))