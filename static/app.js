(function () {
  'use strict';

  const STATUS_LABELS = {
    queued: 'Na fila...',
    extracting_audio: 'Extraindo áudio...',
    uploading_audio: 'Enviando áudio para transcrição...',
    transcribing: 'Transcrevendo... (pode levar alguns minutos)',
    cutting: 'Cortando clipes...',
    reframing: 'Reformatando para 9:16...',
    uploading_clips: 'Enviando clipes...',
    done: 'Pronto!',
    error: 'Erro no processamento.',
  };

  let pollTimer = null;
  let currentJobId = null;

  // Seções
  const authSection = document.getElementById('auth-section');
  const changeSection = document.getElementById('change-section');
  const appSection = document.getElementById('app-section');

  // Login
  const loginForm = document.getElementById('login-form');
  const loginBtn = document.getElementById('login-btn');
  const loginError = document.getElementById('login-error');

  // Troca de senha
  const changeForm = document.getElementById('change-form');
  const changeBtn = document.getElementById('change-btn');
  const changeError = document.getElementById('change-error');

  // App
  const userEmail = document.getElementById('user-email');
  const logoutBtn = document.getElementById('logout-btn');
  const form = document.getElementById('upload-form');
  const submitBtn = document.getElementById('submit-btn');
  const uploadError = document.getElementById('upload-error');
  const uploadSection = document.getElementById('upload-section');
  const progressSection = document.getElementById('progress-section');
  const filenameLabel = document.getElementById('filename-label');
  const progressBar = document.getElementById('progress-bar');
  const progressPct = document.getElementById('progress-pct');
  const statusLabel = document.getElementById('status-label');
  const newVideoBtn = document.getElementById('new-video-btn');
  const resultsSection = document.getElementById('results-section');
  const clipsList = document.getElementById('clips-list');

  // ─── Roteamento de telas ────────────────────────────────────────────────

  function show(section) {
    authSection.classList.add('hidden');
    changeSection.classList.add('hidden');
    appSection.classList.add('hidden');
    section.classList.remove('hidden');
  }

  async function boot() {
    try {
      const resp = await fetch('/api/me');
      if (resp.status === 401) { show(authSection); return; }
      const me = await resp.json();
      userEmail.textContent = me.email || '';
      if (me.must_change_password) { show(changeSection); }
      else { show(appSection); }
    } catch (_) {
      show(authSection);
    }
  }

  // ─── Login ──────────────────────────────────────────────────────────────

  loginForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    hide(loginError);
    loginBtn.disabled = true;
    loginBtn.textContent = 'Entrando...';
    try {
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: document.getElementById('login-email').value,
          password: document.getElementById('login-password').value,
        }),
      });
      const json = await resp.json();
      if (!resp.ok) { showMsg(loginError, json.error || 'Falha no login.'); return; }
      userEmail.textContent = json.email || '';
      loginForm.reset();
      if (json.must_change_password) { show(changeSection); } else { show(appSection); }
    } catch (_) {
      showMsg(loginError, 'Falha de conexão. Tente novamente.');
    } finally {
      loginBtn.disabled = false;
      loginBtn.textContent = 'Entrar';
    }
  });

  // ─── Troca de senha ───────────────────────────────────────────────────────

  changeForm.addEventListener('submit', async function (e) {
    e.preventDefault();
    hide(changeError);
    const cur = document.getElementById('cur-password').value;
    const n1 = document.getElementById('new-password').value;
    const n2 = document.getElementById('new-password2').value;
    if (n1 !== n2) { showMsg(changeError, 'As senhas novas não coincidem.'); return; }
    if (n1.length < 8) { showMsg(changeError, 'A nova senha precisa ter ao menos 8 caracteres.'); return; }

    changeBtn.disabled = true;
    changeBtn.textContent = 'Salvando...';
    try {
      const resp = await fetch('/api/change_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: cur, new_password: n1 }),
      });
      const json = await resp.json();
      if (!resp.ok) { showMsg(changeError, json.error || 'Falha ao trocar a senha.'); return; }
      changeForm.reset();
      show(appSection);
    } catch (_) {
      showMsg(changeError, 'Falha de conexão. Tente novamente.');
    } finally {
      changeBtn.disabled = false;
      changeBtn.textContent = 'Salvar nova senha';
    }
  });

  // ─── Logout ───────────────────────────────────────────────────────────────

  logoutBtn.addEventListener('click', async function () {
    stopPolling();
    try { await fetch('/api/logout', { method: 'POST' }); } catch (_) {}
    resetUI();
    show(authSection);
  });

  // ─── Upload ───────────────────────────────────────────────────────────────

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    hide(uploadError);

    const minD = parseInt(document.getElementById('min-duration').value, 10);
    const maxD = parseInt(document.getElementById('max-duration').value, 10);
    if (minD >= maxD) { showMsg(uploadError, 'A duração mínima deve ser menor que a máxima.'); return; }

    const fileInput = document.getElementById('video-file');
    if (!fileInput.files.length) { showMsg(uploadError, 'Selecione um vídeo.'); return; }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Enviando...';

    const data = new FormData(form);
    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: data });
      if (resp.status === 401) { show(authSection); return; }
      const json = await resp.json();
      if (!resp.ok) { showMsg(uploadError, json.error || 'Erro ao enviar vídeo.'); resetSubmitBtn(); return; }
      currentJobId = json.job_id;
      startPolling(currentJobId, fileInput.files[0].name);
    } catch (err) {
      showMsg(uploadError, 'Falha de conexão ao enviar o vídeo. Tente novamente.');
      resetSubmitBtn();
    }
  });

  newVideoBtn.addEventListener('click', function () { resetUI(); });

  function startPolling(jobId, filename) {
    uploadSection.classList.add('hidden');
    progressSection.classList.remove('hidden');
    resultsSection.classList.add('hidden');
    filenameLabel.textContent = filename;
    setProgress(5, 'queued');
    if (pollTimer) clearInterval(pollTimer);
    poll(jobId);
    pollTimer = setInterval(function () { poll(jobId); }, 30000);
  }

  async function poll(jobId) {
    try {
      const resp = await fetch('/api/status/' + jobId);
      if (resp.status === 401) { stopPolling(); show(authSection); return; }
      if (!resp.ok) return;
      const data = await resp.json();
      setProgress(data.progress || 0, data.status);

      if (data.status === 'done') {
        stopPolling();
        showResults(data.clips || []);
        newVideoBtn.classList.remove('hidden');
      } else if (data.status === 'error') {
        stopPolling();
        statusLabel.textContent = 'Erro: ' + (data.error || 'Falha desconhecida.');
        statusLabel.classList.add('error-text');
        newVideoBtn.classList.remove('hidden');
        if (data.partial_clips && data.partial_clips.length) {
          showResults(data.partial_clips, true);
        }
      }
    } catch (_) { /* network blip — keep polling */ }
  }

  function setProgress(pct, status) {
    progressBar.style.width = pct + '%';
    progressPct.textContent = pct + '%';
    statusLabel.textContent = STATUS_LABELS[status] || status;
    statusLabel.classList.remove('error-text');
  }

  function showResults(urls, partial) {
    resultsSection.classList.remove('hidden');
    clipsList.innerHTML = '';
    if (!urls.length) { clipsList.innerHTML = '<p>Nenhum clipe gerado.</p>'; return; }
    const heading = partial ? '<p class="partial-notice">Clipes parciais (processamento falhou após estes):</p>' : '';
    clipsList.innerHTML = heading + urls.map(function (url, i) {
      return '<a href="' + url + '" class="clip-link" download>Baixar clipe ' + (i + 1) + '</a>';
    }).join('');
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function showMsg(el, msg) { el.textContent = msg; el.classList.remove('hidden'); }
  function hide(el) { el.classList.add('hidden'); }

  function resetSubmitBtn() {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Processar Vídeo';
  }

  function resetUI() {
    stopPolling();
    currentJobId = null;
    form.reset();
    uploadSection.classList.remove('hidden');
    progressSection.classList.add('hidden');
    resultsSection.classList.add('hidden');
    clipsList.innerHTML = '';
    newVideoBtn.classList.add('hidden');
    statusLabel.classList.remove('error-text');
    resetSubmitBtn();
    hide(uploadError);
  }

  boot();
})();
