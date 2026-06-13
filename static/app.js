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

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    hideError();

    const minD = parseInt(document.getElementById('min-duration').value, 10);
    const maxD = parseInt(document.getElementById('max-duration').value, 10);
    if (minD >= maxD) {
      showError('A duração mínima deve ser menor que a máxima.');
      return;
    }

    const fileInput = document.getElementById('video-file');
    if (!fileInput.files.length) {
      showError('Selecione um vídeo.');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Enviando...';

    const data = new FormData(form);

    try {
      const resp = await fetch('/api/upload', { method: 'POST', body: data });
      const json = await resp.json();

      if (!resp.ok) {
        showError(json.error || 'Erro ao enviar vídeo.');
        resetSubmitBtn();
        return;
      }

      currentJobId = json.job_id;
      startPolling(currentJobId, fileInput.files[0].name);
    } catch (err) {
      showError('Falha de conexão ao enviar o vídeo. Tente novamente.');
      resetSubmitBtn();
    }
  });

  newVideoBtn.addEventListener('click', function () {
    resetUI();
  });

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
    } catch (_) {
      // network blip — keep polling
    }
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
    if (!urls.length) {
      clipsList.innerHTML = '<p>Nenhum clipe gerado.</p>';
      return;
    }
    const heading = partial ? '<p class="partial-notice">Clipes parciais (processamento falhou após estes):</p>' : '';
    clipsList.innerHTML = heading + urls.map(function (url, i) {
      return '<a href="' + url + '" class="clip-link" download>Baixar clipe ' + (i + 1) + '</a>';
    }).join('');
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function showError(msg) {
    uploadError.textContent = msg;
    uploadError.classList.remove('hidden');
  }

  function hideError() {
    uploadError.classList.add('hidden');
  }

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
    hideError();
  }
})();
