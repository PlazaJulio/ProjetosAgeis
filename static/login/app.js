(function () {
  'use strict';

  const form = document.getElementById('loginForm');
  const emailEl = document.getElementById('email');
  const passEl  = document.getElementById('password');
  const emailErr = document.getElementById('emailError');
  const passErr  = document.getElementById('passwordError');
  const submitBtn = document.getElementById('submitBtn');
  const spinner = submitBtn.querySelector('.spinner');
  const btnText = submitBtn.querySelector('.btn-text');
  const togglePassword = document.getElementById('togglePassword');
  const toast = document.getElementById('toast');
  const modeToggle = document.getElementById('modeToggle');
  const remember = document.getElementById('remember');
  const loginSso = document.getElementById('loginSso');
  const forgotLink = document.getElementById('forgotLink');

  // >>>>> BACKEND / ROTAS <<<<<
  const API_BASE = window.location.origin; // ex: http://127.0.0.1:8080
  const REDIRECT_AFTER_LOGIN = true;       // defina false se NÃO quiser ir ao agente
  const REDIRECT_URL = '/static/agent/index.html';

  // ---------------- Tema ----------------
  const THEME_KEY = 'onboardly_theme';
  const savedTheme = localStorage.getItem(THEME_KEY) || 'dark';
  if (savedTheme === 'light') enableLight();
  else enableDark();

  if (modeToggle) {
    modeToggle.addEventListener('click', () => {
      if (document.documentElement.dataset.theme === 'light') enableDark();
      else enableLight();
    });
  }

  function enableLight() {
    document.documentElement.dataset.theme = 'light';
    if (modeToggle) modeToggle.textContent = '🌙';
    document.documentElement.style.cssText = `
      --bg: #f3f5fb;
      --bg-soft: rgba(10,20,40,0.02);
      --card: rgba(255,255,255,0.7);
      --stroke: rgba(10,20,40,0.12);
      --text: #111827;
      --muted: #4b5563;
      --primary: #2a69ff;
      --primary-strong:#2758d8;
      --danger:#e11d48;
    `;
    localStorage.setItem(THEME_KEY, 'light');
  }
  function enableDark() {
    document.documentElement.dataset.theme = 'dark';
    if (modeToggle) modeToggle.textContent = '🌙';
    document.documentElement.style.cssText = ``; // volta para o :root padrão do CSS
    localStorage.setItem(THEME_KEY, 'dark');
  }

  // ----------- Mostrar/ocultar senha -----------
  if (togglePassword) {
    togglePassword.addEventListener('click', () => {
      const type = passEl.getAttribute('type') === 'password' ? 'text' : 'password';
      passEl.setAttribute('type', type);
      togglePassword.textContent = type === 'password' ? '👁' : '🙈';
    });
  }

  // ------------- Lembre-me (front only) -------------
  const REMEMBER_KEY = 'onboardly_login_email';
  const savedEmail = localStorage.getItem(REMEMBER_KEY);
  if (savedEmail) {
    emailEl.value = savedEmail;
    if (remember) remember.checked = true;
  }

  // ---------------- Validação ----------------
  function validateEmail(value) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(String(value).trim());
  }
  function validatePassword(value) {
    return String(value).trim().length >= 6;
  }

  function showToast(msg, ok = true) {
    if (!toast) return;
    toast.textContent = msg;
    toast.style.borderColor = ok ? '#1d4ed8' : '#b91c1c';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2200);
  }

  function setLoading(loading) {
    if (submitBtn) submitBtn.disabled = loading;
    if (spinner) spinner.style.display = loading ? 'inline-block' : 'none';
    if (btnText) btnText.textContent = loading ? 'Entrando…' : 'Entrar';
  }

  // ----------------- Submit -----------------
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      emailErr.textContent = '';
      passErr.textContent  = '';

      const email = emailEl.value;
      const senha = passEl.value;

      let ok = true;
      if (!validateEmail(email)) {
        emailErr.textContent = 'Informe um e-mail válido.';
        ok = false;
      }
      if (!validatePassword(senha)) {
        passErr.textContent = 'A senha deve ter pelo menos 6 caracteres.';
        ok = false;
      }
      if (!ok) return;

      if (remember && remember.checked) localStorage.setItem(REMEMBER_KEY, email);
      else localStorage.removeItem(REMEMBER_KEY);

      try {
        setLoading(true);
        const res = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, senha }),
          mode: 'cors',
          credentials: 'omit'
        });

        let data = null;
        try { data = await res.json(); } catch (_) { data = null; }

        if (!res.ok) {
          const msg = (data && data.message) || 'Falha no login.';
          if (data && data.field === 'email') emailErr.textContent = msg;
          else if (data && data.field === 'senha') passErr.textContent = msg;
          else showToast(msg, false);
          return;
        }

        if (data && data.success) {
          try { sessionStorage.setItem('onboardly_user', JSON.stringify(data.user)); } catch (_) {}
          showToast(`Bem-vindo(a), ${data.user?.nome || 'usuário'}!`, true);

          if (REDIRECT_AFTER_LOGIN) {
            setTimeout(() => {
              // usa replace para evitar voltar ao login no histórico
              window.location.replace(REDIRECT_URL);
            }, 600);
          }
        } else {
          showToast((data && data.message) || 'Falha no login.', false);
        }
      } catch (err) {
        console.error('Login error:', err);
        showToast('Não foi possível conectar ao servidor.', false);
      } finally {
        setLoading(false);
      }
    });
  }

  // ------------- Botões auxiliares -------------
  if (loginSso) {
    loginSso.addEventListener('click', () => {
      showToast('SSO ainda não configurado.', false);
    });
  }
  if (forgotLink) {
    forgotLink.addEventListener('click', (e) => {
      e.preventDefault();
      showToast('Fluxo de recuperação ainda não disponível.', false);
    });
  }
})();