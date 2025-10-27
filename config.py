# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Carrega .env a partir da mesma pasta do config.py
load_dotenv(BASE_DIR / ".env")

# Flask
PORT = int(os.getenv("PORT", 8080))

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL  = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

# MySQL
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASS", "")   # importante: lê DB_PASS do .env
DB_NAME = os.getenv("DB_NAME", "Projetos")
DB_PORT = int(os.getenv("DB_PORT", 3306))

# PDF / FAQ
PDF_PATH = os.getenv("PDF_PATH", str(BASE_DIR / "data" / "FAQ.pdf"))
PDF_TXT_CACHE = os.getenv("PDF_TXT_CACHE", str(BASE_DIR / "data" / "FAQ.txt"))

# Log rápido (não imprime a senha)
print(f"[config] DB_HOST={DB_HOST} DB_USER={DB_USER} DB_NAME={DB_NAME} PASS_SET={bool(DB_PASSWORD)}")