/* OpusSB · app.js (core)
   Orquestra os fluxos REAIS (login, troca de senha, upload, status/progresso, resultado),
   o roteamento entre telas/views, o envio aprimorado e os atalhos de teclado.
   Depende de: store.js, ui.js, api.js, router.js, views.js (carregados antes). */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});
  var ui = NS.ui, api = NS.api, store = NS.store, router = NS.router, views = NS.views;

  var STATUS_LABELS = {
    queued: 'Na fila...',
    extracting_audio: 'Extraindo áudio...',
    uploading_audio: 'Enviando áudio para transcrição...',
    transcribing: 'Transcrevendo... (pode levar alguns minutos)',
    cutting: 'Cortando clipes...',
    reframing: 'Reformatando para 9:16...',
    uploading_clips: 'Enviando clipes...',
    done: 'Pronto!',
    error: 'Erro no processamento.'
  };

  var POLL_INTERVAL_MS = 5000;
  var MAX_BYTES = 15 * 1024 * 1024 * 1024; // 15GB
  var ACCEPT_EXT = ['mp4', 'mov', 'avi', 'mkv', 'webm'];

  var pollTimer = null;
  var currentJobId = null;
  var routerStarted = false;
  var fileValid = false;

  function byId(id) { return document.getElementById(id); }

  // Telas (screen layer)
  var authSection = byId('auth-section');
  var changeSection = byId('change-section');
  var appSection = byId('app-section');

  // ── Identidade do usuário ─────────────────────────────────────────────
  function firstNameFromEmail(email) {
    if (!email) return 'você';
    var local = (email.split('@')[0] || '');
    var token = (local.split(/[._-]+/).filter(Boolean)[0] || local);
    if (!token) return 'você';
    return token.charAt(0).toUpperCase() + token.slice(1).toLowerCase();
  }
  function initialsFromEmail(email) {
    if (!email) return 'SB';
    var local = (email.split('@')[0] || '');
    var parts = local.split(/[._-]+/).filter(Boolean);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return local.slice(0, 2).toUpperCase() || 'SB';
  }
  function setUser(email) {
    var v = email || '';
    var ue = byId('user-email'); if (ue) ue.textContent = v;
    var un = byId('user-name'); if (un) un.textContent = firstNameFromEmail(v);
    var gn = byId('greeting-name'); if (gn) gn.textContent = firstNameFromEmail(v);
    var av = byId('user-avatar'); if (av) av.textContent = initialsFromEmail(v);
  }

  // ── Camadas de tela / view ────────────────────────────────────────────
  function showScreen(section) {
    [authSection, changeSection, appSection].forEach(function (s) { if (s) s.classList.add('hidden'); });
    if (section) section.classList.remove('hidden');
  }
  function showView(name) {
    var nodes = document.querySelectorAll('.view');
    Array.prototype.forEach.call(nodes, function (v) { v.classList.add('hidden'); });
    var target = byId('view-' + name);
    if (target) target.classList.remove('hidden');
    var main = document.querySelector('.main');
    if (main) main.scrollTop = 0;
  }
  function updateNav() {
    var segs = router.current();
    var base = segs.length ? segs[0] : 'home';
    var active = (base === 'projetos') ? 'projetos' : (base === 'favoritos' ? 'favoritos' : (base === 'config' ? 'config' : 'home'));
    var items = document.querySelectorAll('[data-nav]');
    Array.prototype.forEach.call(items, function (it) {
      if (it.getAttribute('data-nav') === active) it.classList.add('is-active');
      else it.classList.remove('is-active');
    });
  }

  function requireAuth() { stopPolling(); showScreen(authSection); }

  // ── Boot ──────────────────────────────────────────────────────────────
  function boot() {
    registerRoutes();
    api.me().then(function (me) {
      setUser(me.email);
      if (me.must_change_password) { showScreen(changeSection); }
      else { enterApp(); }
    }, function () { showScreen(authSection); });
  }

  function registerRoutes() {
    router.register('', function () { showView('home'); views.renderHome(); });
    router.register('projetos', function () { showView('projetos'); views.renderProjetos(); });
    router.register('projetos/:id', function (p) { showView('projeto'); views.renderProjeto(p); });
    router.register('favoritos', function () { showView('favoritos'); views.renderFavoritos(); });
    router.register('config', function () { showView('config'); views.renderConfig(); });
  }

  function enterApp() {
    showScreen(appSection);
    applyPrefsToUpload();
    if (!routerStarted) {
      var last = store ? store.getLastRoute() : null;
      if (last && (!window.location.hash || window.location.hash === '#/')) {
        window.location.hash = last;
      }
      router.start(updateNav);
      routerStarted = true;
    } else {
      router.resolve();
    }
  }

  // ── Login ─────────────────────────────────────────────────────────────
  var loginForm = byId('login-form');
  if (loginForm) loginForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = byId('login-btn'), err = byId('login-error');
    if (err) err.classList.add('hidden');
    btn.disabled = true; btn.textContent = 'Entrando...';
    api.login(byId('login-email').value, byId('login-password').value).then(function (json) {
      setUser(json.email);
      loginForm.reset();
      if (json.must_change_password) { showScreen(changeSection); } else { enterApp(); }
    }, function (ex) {
      if (err) { err.textContent = (ex && ex.message) || 'Falha no login.'; err.classList.remove('hidden'); }
    }).then(function () { btn.disabled = false; btn.textContent = 'Entrar'; });
  });

  // ── Troca de senha (primeiro acesso) ──────────────────────────────────
  var changeForm = byId('change-form');
  if (changeForm) changeForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = byId('change-btn'), err = byId('change-error');
    function showErr(m) { if (err) { err.textContent = m; err.classList.remove('hidden'); } }
    if (err) err.classList.add('hidden');
    var n1 = byId('new-password').value, n2 = byId('new-password2').value;
    if (n1 !== n2) { showErr('As senhas novas não coincidem.'); return; }
    if (n1.length < 8) { showErr('A nova senha precisa ter ao menos 8 caracteres.'); return; }
    btn.disabled = true; btn.textContent = 'Salvando...';
    api.changePassword(byId('cur-password').value, n1).then(function () {
      changeForm.reset();
      enterApp();
    }, function (ex) { showErr((ex && ex.message) || 'Falha ao trocar a senha.'); })
      .then(function () { btn.disabled = false; btn.textContent = 'Salvar nova senha'; });
  });

  // ── Logout ────────────────────────────────────────────────────────────
  var logoutBtn = byId('logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', function () {
    stopPolling();
    api.logout().then(function () {
      resetUpload();
      showScreen(authSection);
    });
  });

  // ── Seleção de arquivo + validação ────────────────────────────────────
  var dropzone = byId('dropzone');
  var videoFile = byId('video-file');
  var fileNameDisplay = byId('file-name-display');
  var fileMeta = byId('file-meta');
  var submitBtn = byId('submit-btn');
  var uploadForm = byId('upload-form');
  var uploadError = byId('upload-error');

  function fileExtOk(name) {
    var m = /\.([a-z0-9]+)$/i.exec(name || '');
    if (!m) return false;
    return ACCEPT_EXT.indexOf(m[1].toLowerCase()) >= 0;
  }
  function validateFile() {
    fileValid = false;
    if (fileMeta) { fileMeta.textContent = ''; fileMeta.classList.remove('meta-error'); }
    if (!videoFile || !videoFile.files.length) { if (submitBtn) submitBtn.disabled = false; return; }
    var f = videoFile.files[0];
    if (!fileExtOk(f.name)) {
      if (fileMeta) { fileMeta.textContent = 'Formato não suportado. Use mp4, mov, avi, mkv ou webm.'; fileMeta.classList.add('meta-error'); }
      if (submitBtn) submitBtn.disabled = true;
      return;
    }
    if (f.size > MAX_BYTES) {
      if (fileMeta) { fileMeta.textContent = 'Arquivo acima de 15GB (' + ui.fmtBytes(f.size) + ').'; fileMeta.classList.add('meta-error'); }
      if (submitBtn) submitBtn.disabled = true;
      return;
    }
    fileValid = true;
    if (submitBtn) submitBtn.disabled = false;
    if (fileMeta) fileMeta.textContent = ui.fmtBytes(f.size);
  }
  function updateFileName() {
    if (fileNameDisplay) fileNameDisplay.textContent = (videoFile && videoFile.files.length) ? videoFile.files[0].name : '';
    validateFile();
  }
  if (videoFile) videoFile.addEventListener('change', updateFileName);
  if (dropzone) {
    ['dragenter', 'dragover'].forEach(function (ev) {
      dropzone.addEventListener(ev, function (e) { e.preventDefault(); dropzone.classList.add('is-dragover'); });
    });
    ['dragleave', 'dragend'].forEach(function (ev) {
      dropzone.addEventListener(ev, function () { dropzone.classList.remove('is-dragover'); });
    });
    dropzone.addEventListener('drop', function (e) {
      e.preventDefault(); dropzone.classList.remove('is-dragover');
      if (e.dataTransfer && e.dataTransfer.files.length) {
        try { videoFile.files = e.dataTransfer.files; } catch (err) {}
        updateFileName();
      }
    });
  }

  // Presets de formato/duração
  var presets = byId('presets');
  if (presets) presets.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-min]') : null;
    if (!b) return;
    var min = byId('min-duration'), max = byId('max-duration');
    if (min) min.value = b.getAttribute('data-min');
    if (max) max.value = b.getAttribute('data-max');
    var asp = b.getAttribute('data-aspect'); var aspSel = byId('opt-aspect');
    if (asp && aspSel) aspSel.value = asp;
    var chips = presets.querySelectorAll('[data-min]');
    Array.prototype.forEach.call(chips, function (x) { x.classList.remove('is-active'); });
    b.classList.add('is-active');
  });

  function applyPrefsToUpload() {
    if (!store) return;
    var p = store.getPrefs();
    var min = byId('min-duration'), max = byId('max-duration');
    var aspSel = byId('opt-aspect'), capChk = byId('opt-captions'), langSel = byId('opt-language');
    if (min && p.defaultMin != null) min.value = p.defaultMin;
    if (max && p.defaultMax != null) max.value = p.defaultMax;
    if (aspSel && p.aspect) aspSel.value = p.aspect;
    if (capChk) capChk.checked = !!p.captions;
    if (langSel && p.language) langSel.value = p.language;
  }

  // ── Upload ────────────────────────────────────────────────────────────
  if (uploadForm) uploadForm.addEventListener('submit', function (e) {
    e.preventDefault();
    if (uploadError) uploadError.classList.add('hidden');
    var minD = parseInt(byId('min-duration').value, 10);
    var maxD = parseInt(byId('max-duration').value, 10);
    if (minD >= maxD) { showUploadError('A duração mínima deve ser menor que a máxima.'); return; }
    if (!videoFile.files.length) { showUploadError('Selecione um vídeo.'); return; }
    validateFile();
    if (!fileValid) { showUploadError('Arquivo inválido. Confira formato e tamanho.'); return; }

    submitBtn.disabled = true; submitBtn.textContent = 'Enviando...';
    var data = new FormData(uploadForm);
    // opções de processamento (ver BACKEND_INTEGRATION.md — backend deve aceitar estes campos)
    var capChk = byId('opt-captions'), langSel = byId('opt-language'), aspSel = byId('opt-aspect');
    if (capChk) data.set('captions', capChk.checked ? 'true' : 'false');
    if (langSel) data.set('language', langSel.value);
    if (aspSel) data.set('aspect_ratio', aspSel.value);

    api.upload(data).then(function (json) {
      trackJob(json.job_id, videoFile.files[0].name);
    }, function (ex) {
      if (ex && ex.status === 401) { requireAuth(); return; }
      showUploadError((ex && ex.message) || 'Erro ao enviar vídeo.');
      resetSubmitBtn();
    });
  });

  function showUploadError(msg) { if (uploadError) { uploadError.textContent = msg; uploadError.classList.remove('hidden'); } }
  function resetSubmitBtn() { if (submitBtn) { submitBtn.disabled = false; submitBtn.textContent = 'Gerar clipes'; } }

  // ── Progresso / polling ───────────────────────────────────────────────
  var uploadSection = byId('upload-section');
  var progressSection = byId('progress-section');
  var resultsSection = byId('results-section');
  var filenameLabel = byId('filename-label');
  var progressBar = byId('progress-bar');
  var progressPct = byId('progress-pct');
  var statusLabel = byId('status-label');
  var newVideoBtn = byId('new-video-btn');
  var cancelBtn = byId('cancel-btn');
  var clipsList = byId('clips-list');

  function trackJob(jobId, filename) {
    currentJobId = jobId;
    if (uploadSection) uploadSection.classList.add('hidden');
    if (progressSection) progressSection.classList.remove('hidden');
    if (resultsSection) resultsSection.classList.add('hidden');
    if (newVideoBtn) newVideoBtn.classList.add('hidden');
    if (cancelBtn) cancelBtn.classList.remove('hidden');
    if (filenameLabel) filenameLabel.textContent = filename || '';
    setProgress(5, 'queued');
    if (pollTimer) clearInterval(pollTimer);
    poll(jobId);
    pollTimer = setInterval(function () { poll(jobId); }, POLL_INTERVAL_MS);
  }

  function poll(jobId) {
    api.status(jobId).then(function (data) {
      setProgress(data.progress || 0, data.status);
      if (data.status === 'done') {
        stopPolling();
        if (cancelBtn) cancelBtn.classList.add('hidden');
        showResults(data.clips || [], false);
        if (newVideoBtn) newVideoBtn.classList.remove('hidden');
        if (views && views.renderHome) views.renderHome();
      } else if (data.status === 'error') {
        stopPolling();
        if (cancelBtn) cancelBtn.classList.add('hidden');
        if (statusLabel) { statusLabel.textContent = 'Erro: ' + (data.error || 'Falha desconhecida.'); statusLabel.classList.add('error-text'); }
        if (newVideoBtn) newVideoBtn.classList.remove('hidden');
        if (data.partial_clips && data.partial_clips.length) showResults(data.partial_clips, true);
      }
    }, function (ex) {
      if (ex && ex.status === 401) { stopPolling(); requireAuth(); }
      /* outros erros de rede: segue tentando */
    });
  }

  function setProgress(pct, status) {
    if (progressBar) progressBar.style.width = pct + '%';
    if (progressPct) progressPct.textContent = pct + '%';
    if (statusLabel) { statusLabel.textContent = STATUS_LABELS[status] || status; statusLabel.classList.remove('error-text'); }
  }

  function showResults(clips, partial) {
    if (resultsSection) resultsSection.classList.remove('hidden');
    if (!clipsList) return;
    if (!clips || !clips.length) { clipsList.innerHTML = ui.emptyState({ title: 'Nenhum clipe gerado' }); return; }
    views.mountClips(clipsList, clips, { toolbar: true, notice: partial ? 'Clipes parciais (o processamento falhou após estes).' : null });
  }

  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  if (cancelBtn) cancelBtn.addEventListener('click', function () {
    if (!currentJobId) return;
    ui.confirm({ title: 'Cancelar processamento?', message: 'O vídeo atual deixa de ser processado.', okLabel: 'Cancelar processamento', danger: true }).then(function (yes) {
      if (!yes) return;
      api.cancelJob(currentJobId).then(function (ok) {
        if (ok) { stopPolling(); ui.toast('Processamento cancelado', 'success'); resetUpload(); }
        else ui.toast('Cancelar aguarda o backend (POST /api/jobs/:id/cancel).', 'info', { ttl: 6000 });
      });
    });
  });

  if (newVideoBtn) newVideoBtn.addEventListener('click', function () { resetUpload(); });

  function resetUpload() {
    stopPolling();
    currentJobId = null;
    if (uploadForm) uploadForm.reset();
    applyPrefsToUpload();
    updateFileName();
    if (uploadSection) uploadSection.classList.remove('hidden');
    if (progressSection) progressSection.classList.add('hidden');
    if (resultsSection) resultsSection.classList.add('hidden');
    if (clipsList) clipsList.innerHTML = '';
    if (newVideoBtn) newVideoBtn.classList.add('hidden');
    if (cancelBtn) cancelBtn.classList.add('hidden');
    if (statusLabel) statusLabel.classList.remove('error-text');
    resetSubmitBtn();
    if (uploadError) uploadError.classList.add('hidden');
  }

  // ── Atalhos de teclado ────────────────────────────────────────────────
  var lastG = 0;
  function isTyping(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
  }
  document.addEventListener('keydown', function (e) {
    if (isTyping(e.target)) return;
    var modalOpen = document.body.classList.contains('modal-open');
    if (e.key === '?') { e.preventDefault(); openShortcuts(); return; }
    if (modalOpen) return;
    var now = Date.now();
    if (e.key === 'g') { lastG = now; return; }
    if (now - lastG < 800) {
      if (e.key === 'h') { router.go('/'); return; }
      if (e.key === 'p') { router.go('/projetos'); return; }
      if (e.key === 'f') { router.go('/favoritos'); return; }
      if (e.key === 'c') { router.go('/config'); return; }
    }
    if (e.key === 'n') { router.go('/'); var dz = byId('dropzone'); if (dz && dz.focus) dz.focus(); }
  });
  function openShortcuts() {
    var html = '<h3 class="modal-title">Atalhos</h3><ul class="shortcuts">' +
      '<li><kbd>n</kbd> Novo vídeo</li>' +
      '<li><kbd>g</kbd> <kbd>h</kbd> Início</li>' +
      '<li><kbd>g</kbd> <kbd>p</kbd> Projetos</li>' +
      '<li><kbd>g</kbd> <kbd>f</kbd> Favoritos</li>' +
      '<li><kbd>g</kbd> <kbd>c</kbd> Configurações</li>' +
      '<li><kbd>?</kbd> Esta ajuda</li>' +
      '<li><kbd>Esc</kbd> Fechar janelas</li>' +
    '</ul>';
    ui.modalOpen(html, { cardClass: 'modal-sm' });
  }

  // expõe para os outros módulos
  NS.core = {
    requireAuth: requireAuth,
    trackJob: trackJob,
    applyPrefsToUpload: applyPrefsToUpload,
    showView: showView,
    resetUpload: resetUpload
  };

  boot();
})();
