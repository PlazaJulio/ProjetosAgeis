import requests
from typing import List, Dict
from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def chat(messages: List[Dict], temperature: float = 0.2, max_tokens: int = 500) -> str:
    """Envia lista de mensagens para o modelo no OpenRouter e retorna o texto da resposta."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",   # pode deixar fixo
        "X-Title": "OnboardingAgent-P1",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()