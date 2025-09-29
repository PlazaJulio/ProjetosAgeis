import os
from PyPDF2 import PdfReader
from config import PDF_PATH, PDF_TXT_CACHE

def load_knowledge_text() -> str:
    """
    Carrega o texto do cache .txt se existir; senão, extrai do PDF e salva o cache.
    Retorna o conteúdo como uma string única.
    """
    
    if PDF_TXT_CACHE and os.path.exists(PDF_TXT_CACHE):
        with open(PDF_TXT_CACHE, "r", encoding="utf-8") as f:
            return f.read()

  
    if not os.path.exists(PDF_PATH):
       
        return ""

    reader = PdfReader(PDF_PATH)
    pages_text = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages_text.append(txt)

    full_text = "\n\n".join(pages_text).strip()

    
    if PDF_TXT_CACHE:
        os.makedirs(os.path.dirname(PDF_TXT_CACHE), exist_ok=True)
        with open(PDF_TXT_CACHE, "w", encoding="utf-8") as f:
            f.write(full_text)

    return full_text