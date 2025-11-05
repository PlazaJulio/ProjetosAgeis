(() => {
  const el = (sel) => document.querySelector(sel);
  const chat = el('#chat');
  const input = el('#input');
  const sendBtn = el('#send');
  const statusEl = el('#status');
  const form = el('#chat-form');

  // ——— Util
  const now = () => new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const append = (role, text, thinking=false) => {
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}${thinking ? ' thinking' : ''}`;
    wrap.innerHTML = `<div class="bubble">${escapeHtml(text)}</div><div class="time">${now()}</div>`;
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
    return wrap;
  };
  const escapeHtml = (s='') => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const setStatus = (txt, cls='') => {
    statusEl.className = `status badge ${cls}`.trim();
    statusEl.textContent = txt;
  };
  const autoresize = () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
  };

  // ——— Health check
  const health = async () => {
    try {
      const r = await fetch('/healthz', {cache:'no-store'});
      if (!r.ok) throw 0;
      const j = await r.json();
      setStatus(j.base_loaded ? 'base: ok' : 'base: não carregada', j.base_loaded ? 'ok' : 'warn');
    } catch {
      setStatus('offline', 'warn');
    }
  };

  // ——— Envio
  const send = async () => {
    const text = (input.value || '').trim();
    if (!text) return;
    sendBtn.disabled = true;

    append('user', text);
    input.value = '';
    autoresize();

    const thinking = append('bot', '<span class="typing">pensando…</span>', true);

    try {
      const r = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ body: text })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      thinking.remove();
      append('bot', j.answer || 'Sem resposta.');
    } catch (err) {
      thinking.remove();
      append('bot', 'Erro ao falar com o servidor. Veja o console.');
      console.error(err);
    } finally {
      sendBtn.disabled = false;
      input.focus();
    }
  };

  // ——— Ligações de eventos
  // 1) Evita reload se alguém pressionar Enter e o form tentar submeter
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    send();
  });

  // 2) Botão envia (type="button" também evita submit)
  sendBtn.addEventListener('click', send);

  // 3) Enter envia / Shift+Enter quebra linha
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  input.addEventListener('input', autoresize);
  autoresize();

  // Start
  health();
  setInterval(health, 15000);
})();