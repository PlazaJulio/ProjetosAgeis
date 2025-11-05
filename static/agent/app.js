// static/agent/app.js
(function () {
  'use strict';

  // ------- Sessão / rota base -------
  const API_BASE = window.location.origin;                // ex.: http://127.0.0.1:8080
  const LOGIN_URL = '/static/login/index.html';

  // Tenta recuperar o usuário logado (salvo pelo login)
  let currentUser = null;
  try {
    const raw = sessionStorage.getItem('onboardly_user');
    if (raw) currentUser = JSON.parse(raw);
  } catch (_) {}

  // Se não estiver logado -> volta para o login
  if (!currentUser || !currentUser.id) {
    window.location.replace(LOGIN_URL);
    return;
  }

  // ------- DOM -------
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const list  = document.getElementById('messages');
  const sendBtn = document.getElementById('sendBtn');
  const headerUser = document.getElementById('headerUser'); // opcional, exibe nome no topo

  if (headerUser) headerUser.textContent = currentUser.nome || currentUser.email || 'Você';

  // ------- Helpers UI -------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function appendMessage(role, text) {
    const item = el('div', `msg ${role}`);
    const bubble = el('div', 'bubble');
    bubble.innerText = text;
    item.appendChild(bubble);
    list.appendChild(item);
    list.scrollTop = list.scrollHeight;
  }

  let typingRow = null;
  function showTyping() {
    if (typingRow) return;
    typingRow = el('div', 'msg bot');
    const bubble = el('div', 'bubble typing');
    bubble.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    typingRow.appendChild(bubble);
    list.appendChild(typingRow);
    list.scrollTop = list.scrollHeight;
  }
  function hideTyping() {
    if (!typingRow) return;
    typingRow.remove();
    typingRow = null;
  }

  function setLoading(loading) {
    if (!sendBtn) return;
    sendBtn.disabled = loading;
  }

  // ------- Envio -------
  async function sendMessage(text) {
    if (!text || !text.trim()) return;
    appendMessage('user', text.trim());
    input.value = '';
    showTyping();
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          // IMPORTANTE: também enviamos no body, mas manter no header facilita logs/filters no backend
          'X-User-Id': String(currentUser.id),
        },
        body: JSON.stringify({
          body: text.trim(),
          user_id: currentUser.id, // o backend lê daqui também
        }),
      });

      let data = null;
      try { data = await res.json(); } catch (_) {}

      if (!res.ok) {
        const msg = (data && (data.answer || data.error || data.message)) ||
                    'Erro ao falar com o servidor.';
        appendMessage('bot', msg);
        return;
      }

      const answer = (data && (data.answer || data.message)) || '…';
      appendMessage('bot', answer);
    } catch (err) {
      console.error('CHAT /chat error:', err);
      appendMessage('bot', 'Erro ao falar com o servidor. Veja o console.');
    } finally {
      hideTyping();
      setLoading(false);
    }
  }

  // ------- Eventos -------
  if (form) {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      sendMessage(input.value);
    });
  }
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input.value);
      }
    });
  }

  // Mensagem de boas-vindas
  appendMessage('bot', 'Olá! Sou o assistente interno. Pergunte sobre onboarding, setores, benefícios e processos.');
})();