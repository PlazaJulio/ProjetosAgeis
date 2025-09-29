# Onboarding WhatsApp Agent — P1 (MVP)

> Objetivo: um agente **simples** que recebe perguntas de onboarding via WhatsApp (Twilio/Meta), lê um **PDF → texto** com FAQs internas, consulta o **banco SQL** para reconhecer o cargo/validação do colaborador e responde via **LLM (OpenRouter)**. Código mínimo, arquivos pequenos, cada um explicado.

---

## 📁 Estrutura do projeto

```
onboarding-agent/
├─ app.py                 # Flask API: webhook WhatsApp + rota de teste local
├─ llm.py                 # Cliente OpenRouter (chat completions)
├─ pdf_ingest.py          # Conversão PDF → texto (cache em .txt)
├─ db.py                  # Acesso MySQL (users/cargos)
├─ config.py              # Carrega variáveis de ambiente (.env)
├─ prompts/
│  └─ system_prompt.txt   # Instruções do agente (tom e limites)
├─ requirements.txt       # Dependências Python
├─ .env.example           # Modelo das variáveis de ambiente
├─ README.md              # Passo a passo para rodar e testar
└─ data/
   └─ faq.pdf             # (coloque aqui o PDF de onboarding)
```

---

## 🔑 requirements.txt

```txt
flask==3.0.3
requests==2.32.3
python-dotenv==1.0.1
PyPDF2==3.0.1
pymysql==1.1.1
```

**O que é**: lista simples de libs. Flask (API), requests (HTTP), dotenv (carregar .env), PyPDF2 (extrair texto), PyMySQL (banco).

---

## 🔐 .env.example

```env
# OpenRouter
OPENROUTER_API_KEY=coloque_sua_chave_aqui
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet

# PDF
PDF_PATH=./data/faq.pdf
PDF_TXT_CACHE=./data/faq.txt

# DB (preencha se quiser validar usuário/cargo já na P1)
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=senha
DB_NAME=Projetos

# App
PORT=8080
```

**O que é**: variáveis que o código lê. Copie para `.env` e ajuste.

---

## ⚙️ config.py

```python
from dotenv import load_dotenv
import os

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

PDF_PATH = os.getenv("PDF_PATH", "./data/faq.pdf")
PDF_TXT_CACHE = os.getenv("PDF_TXT_CACHE", "./data/faq.txt")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.geten
```
