/* OpusSB · views.js
   Componentes de clipe (card, grid com busca/filtro/ordenação, player modal, favoritos) e as páginas
   Dashboard, Projetos, Detalhe do projeto, Favoritos e Configurações. Renderiza em containers do index.html. */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});
  var ui = NS.ui, api = NS.api, store = NS.store;

  var HEART = '♥', EXPAND = '⤢', PLAY = '▶';
  // Ícones Lucide (fill=none + stroke=currentColor herdam a cor do botão; 16px casa
  // com os labels). Substituem os glifos unicode crus (⤓ ⧉ ✎), que destoavam.
  function icon(paths) {
    return '<svg class="ic" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>';
  }
  var DL = icon('<path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>');
  var COPY = icon('<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>');
  var EDIT = icon('<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>');

  // O vídeo é renderizado sob demanda (a rota /video só faz 302 depois de queimar a
  // legenda), então o 1o load de cada clipe demora alguns segundos. Este overlay fica
  // por cima do <video> até ele ficar pronto, pra não parecer travado. Depois é cache.
  var PREP_OVERLAY = '<div class="clip-prep" aria-hidden="true"><span class="clip-prep-spin"></span><span class="clip-prep-note">preparando legenda…</span></div>';

  // clip.url é rota relativa que exige login (/api/clips/<id>/video). Pra "copiar link"
  // devolvemos o absoluto; abrir ainda depende de sessão (avisado no toast).
  function shareableUrl(clip) {
    var u = (clip && clip.url) || '';
    if (!u) return '';
    if (/^https?:\/\//i.test(u)) return u;
    return location.origin + u;
  }
  function copyClipLink(clip) {
    var u = shareableUrl(clip);
    if (!u) return;
    ui.copyToClipboard(u).then(
      function () { ui.toast('Link copiado — abrir exige login na sua conta.', 'success'); },
      function () { ui.toast('Não consegui copiar', 'error'); }
    );
  }

  // Pós-save: re-renderiza o <video> do card com cache-buster. O backend invalidou o
  // _cap.mp4; sem isto o card ficaria na versão antiga até um F5. Também reexibe o
  // overlay "preparando" enquanto a nova versão re-renderiza.
  function bustClipVideo(clipId) {
    var cards = document.querySelectorAll('.clip-card');
    for (var i = 0; i < cards.length; i++) {
      if (String(cards[i].getAttribute('data-clip-id')) !== String(clipId)) continue;
      var v = cards[i].querySelector('video');
      if (!v) return;
      var src = (v.getAttribute('src') || '').replace(/([?&])t=\d+/, '$1').replace(/[?&]$/, '');
      if (!src) return;
      v.src = src + (src.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now();
      var box = v.closest ? v.closest('.clip-video') : null;
      if (box) box.classList.remove('is-ready');
      v.load();
      return;
    }
  }

  // Eventos de mídia não borbulham, então delegamos na fase de captura: assim o
  // wiring sobrevive ao re-render do grid (filtro/ordenação refazem o innerHTML).
  // Marca o container com .is-ready quando o vídeo dá pra tocar; remove no "waiting".
  function wirePrepDelegation(container, sel) {
    function ready(e) { var c = e.target.closest && e.target.closest(sel); if (c) c.classList.add('is-ready'); }
    function wait(e) { var c = e.target.closest && e.target.closest(sel); if (c) c.classList.remove('is-ready'); }
    container.addEventListener('loadedmetadata', ready, true);   // 302 resolveu (burn pronto)
    container.addEventListener('loadeddata', ready, true);
    container.addEventListener('canplay', ready, true);
    container.addEventListener('playing', ready, true);
    container.addEventListener('error', ready, true);   // em erro, deixa os controls nativos aparecerem
    container.addEventListener('waiting', wait, true);
  }

  // arrow-burst decorativo (empty de favoritos), cinza + gradiente dourado
  function arrowBurst(gradId, baseFill) {
    var rots = [0, 45, 90, 135, 180, 270, 315];
    var g = '';
    for (var i = 0; i < rots.length; i++) {
      g += '<g transform="rotate(' + rots[i] + ' 100 100)"><path d="M126,100 L148,84 L148,92 L190,92 L190,108 L148,108 L148,116 Z" fill="' + baseFill + '"/></g>';
    }
    g += '<g transform="rotate(225 100 100)"><path d="M126,100 L148,84 L148,92 L190,92 L190,108 L148,108 L148,116 Z" fill="url(#' + gradId + ')"/></g>';
    return '<svg class="arrowburst" width="120" height="120" viewBox="0 0 200 200" aria-hidden="true">' +
      '<defs><linearGradient id="' + gradId + '" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFD75E"/><stop offset="1" stop-color="#E0A800"/></linearGradient></defs>' + g + '</svg>';
  }

  // ── Normalização de clipe ─────────────────────────────────────────────
  function normalizeClip(item, i) {
    if (typeof item === 'string') {
      return { id: 'idx-' + i, url: item, score: null, title: null, reason: null, thumbnail_url: '', duration: null, project_id: null, favorite: false, index: i };
    }
    var raw = item || {};
    var url = raw.url || raw.clip_url || raw.href || '';
    var score = null;
    if (raw.score !== undefined && raw.score !== null && raw.score !== '') {
      var n = Number(raw.score); if (!isNaN(n)) score = n;
    }
    var id = raw.id || raw.clip_id || ('idx-' + i);
    var fav = (raw.favorite !== undefined && raw.favorite !== null) ? !!raw.favorite : (store ? store.isFavorite(id) : false);
    return {
      id: id, url: url, score: score, title: raw.title || null, reason: raw.reason || null,
      thumbnail_url: raw.thumbnail_url || raw.thumb || '', duration: (raw.duration != null ? raw.duration : null),
      project_id: raw.project_id || null, favorite: fav, index: i
    };
  }

  function findClip(clips, id) {
    for (var i = 0; i < clips.length; i++) { if (String(clips[i].id) === String(id)) return clips[i]; }
    return null;
  }
  function snapshotClip(c) {
    return { id: c.id, url: c.url, title: c.title, reason: c.reason, score: c.score, thumbnail_url: c.thumbnail_url, duration: c.duration, project_id: c.project_id, favorite: true, index: c.index || 0 };
  }
  function toggleFavorite(clip) {
    var on = !(store && store.isFavorite(clip.id));
    if (store) store.setFavorite(clip.id, snapshotClip(clip), on);
    if (api) api.favoriteClip(clip.id, on);
    if (ui) ui.toast(on ? 'Adicionado aos favoritos' : 'Removido dos favoritos', 'success');
    return on;
  }

  // ── Card de clipe ─────────────────────────────────────────────────────
  function clipCard(clip) {
    var n = (clip.index || 0) + 1;
    var title = clip.title ? ui.escapeHtml(clip.title) : ('Clipe ' + n);
    var safeUrl = ui.escapeHtml(clip.url);
    var badge = clip.score != null ? '<span class="score-badge ' + ui.scoreClass(clip.score) + '">' + ui.fmtScore(clip.score) + '</span>' : '';
    var dur = clip.duration != null ? '<span class="clip-dur">' + ui.fmtSeconds(clip.duration) + '</span>' : '';
    var favOn = clip.favorite;
    var favBtn = '<button class="clip-fav' + (favOn ? ' is-on' : '') + '" type="button" data-act="fav" title="Favoritar" aria-pressed="' + (favOn ? 'true' : 'false') + '">' + HEART + '</button>';
    var maxBtn = '<button class="clip-max" type="button" data-act="max" title="Abrir" aria-label="Abrir clipe">' + EXPAND + '</button>';
    var media = clip.url
      ? '<video src="' + safeUrl + '" controls playsinline preload="metadata"></video>' + PREP_OVERLAY
      : '<div class="clip-ph" data-act="max"><span class="clip-ph-glyph" aria-hidden="true">' + PLAY + '</span><span class="clip-ph-note">prévia no modo demonstração</span></div>';
    var reason = clip.reason ? '<p class="clip-reason">' + ui.escapeHtml(clip.reason) + '</p>' : '';
    var dl = clip.url
      ? '<a class="clip-download" href="' + safeUrl + '" download>' + DL + 'Baixar</a>'
      : '<button class="clip-download" type="button" disabled>' + DL + 'Baixar</button>';
    var copy = '<button class="clip-copy" type="button" data-act="copy" title="Copiar link"' + (clip.url ? '' : ' disabled') + '>' + COPY + '</button>';
    var edit = clip.url ? '<button class="clip-edit" type="button" data-act="edit" title="Editar legenda e música" aria-label="Editar legenda e música">' + EDIT + '</button>' : '';
    return '<article class="clip-card" data-clip-id="' + ui.attr(clip.id) + '">' +
        '<div class="clip-video">' + media + badge + dur + favBtn + maxBtn + '</div>' +
        '<div class="clip-body"><h3 class="clip-title">' + title + '</h3>' + reason + '</div>' +
        '<div class="clip-actions">' + dl + copy + edit + '</div>' +
      '</article>';
  }

  // ── Player modal ──────────────────────────────────────────────────────
  function openClipModal(clip) {
    var media = clip.url
      ? '<video src="' + ui.escapeHtml(clip.url) + '" controls playsinline autoplay></video>' + PREP_OVERLAY
      : '<div class="clip-ph clip-ph-lg"><span class="clip-ph-glyph" aria-hidden="true">' + PLAY + '</span><span class="clip-ph-note">Prévia indisponível no modo demonstração</span></div>';
    var badge = clip.score != null ? '<span class="score-badge ' + ui.scoreClass(clip.score) + '">' + ui.fmtScore(clip.score) + '</span>' : '';
    var title = clip.title ? ui.escapeHtml(clip.title) : ('Clipe ' + ((clip.index || 0) + 1));
    var reason = clip.reason ? '<p class="modal-reason">' + ui.escapeHtml(clip.reason) + '</p>' : '';
    var favOn = store ? store.isFavorite(clip.id) : clip.favorite;
    var html = '<div class="clipmodal">' +
      '<div class="clipmodal-video">' + media + '</div>' +
      '<div class="clipmodal-info">' +
        '<div class="clipmodal-head"><h3 class="modal-title">' + title + '</h3>' + badge + '</div>' +
        reason +
        '<div class="clipmodal-actions">' +
          (clip.url ? '<a class="btn btn-gold" href="' + ui.escapeHtml(clip.url) + '" download>' + DL + 'Baixar</a>' : '<button class="btn btn-gold" disabled>' + DL + 'Baixar</button>') +
          '<button class="btn btn-ghost" data-act="copy"' + (clip.url ? '' : ' disabled') + '>' + COPY + 'Copiar link</button>' +
          '<button class="btn btn-ghost clip-fav-btn' + (favOn ? ' is-on' : '') + '" data-act="fav">' + HEART + ' ' + (favOn ? 'Favorito' : 'Favoritar') + '</button>' +
        '</div>' +
      '</div></div>';
    ui.modalOpen(html, { cardClass: 'modal-clip' });
    var root = document.getElementById('modal-root');
    if (!root) return;
    if (clip.url) {
      var vbox = root.querySelector('.clipmodal-video');
      if (vbox) wirePrepDelegation(vbox, '.clipmodal-video');
    }
    var copyBtn = root.querySelector('[data-act="copy"]');
    if (copyBtn && clip.url) copyBtn.addEventListener('click', function () { copyClipLink(clip); });
    var favBtn = root.querySelector('[data-act="fav"]');
    if (favBtn) favBtn.addEventListener('click', function () {
      var now = toggleFavorite(clip); clip.favorite = now;
      favBtn.classList.toggle('is-on', now);
      favBtn.innerHTML = HEART + ' ' + (now ? 'Favorito' : 'Favoritar');
    });
  }

  // ── Editor do clipe (legenda: texto+tempo; música de fundo) ───────────
  function fmtNum(n) { var x = Number(n); return isNaN(x) ? 0 : (Math.round(x * 100) / 100); }

  function segRowHtml(seg) {
    var s = seg || { start: 0, end: 2, text: '' };
    return '<div class="seg-row">' +
        '<input class="seg-start" type="number" step="0.05" min="0" value="' + fmtNum(s.start) + '" aria-label="Início (s)">' +
        '<input class="seg-end" type="number" step="0.05" min="0" value="' + fmtNum(s.end) + '" aria-label="Fim (s)">' +
        '<input class="seg-text" type="text" maxlength="300" value="' + ui.attr(s.text || '') + '" placeholder="Texto da legenda">' +
        '<button class="seg-del" type="button" data-act="del-seg" title="Remover linha" aria-label="Remover linha">✕</button>' +
      '</div>';
  }

  function musicSectionHtml(music) {
    var head = '<div class="editor-sec-head"><strong>Música de fundo</strong></div>';
    if (music && music.filename) {
      var vol = Math.round((music.volume != null ? music.volume : 0.15) * 100);
      return head +
        '<div class="editor-music-has"><span class="editor-music-name">🎵 ' + ui.escapeHtml(music.filename) + '</span>' +
          '<button class="btn btn-ghost btn-sm" type="button" data-act="music-remove">Remover</button></div>' +
        '<label class="editor-vol">Volume <input type="range" min="0" max="100" value="' + vol + '" data-music-vol></label>';
    }
    return head +
      '<label class="btn btn-ghost btn-sm editor-music-add">+ Adicionar música' +
        '<input type="file" accept="audio/*,.mp3,.m4a,.aac,.wav,.ogg" data-music-file></label>' +
      '<p class="editor-hint">mp3, m4a, aac, wav ou ogg. Entra de fundo, em loop, abaixo da fala.</p>';
  }

  function openEditorModal(clip) {
    ui.modalOpen('<div class="editor"><h3 class="modal-title">Editar clipe</h3><p class="modal-text">Carregando…</p></div>', { cardClass: 'modal-editor' });
    api.getCaptions(clip.id).then(function (data) {
      renderEditor(clip, data || {});
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      var root = document.getElementById('modal-root');
      var p = root && root.querySelector('.modal-text');
      if (p) p.textContent = 'Não consegui carregar a legenda deste clipe.';
    });
  }

  function renderEditor(clip, data) {
    var root = document.getElementById('modal-root');
    if (!root) return;
    var body = root.querySelector('.modal-body');
    if (!body) return;

    if (!data.editable) {
      body.innerHTML = '<div class="editor"><h3 class="modal-title">Edição indisponível</h3>' +
        '<p class="modal-text">Este clipe foi gerado antes do editor (legenda fixa no vídeo) ou não tem fala. Reprocessar o projeto gera clipes editáveis.</p>' +
        '<div class="modal-actions"><button class="btn btn-gold" type="button" data-act="close">Fechar</button></div></div>';
      var c0 = body.querySelector('[data-act="close"]'); if (c0) c0.addEventListener('click', ui.modalClose);
      return;
    }

    var segs = (data.segments && data.segments.length) ? data.segments : [];
    var previewUrl = clip.url ? (clip.url + (clip.url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now()) : '';
    body.innerHTML = '<div class="editor">' +
      '<h3 class="modal-title">Editar clipe</h3>' +
      (previewUrl ? '<div class="editor-preview"><video src="' + ui.escapeHtml(previewUrl) + '" controls playsinline preload="metadata"></video>' + PREP_OVERLAY + '</div>' : '') +
      '<div class="editor-section">' +
        '<div class="editor-sec-head"><strong>Legenda</strong>' +
          '<button class="btn btn-ghost btn-sm" type="button" data-act="add-seg">+ Linha</button></div>' +
        '<div class="editor-segs" data-segs>' + segs.map(segRowHtml).join('') + '</div>' +
      '</div>' +
      '<div class="editor-section" data-music-sec>' + musicSectionHtml(data.music) + '</div>' +
      '<div class="modal-actions">' +
        '<button class="btn btn-ghost" type="button" data-act="close">Fechar</button>' +
        '<button class="btn btn-gold" type="button" data-act="save">Salvar</button>' +
      '</div>' +
    '</div>';

    var segsBox = body.querySelector('[data-segs]');
    var musicSec = body.querySelector('[data-music-sec]');
    var prevBox = body.querySelector('.editor-preview');
    if (prevBox) wirePrepDelegation(prevBox, '.editor-preview');

    body.querySelector('[data-act="add-seg"]').addEventListener('click', function () {
      segsBox.insertAdjacentHTML('beforeend', segRowHtml({ start: 0, end: 2, text: '' }));
    });
    segsBox.addEventListener('click', function (e) {
      var del = e.target.closest ? e.target.closest('[data-act="del-seg"]') : null;
      if (del) { var row = del.closest('.seg-row'); if (row) row.parentNode.removeChild(row); }
    });

    function wireMusic() {
      var fileInput = musicSec.querySelector('[data-music-file]');
      if (fileInput) fileInput.addEventListener('change', function () {
        var f = fileInput.files && fileInput.files[0];
        if (!f) return;
        ui.toast('Enviando música…', 'info');
        api.uploadMusic(clip.id, f, 0.15).then(function (res) {
          ui.toast('Música adicionada. O vídeo re-renderiza no próximo play.', 'success');
          musicSec.innerHTML = musicSectionHtml((res && res.music) || { filename: f.name, volume: 0.15 });
          wireMusic();
        }, function (er) { ui.toast((er && er.message) || 'Falha ao enviar música.', 'error'); });
      });
      var rem = musicSec.querySelector('[data-act="music-remove"]');
      if (rem) rem.addEventListener('click', function () {
        api.removeMusic(clip.id).then(function () {
          ui.toast('Música removida.', 'success');
          musicSec.innerHTML = musicSectionHtml(null);
          wireMusic();
        }, function () { ui.toast('Falha ao remover música.', 'error'); });
      });
    }
    wireMusic();

    body.querySelector('[data-act="close"]').addEventListener('click', ui.modalClose);
    body.querySelector('[data-act="save"]').addEventListener('click', function () {
      var rows = segsBox.querySelectorAll('.seg-row');
      var out = [], bad = null;
      Array.prototype.forEach.call(rows, function (r, i) {
        var st = parseFloat(r.querySelector('.seg-start').value);
        var en = parseFloat(r.querySelector('.seg-end').value);
        var tx = r.querySelector('.seg-text').value.trim();
        if (isNaN(st) || isNaN(en) || en <= st) { if (bad === null) bad = i + 1; return; }
        if (!tx) return;
        out.push({ start: st, end: en, text: tx });
      });
      if (bad !== null) { ui.toast('Linha ' + bad + ': o fim precisa ser maior que o início.', 'error'); return; }
      var saveBtn = this; saveBtn.disabled = true;
      var tasks = [api.putCaptions(clip.id, out)];
      var volEl = musicSec.querySelector('[data-music-vol]');
      if (volEl) tasks.push(api.setMusicVolume(clip.id, Math.max(0, Math.min(1, (parseInt(volEl.value, 10) || 0) / 100))));
      Promise.all(tasks).then(function () {
        ui.toast('Salvo! O vídeo re-renderiza no próximo play.', 'success');
        ui.modalClose();
        bustClipVideo(clip.id);   // reflete a nova versão no card sem F5
      }, function (er) {
        saveBtn.disabled = false;
        ui.toast((er && er.message) || 'Falha ao salvar.', 'error');
      });
    });
  }

  // ── Grid de clipes com toolbar (busca / filtro / ordenação) ───────────
  function toolbarHtml() {
    return '<div class="clip-toolbar">' +
      '<input type="search" class="clip-search" placeholder="Buscar por título ou motivo" data-q aria-label="Buscar clipes">' +
      '<div class="clip-bands">' +
        '<button class="chip is-active" type="button" data-band="all">Todas</button>' +
        '<button class="chip" type="button" data-band="high">Nota ≥ 8</button>' +
        '<button class="chip" type="button" data-band="mid">6 a 8</button>' +
        '<button class="chip" type="button" data-band="low">&lt; 6</button>' +
      '</div>' +
      '<select class="clip-sort" data-sort aria-label="Ordenar clipes">' +
        '<option value="score_desc">Maior nota</option>' +
        '<option value="score_asc">Menor nota</option>' +
        '<option value="dur_desc">Mais longos</option>' +
        '<option value="order">Ordem original</option>' +
      '</select>' +
    '</div>';
  }

  function mountClips(container, rawClips, opts) {
    if (!container) return;
    opts = opts || {};
    var clips = (rawClips || []).map(normalizeClip);
    var st = { q: '', sort: 'score_desc', band: 'all' };
    var showToolbar = (opts.toolbar !== false) && clips.length > 1;

    container.innerHTML =
      (opts.notice ? '<p class="partial-notice">' + ui.escapeHtml(opts.notice) + '</p>' : '') +
      (showToolbar ? toolbarHtml() : '') +
      '<div class="clips-grid" data-grid></div>';

    var grid = container.querySelector('[data-grid]');
    wirePrepDelegation(grid, '.clip-video');

    function apply() {
      var list = clips.slice();
      if (st.q) {
        var q = st.q.toLowerCase();
        list = list.filter(function (c) { return ((c.title || '') + ' ' + (c.reason || '')).toLowerCase().indexOf(q) >= 0; });
      }
      if (st.band !== 'all') {
        list = list.filter(function (c) {
          if (c.score == null) return false;
          if (st.band === 'high') return c.score >= 8;
          if (st.band === 'mid') return c.score >= 6 && c.score < 8;
          return c.score < 6;
        });
      }
      list.sort(function (a, b) {
        if (st.sort === 'score_desc') return (b.score || 0) - (a.score || 0);
        if (st.sort === 'score_asc') return (a.score || 0) - (b.score || 0);
        if (st.sort === 'dur_desc') return (b.duration || 0) - (a.duration || 0);
        return a.index - b.index;
      });
      if (!list.length) { grid.innerHTML = ui.emptyState({ title: 'Nenhum clipe com esse filtro', text: 'Ajuste a busca ou o filtro de nota.' }); return; }
      grid.innerHTML = list.map(clipCard).join('');
    }
    apply();

    if (showToolbar) {
      var search = container.querySelector('[data-q]');
      if (search) search.addEventListener('input', function () { st.q = search.value; apply(); });
      var sortSel = container.querySelector('[data-sort]');
      if (sortSel) sortSel.addEventListener('change', function () { st.sort = sortSel.value; apply(); });
      var chips = container.querySelectorAll('[data-band]');
      Array.prototype.forEach.call(chips, function (ch) {
        ch.addEventListener('click', function () {
          st.band = ch.getAttribute('data-band');
          Array.prototype.forEach.call(chips, function (x) { x.classList.remove('is-active'); });
          ch.classList.add('is-active');
          apply();
        });
      });
    }

    grid.addEventListener('click', function (e) {
      var t = e.target;
      var card = t.closest ? t.closest('.clip-card') : null;
      if (!card) return;
      var clip = findClip(clips, card.getAttribute('data-clip-id'));
      if (!clip) return;
      var actEl = t.closest ? t.closest('[data-act]') : null;
      var act = actEl ? actEl.getAttribute('data-act') : null;
      if (act === 'fav') {
        e.preventDefault();
        var now = toggleFavorite(clip); clip.favorite = now;
        actEl.classList.toggle('is-on', now);
        actEl.setAttribute('aria-pressed', now ? 'true' : 'false');
      } else if (act === 'copy') {
        e.preventDefault();
        if (clip.url) copyClipLink(clip);
      } else if (act === 'edit') {
        e.preventDefault();
        openEditorModal(clip);
      } else if (act === 'max') {
        e.preventDefault();
        openClipModal(clip);
      }
    });
  }

  // ── Cards de projeto ──────────────────────────────────────────────────
  // O backend não gera thumbnail (thumbnail_url sempre null), então a capa seria um
  // retângulo cinza idêntico em todo card. No lugar, geramos uma capa por projeto:
  // hash do id -> matiz do brilho + uma "assinatura de onda" única. Vira identidade
  // visual sem depender de imagem.
  function hashStr(s) {
    var h = 2166136261; s = String(s || '');
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = (h * 16777619) >>> 0; }
    return h >>> 0;
  }
  function projectWave(seed) {
    var n = 32, bars = '', h = seed || 1;
    for (var i = 0; i < n; i++) {
      h = (h * 1103515245 + 12345) >>> 0;
      var amp = 0.16 + (h % 1000) / 1000 * 0.84;          // 0.16..1.0
      var bh = Math.max(2, Math.round(amp * 46));
      var y = ((60 - bh) / 2).toFixed(1);
      bars += '<rect x="' + (4 + i * 6) + '" y="' + y + '" width="3" height="' + bh + '" rx="1.5"/>';
    }
    return '<svg class="project-wave" viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true">' + bars + '</svg>';
  }
  function projectPill(p) {
    if (p.status === 'processing') return '<span class="project-pill is-proc"><span class="pill-dot"></span>processando</span>';
    if (p.status === 'error') return '<span class="project-pill is-err">falhou</span>';
    if (p.clip_count != null) return '<span class="project-pill">' + PLAY + ' ' + p.clip_count + ' ' + (p.clip_count === 1 ? 'clipe' : 'clipes') + '</span>';
    return '';
  }
  function projectCover(p) {
    var avg = p.avg_score != null ? '<span class="score-badge ' + ui.scoreClass(p.avg_score) + '">' + ui.fmtScore(p.avg_score) + '</span>' : '';
    var pill = projectPill(p);
    if (p.thumbnail_url) {
      return '<div class="project-thumb"><img class="project-thumb-img" src="' + ui.attr(p.thumbnail_url) + '" alt="">' + avg + pill + '</div>';
    }
    var seed = hashStr(p.id || p.title || p.filename);
    var hue = seed % 360, hue2 = (hue + 38) % 360;
    var bg = 'background:' +
      'radial-gradient(115% 120% at 82% 12%, hsla(' + hue + ',62%,56%,0.20), transparent 58%),' +
      'radial-gradient(120% 120% at 12% 92%, hsla(' + hue2 + ',55%,50%,0.13), transparent 55%),' +
      'linear-gradient(150deg,#17171b,#0f1012)';
    return '<div class="project-thumb project-thumb-gen" style="' + bg + '">' +
      projectWave(seed) +
      '<span class="project-mark-sm" aria-hidden="true">OpusSB</span>' +
      avg + pill +
    '</div>';
  }
  function projectCard(p) {
    return '<a class="project-card" href="#/projetos/' + ui.attr(p.id) + '">' +
      projectCover(p) +
      '<div class="project-body">' +
        '<p class="project-title">' + ui.escapeHtml(p.title || p.filename || 'Projeto') + '</p>' +
        '<p class="project-meta">' + ui.escapeHtml(ui.timeAgo(p.created_at)) + '</p>' +
        '<span class="project-open">Abrir <span aria-hidden="true">→</span></span>' +
      '</div>' +
    '</a>';
  }

  // Reel de mini-capas generativas (mesma linguagem dos project cards, em dourado)
  // preenchendo a área antes vazia do card em destaque. O nº de tiles reflete a
  // contagem de clipes do projeto (2–4).
  function featuredReel(p) {
    var n = Math.max(2, Math.min(p.clip_count || 3, 4));
    var tiles = '';
    for (var i = 0; i < n; i++) {
      var seed = hashStr((p.id || 'p') + ':' + i);
      var hue = seed % 360, hue2 = (hue + 38) % 360;
      var bg = 'background:' +
        'radial-gradient(120% 90% at 50% 12%, hsla(' + hue + ',62%,56%,0.22), transparent 60%),' +
        'radial-gradient(120% 120% at 12% 92%, hsla(' + hue2 + ',55%,50%,0.13), transparent 55%),' +
        'linear-gradient(150deg,#17171b,#0f1012)';
      tiles += '<div class="featured-tile" style="' + bg + '">' + projectWave(seed) + '</div>';
    }
    return '<div class="featured-reel">' + tiles + '</div>';
  }

  // Card "featured" (último projeto, em destaque)
  function featuredCard(p) {
    var good = (p.avg_score != null && p.avg_score >= 8);
    var scoreColor = good ? 'var(--green)' : 'var(--gold)';
    var score = p.avg_score != null ? ui.fmtScore(p.avg_score) : '—';
    var meta = (p.clip_count != null ? p.clip_count + ' clipes · ' : '') + ui.timeAgo(p.created_at);
    var href = '#/projetos/' + ui.attr(p.id);
    var thumb = p.thumbnail_url
      ? '<img class="project-thumb-img" src="' + ui.attr(p.thumbnail_url) + '" alt="">'
      : featuredReel(p);
    return '<div class="featured-card">' +
      '<a class="featured-thumb" href="' + href + '">' + thumb + '<span class="featured-tag">Último projeto</span></a>' +
      '<div class="featured-body">' +
        '<div class="featured-score-row"><span class="featured-score" style="color:' + scoreColor + '">' + score + '</span><span class="featured-score-label">nota média dos clipes</span></div>' +
        '<div class="featured-title">' + ui.escapeHtml(p.title || p.filename || 'Projeto') + '</div>' +
        '<div class="featured-meta">' + ui.escapeHtml(meta) + '</div>' +
        '<a class="btn btn-gold" href="' + href + '">Abrir projeto →</a>' +
      '</div></div>';
  }

  // ── Dashboard / Início ────────────────────────────────────────────────
  // Ícones + card de estatística. Usados na aba Config › Conta (produção da conta).
  var STAT_ICONS = {
    videos: icon('<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>'),
    clips: icon('<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>'),
    star: icon('<path d="M11.5 2.6a.5.5 0 0 1 .95 0l2.3 4.66 5.15.75a.53.53 0 0 1 .3.9l-3.73 3.64.88 5.12a.53.53 0 0 1-.77.56L12 16.4l-4.6 2.42a.53.53 0 0 1-.77-.56l.88-5.12-3.73-3.64a.53.53 0 0 1 .3-.9l5.15-.75z"/>')
  };
  function statCardHtml(ic, label, num, opts) {
    opts = opts || {};
    var disc = '<span class="stat-ic' + (opts.accent ? ' is-gold' : '') + '" aria-hidden="true">' + ic + '</span>';
    if (num == null) return '<div class="stat-card">' + disc + '<p class="stat-value">—</p><p class="stat-label">' + label + '</p></div>';
    var disp = opts.decimals ? ui.fmtScore(num) : String(num);
    return '<div class="stat-card">' + disc +
      '<p class="stat-value' + (opts.accent ? ' is-gold' : '') + '" data-count="' + num + '"' + (opts.decimals ? ' data-decimals="1"' : '') + '>' + disp + '</p>' +
      '<p class="stat-label">' + label + '</p></div>';
  }
  // Contador animado (0 → valor).
  function countUpStats(root) {
    var els = root.querySelectorAll('.stat-value[data-count]');
    Array.prototype.forEach.call(els, function (el) {
      var target = parseFloat(el.getAttribute('data-count'));
      if (isNaN(target)) return;
      var dec = el.getAttribute('data-decimals') ? 1 : 0, dur = 1000, t0 = null;
      function fmt(v) { return dec ? v.toFixed(1) : String(Math.round(v)); }
      function step(ts) {
        if (t0 === null) t0 = ts;
        var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
        el.textContent = fmt(target * e);
        if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(target);
      }
      el.textContent = fmt(0);
      requestAnimationFrame(step);
    });
  }
  function renderHome() {
    var recentEl = document.getElementById('home-recent');
    if (recentEl) recentEl.innerHTML = '';
    if (!api) return;
    // getStats ainda alimenta o "Recentes" (s.recent); os números de produção foram
    // pro Config › Conta (faziam sentido como dado de conta, não como vitrine na Home).
    api.getStats().then(function (s) {
      if (!s || !recentEl) return;
      var recent = s.recent || [];
      if (!recent.length) { recentEl.innerHTML = ''; return; }
      var head = '<div class="section-head"><div><p class="eyebrow">Recentes</p><h2 class="results-title">Seus projetos</h2></div><a class="link-gold" href="#/projetos">Ver todos →</a></div>';
      var rest = recent.slice(1);
      var grid = rest.length ? '<div class="projects-grid">' + rest.map(projectCard).join('') + '</div>' : '';
      recentEl.innerHTML = head + featuredCard(recent[0]) + grid;
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
    });
  }

  // ── Projetos (histórico) ──────────────────────────────────────────────
  var projSort = 'all';
  function renderProjetos() {
    var el = document.getElementById('projetos-body');
    if (!el) return;
    el.innerHTML = ui.skeletonGrid(8, 'skel-card');
    api.getProjects().then(function (res) {
      var list = (res && res.projects) ? res.projects.slice() : [];
      if (!list.length) {
        el.innerHTML = ui.emptyState({ title: 'Nenhum projeto ainda', text: 'Envie um vídeo na tela inicial para gerar seus primeiros clipes.', actionHtml: '<a class="btn btn-gold" href="#/">Enviar vídeo</a>' });
        return;
      }
      function sorted() {
        var arr = list.slice();
        if (projSort === 'score') arr.sort(function (a, b) { return (b.avg_score || 0) - (a.avg_score || 0); });
        else if (projSort === 'recent') arr.sort(function (a, b) { return (new Date(b.created_at).getTime() || 0) - (new Date(a.created_at).getTime() || 0); });
        return arr;
      }
      function chip(key, label) { return '<button class="chip' + (projSort === key ? ' is-active' : '') + '" type="button" data-projsort="' + key + '">' + label + '</button>'; }
      function draw() {
        el.innerHTML = '<div class="projects-toolbar">' +
            '<span class="projects-count">' + list.length + ' projeto' + (list.length === 1 ? '' : 's') + '</span>' +
            '<div class="clip-bands">' + chip('all', 'Todos') + chip('score', 'Melhores notas') + chip('recent', 'Mais recentes') + '</div>' +
          '</div>' +
          '<div class="projects-grid">' + sorted().map(projectCard).join('') + '</div>';
        var chips = el.querySelectorAll('[data-projsort]');
        Array.prototype.forEach.call(chips, function (c) {
          c.addEventListener('click', function () { projSort = c.getAttribute('data-projsort'); draw(); });
        });
      }
      draw();
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      el.innerHTML = ui.emptyState({ icon: '⚠️', title: 'Não consegui carregar', text: 'Tente novamente em instantes.' });
    });
  }

  // ── Detalhe do projeto ────────────────────────────────────────────────
  function projectHeadHtml(p) {
    var meta = (p.clip_count != null ? p.clip_count + ' clipes · ' : '') + (p.avg_score != null ? 'nota média ' + ui.fmtScore(p.avg_score) + ' · ' : '') + ui.timeAgo(p.created_at);
    return '<a class="back-link" href="#/projetos">← Projetos</a>' +
      '<div class="section-head"><div><p class="eyebrow">Projeto</p><h2 class="results-title">' + ui.escapeHtml(p.title || p.filename || 'Projeto') + '</h2>' +
      '<p class="project-meta">' + ui.escapeHtml(meta) + '</p></div>' +
      '<div class="head-actions">' +
        '<button class="btn btn-gold" type="button" data-act="dlall">' + DL + 'Baixar todos</button>' +
        '<button class="btn btn-ghost" type="button" data-act="reprocess">↻ Reprocessar</button>' +
        '<button class="btn btn-danger" type="button" data-act="delete">Excluir</button>' +
      '</div></div>';
  }
  function downloadAll(p) {
    var url = api.downloadAllUrl(p.id);
    ui.toast('Preparando download...', 'info');
    fetch(url, { method: 'GET' }).then(function (r) {
      if (r.ok) {
        var ct = r.headers.get('Content-Type') || '';
        if (ct.indexOf('application/json') >= 0) {
          return r.json().then(function (d) { if (d && d.url) { window.location.href = d.url; } else { ui.toast('Resposta inesperada do servidor.', 'error'); } });
        }
        window.location.href = url; return;
      }
      ui.toast('Baixar todos aguarda o backend (GET /api/projects/:id/download_all).', 'info', { ttl: 6000 });
    }, function () {
      ui.toast('Baixar todos aguarda o backend (GET /api/projects/:id/download_all).', 'info', { ttl: 6000 });
    });
  }
  function openReprocessModal(p) {
    var prefs = store ? store.getPrefs() : { defaultMin: 30, defaultMax: 90, captions: true };
    var html = '<h3 class="modal-title">Reprocessar projeto</h3>' +
      '<p class="modal-text">Gerar novos clipes com outros parâmetros.</p>' +
      '<div class="field row"><div><label for="rp-min">Duração mínima (s)</label><input type="number" id="rp-min" value="' + (prefs.defaultMin || 30) + '" min="5" max="300"></div>' +
      '<div><label for="rp-max">Duração máxima (s)</label><input type="number" id="rp-max" value="' + (prefs.defaultMax || 90) + '" min="10" max="600"></div></div>' +
      '<label class="switch"><input type="checkbox" id="rp-cap" ' + (prefs.captions ? 'checked' : '') + '><span>Gerar legendas</span></label>' +
      '<div class="modal-actions"><button class="btn btn-ghost" type="button" data-act="cancel">Cancelar</button><button class="btn btn-gold" type="button" data-act="go">Reprocessar</button></div>';
    ui.modalOpen(html, { cardClass: 'modal-sm' });
    var root = document.getElementById('modal-root');
    if (!root) return;
    var cancel = root.querySelector('[data-act="cancel"]');
    if (cancel) cancel.addEventListener('click', ui.modalClose);
    var go = root.querySelector('[data-act="go"]');
    if (go) go.addEventListener('click', function () {
      var params = {
        min_duration: parseInt(document.getElementById('rp-min').value, 10),
        max_duration: parseInt(document.getElementById('rp-max').value, 10),
        captions: document.getElementById('rp-cap').checked
      };
      api.reprocess(p.id, params).then(function (res) {
        ui.modalClose();
        if (res && res.job_id && NS.core) { ui.toast('Reprocessamento iniciado', 'success'); NS.router.go('/'); NS.core.trackJob(res.job_id, p.title || p.filename || 'Reprocessamento'); }
        else ui.toast('Reprocessamento solicitado', 'success');
      }, function () {
        ui.modalClose();
        ui.toast('Reprocessar aguarda o backend (POST /api/projects/:id/reprocess).', 'info', { ttl: 6000 });
      });
    });
  }
  function wireProjectHead(p) {
    var head = document.getElementById('projeto-head');
    if (!head) return;
    var dl = head.querySelector('[data-act="dlall"]'); if (dl) dl.addEventListener('click', function () { downloadAll(p); });
    var rp = head.querySelector('[data-act="reprocess"]'); if (rp) rp.addEventListener('click', function () { openReprocessModal(p); });
    var del = head.querySelector('[data-act="delete"]'); if (del) del.addEventListener('click', function () {
      ui.confirm({ title: 'Excluir projeto?', message: 'Isso remove o projeto e seus clipes.', okLabel: 'Excluir', danger: true }).then(function (yes) {
        if (!yes) return;
        api.deleteProject(p.id).then(function (ok) {
          if (ok) { ui.toast('Projeto excluído', 'success'); NS.router.go('/projetos'); }
          else ui.toast('Excluir aguarda o backend (DELETE /api/projects/:id).', 'info', { ttl: 6000 });
        });
      });
    });
  }
  function renderProjeto(params) {
    var id = params && params.id;
    var head = document.getElementById('projeto-head');
    var body = document.getElementById('projeto-body');
    if (head) head.innerHTML = '';
    if (body) body.innerHTML = ui.skeletonGrid(8, 'skel-card');
    api.getProject(id).then(function (p) {
      if (!p) return;
      if (head) { head.innerHTML = projectHeadHtml(p); wireProjectHead(p); }
      if (body) mountClips(body, p.clips || [], { toolbar: true });
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      if (body) body.innerHTML = ui.emptyState({ icon: '⚠️', title: 'Projeto não encontrado' });
    });
  }

  // ── Favoritos ─────────────────────────────────────────────────────────
  function renderFavoritos() {
    var body = document.getElementById('favoritos-body');
    if (!body) return;
    var local = store ? store.favoriteList() : [];
    body.innerHTML = '<div class="view-pad">' + ui.skeletonGrid(6, 'skel-card') + '</div>';
    function done(list) {
      if (!list.length) {
        body.innerHTML = '<section class="fav-empty">' +
          '<div class="hero-glows" aria-hidden="true"><div class="glow glow-b" style="top:20%;left:50%;transform:translateX(-50%);"></div></div>' +
          '<div class="fav-empty-inner">' + arrowBurst('favArrow', '#1F1F24') +
          '<h2 class="fav-empty-title">Sem favoritos ainda</h2>' +
          '<p class="fav-empty-text">Clique no coração de um clipe para guardá-lo aqui e achar rapidinho depois.</p>' +
          '<a class="btn btn-gold" href="#/projetos">Explorar projetos →</a></div></section>';
        return;
      }
      body.innerHTML = '<div class="view-pad" id="fav-grid"></div>';
      mountClips(document.getElementById('fav-grid'), list, { toolbar: list.length > 1 });
    }
    api.getFavorites().then(function (res) {
      var server = (res && res.clips) ? res.clips : [];
      var map = {};
      local.concat(server).forEach(function (c) { if (c && c.id) map[c.id] = c; });
      var list = Object.keys(map).map(function (k) { return map[k]; });
      done(list);
    }, function () { done(local); });
  }

  // ── Configurações ─────────────────────────────────────────────────────
  // ── Segurança › Sessões ativas ────────────────────────────────────────
  function parseUA() {
    var ua = navigator.userAgent || '';
    var browser = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
      : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Navegador';
    var os = /Windows/.test(ua) ? 'Windows' : /(Macintosh|Mac OS X)/.test(ua) ? 'macOS' : /Android/.test(ua) ? 'Android'
      : /(iPhone|iPad|iPod)/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : '';
    return { browser: browser, os: os };
  }
  function deviceIcon(os) {
    var mobile = /iOS|Android/i.test(os || '');
    return mobile
      ? icon('<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>')
      : icon('<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>');
  }
  function sessionRowHtml(s) {
    var dev = ui.escapeHtml((s.browser || 'Navegador') + (s.os ? ' · ' + s.os : ''));
    var meta = ui.escapeHtml([s.location, s.last_active].filter(Boolean).join(' · '));
    var right = s.current
      ? '<span class="session-current">esta sessão</span>'
      : '<button class="btn btn-ghost btn-sm" type="button" data-act="revoke" data-id="' + ui.attr(s.id || '') + '">Encerrar</button>';
    return '<div class="session-row"><span class="session-ic" aria-hidden="true">' + deviceIcon(s.os) + '</span>' +
      '<div class="session-info"><p class="session-dev">' + dev + '</p>' + (meta ? '<p class="session-meta">' + meta + '</p>' : '') + '</div>' +
      right + '</div>';
  }
  function currentSession() { var u = parseUA(); return { current: true, browser: u.browser, os: u.os, last_active: 'agora' }; }
  function loadSessions(container) {
    if (!container || container.dataset.loaded) return;
    container.dataset.loaded = '1';
    container.innerHTML = '<div class="skel" style="height:56px;margin-bottom:8px"></div><div class="skel" style="height:56px"></div>';
    api.getSessions().then(function (res) {
      var list = (res && res.sessions && res.sessions.length) ? res.sessions : [currentSession()];
      container.innerHTML = list.map(sessionRowHtml).join('');
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      // backend ainda não tem a rota: mostra só a sessão atual (real) + nota honesta
      container.innerHTML = sessionRowHtml(currentSession()) +
        '<p class="editor-hint">Outros dispositivos aparecem aqui quando o backend implementar <code>GET /api/sessions</code>.</p>';
      container.dataset.loaded = '';   // permite retry numa próxima abertura da aba
    });
  }

  // Aba Conta: email da conta + "Sua produção" (os números que saíram da Home).
  function loadAccount(emailEl, statsBox) {
    if (emailEl && emailEl.textContent === '—') {
      var ue = document.getElementById('user-email');
      var em = ue && ue.textContent ? ue.textContent : '';
      if (em) emailEl.textContent = em;
      else if (api.me) api.me().then(function (m) { if (m && m.email) emailEl.textContent = m.email; }, function () {});
    }
    if (!statsBox || statsBox.dataset.loaded) return;
    statsBox.dataset.loaded = '1';
    statsBox.innerHTML = '<div class="skel" style="height:78px"></div>';
    api.getStats().then(function (s) {
      if (!s) { statsBox.innerHTML = ''; statsBox.dataset.loaded = ''; return; }
      statsBox.innerHTML = '<div class="stats-grid">' +
        statCardHtml(STAT_ICONS.videos, 'Vídeos processados', s.videos_processed != null ? s.videos_processed : null) +
        statCardHtml(STAT_ICONS.clips, 'Clipes gerados', s.clips_generated != null ? s.clips_generated : null) +
        statCardHtml(STAT_ICONS.star, 'Nota média', s.avg_score != null ? s.avg_score : null, { decimals: true, accent: true }) +
      '</div>';
      var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!reduce) countUpStats(statsBox);
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      statsBox.innerHTML = '<p class="editor-hint">Não consegui carregar agora.</p>';
      statsBox.dataset.loaded = '';
    });
  }

  function configHtml(p) {
    function opt(v, l, sel) { return '<option value="' + v + '"' + (sel ? ' selected' : '') + '>' + l + '</option>'; }
    var langs = [['pt', 'Português'], ['en', 'Inglês'], ['es', 'Espanhol'], ['auto', 'Detectar automaticamente']];
    var langOpts = langs.map(function (x) { return opt(x[0], x[1], p.language === x[0]); }).join('');
    var aspects = [['9:16', '9:16 (Reels/Shorts/TikTok)'], ['1:1', '1:1 (Feed)'], ['16:9', '16:9 (YouTube)']];
    var aspectOpts = aspects.map(function (x) { return opt(x[0], x[1], p.aspect === x[0]); }).join('');
    return '<div class="config-layout">' +
      '<div class="config-subnav" data-subnav>' +
        '<button class="config-subnav-item is-active" type="button" data-tab="prefs">Padrões de clipe</button>' +
        '<button class="config-subnav-item" type="button" data-tab="security">Segurança</button>' +
        '<button class="config-subnav-item" type="button" data-tab="account">Conta e equipe</button>' +
      '</div>' +
      '<div class="config-cards">' +
      '<div class="config-panel" data-panel="prefs">' +
        '<section class="card-block"><p class="eyebrow">Preferências de geração</p><h2 class="results-title">Padrões de clipe</h2>' +
          '<div class="field row"><div><label for="cfg-min">Duração mínima (s)</label><input type="number" id="cfg-min" value="' + p.defaultMin + '" min="5" max="300"></div>' +
          '<div><label for="cfg-max">Duração máxima (s)</label><input type="number" id="cfg-max" value="' + p.defaultMax + '" min="10" max="600"></div></div>' +
          '<div class="field"><label for="cfg-aspect">Formato padrão</label><select id="cfg-aspect">' + aspectOpts + '</select></div>' +
          '<div class="field"><label for="cfg-lang">Idioma da transcrição</label><select id="cfg-lang">' + langOpts + '</select></div>' +
          '<label class="switch"><input type="checkbox" id="cfg-cap" ' + (p.captions ? 'checked' : '') + '><span>Gerar legendas por padrão</span></label>' +
          '<label class="switch"><input type="checkbox" id="cfg-autoplay" ' + (p.autoplayPreview ? 'checked' : '') + '><span>Tocar prévia automaticamente</span></label>' +
          '<button class="btn btn-gold" id="cfg-save" type="button">Salvar preferências</button>' +
        '</section>' +
      '</div>' +
      '<div class="config-panel hidden" data-panel="security">' +
        '<section class="card-block"><p class="eyebrow">Segurança</p><h2 class="results-title">Trocar senha</h2>' +
          '<form id="cfg-pass-form">' +
            '<div class="field"><label for="cfg-cur">Senha atual</label><input type="password" id="cfg-cur" autocomplete="current-password" required></div>' +
            '<div class="field"><label for="cfg-new">Nova senha (mín. 8)</label><input type="password" id="cfg-new" autocomplete="new-password" minlength="8" required></div>' +
            '<div class="field"><label for="cfg-new2">Repita a nova senha</label><input type="password" id="cfg-new2" autocomplete="new-password" minlength="8" required></div>' +
            '<button class="btn btn-gold btn-block" type="submit">Salvar nova senha</button>' +
            '<div id="cfg-pass-error" class="error hidden"></div>' +
          '</form>' +
        '</section>' +
        '<section class="card-block"><p class="eyebrow">Segurança</p><h2 class="results-title">Sessões ativas</h2>' +
          '<p class="modal-text">Onde sua conta está conectada. Encerre qualquer sessão que você não reconhece.</p>' +
          '<div class="sessions" data-sessions></div>' +
          '<button class="btn btn-ghost" type="button" data-act="revoke-all">Sair de todos os outros dispositivos</button>' +
        '</section>' +
      '</div>' +
      '<div class="config-panel hidden" data-panel="account">' +
        '<section class="card-block"><p class="eyebrow">Conta</p><h2 class="results-title">Sua conta</h2>' +
          '<p class="account-email" data-account-email>—</p>' +
          '<p class="eyebrow account-prod-eyebrow">Sua produção</p>' +
          '<div class="account-stats" data-account-stats></div>' +
        '</section>' +
        '<section class="card-block"><p class="eyebrow">Equipe</p><h2 class="results-title">Equipe</h2>' +
          '<p class="modal-text">Em breve: convidar membros e definir papéis.</p></section>' +
      '</div>' +
      '</div>' +
    '</div>';
  }
  function wireConfig() {
    var save = document.getElementById('cfg-save');
    if (save) save.addEventListener('click', function () {
      var patch = {
        defaultMin: parseInt(document.getElementById('cfg-min').value, 10) || 30,
        defaultMax: parseInt(document.getElementById('cfg-max').value, 10) || 90,
        aspect: document.getElementById('cfg-aspect').value,
        language: document.getElementById('cfg-lang').value,
        captions: document.getElementById('cfg-cap').checked,
        autoplayPreview: document.getElementById('cfg-autoplay').checked
      };
      if (patch.defaultMin >= patch.defaultMax) { ui.toast('A duração mínima deve ser menor que a máxima.', 'error'); return; }
      if (store) store.setPrefs(patch);
      if (api) api.putPreferences(patch);
      if (NS.core && NS.core.applyPrefsToUpload) NS.core.applyPrefsToUpload();
      ui.toast('Preferências salvas', 'success');
    });
    var form = document.getElementById('cfg-pass-form');
    if (form) form.addEventListener('submit', function (e) {
      e.preventDefault();
      var err = document.getElementById('cfg-pass-error');
      var cur = document.getElementById('cfg-cur').value;
      var n1 = document.getElementById('cfg-new').value;
      var n2 = document.getElementById('cfg-new2').value;
      function showErr(m) { if (err) { err.textContent = m; err.classList.remove('hidden'); } }
      if (err) err.classList.add('hidden');
      if (n1 !== n2) { showErr('As senhas novas não coincidem.'); return; }
      if (n1.length < 8) { showErr('A nova senha precisa ter ao menos 8 caracteres.'); return; }
      api.changePassword(cur, n1).then(function () { ui.toast('Senha alterada', 'success'); form.reset(); }, function (ex) { showErr((ex && ex.message) || 'Falha ao trocar a senha.'); });
    });

    // Abas da subnav (antes eram decorativas): troca o painel visível.
    var body = document.getElementById('config-body');
    var subnav = body && body.querySelector('[data-subnav]');
    var panels = body ? body.querySelectorAll('[data-panel]') : [];
    var sessionsBox = body && body.querySelector('[data-sessions]');
    function showTab(tab) {
      if (subnav) Array.prototype.forEach.call(subnav.querySelectorAll('[data-tab]'), function (it) {
        it.classList.toggle('is-active', it.getAttribute('data-tab') === tab);
      });
      Array.prototype.forEach.call(panels, function (pn) {
        pn.classList.toggle('hidden', pn.getAttribute('data-panel') !== tab);
      });
      if (tab === 'security') loadSessions(sessionsBox);
      if (tab === 'account') loadAccount(body.querySelector('[data-account-email]'), body.querySelector('[data-account-stats]'));
    }
    if (subnav) subnav.addEventListener('click', function (e) {
      var it = e.target.closest ? e.target.closest('[data-tab]') : null;
      if (it) showTab(it.getAttribute('data-tab'));
    });

    // Sessões: encerrar uma (delegação) e sair de todas as outras.
    if (sessionsBox) sessionsBox.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-act="revoke"]') : null;
      if (!b) return;
      api.revokeSession(b.getAttribute('data-id')).then(function () {
        var row = b.closest('.session-row'); if (row) row.parentNode.removeChild(row);
        ui.toast('Sessão encerrada', 'success');
      }, function () { ui.toast('Encerrar sessão aguarda o backend (DELETE /api/sessions/:id).', 'info', { ttl: 6000 }); });
    });
    var revAll = body && body.querySelector('[data-act="revoke-all"]');
    if (revAll) revAll.addEventListener('click', function () {
      ui.confirm({ title: 'Sair dos outros dispositivos?', message: 'As outras sessões serão encerradas. Esta continua ativa.', okLabel: 'Sair das outras' }).then(function (yes) {
        if (!yes) return;
        api.revokeAllSessions().then(function () {
          ui.toast('Outras sessões encerradas', 'success');
          if (sessionsBox) { sessionsBox.dataset.loaded = ''; loadSessions(sessionsBox); }
        }, function () { ui.toast('Sair de todos aguarda o backend (POST /api/sessions/revoke_all).', 'info', { ttl: 6000 }); });
      });
    });
  }
  function renderConfig() {
    var body = document.getElementById('config-body');
    if (!body) return;
    var p = store ? store.getPrefs() : {};
    body.innerHTML = configHtml(p);
    wireConfig();
  }

  NS.views = {
    normalizeClip: normalizeClip,
    mountClips: mountClips,
    openClipModal: openClipModal,
    clipCard: clipCard,
    renderHome: renderHome,
    renderProjetos: renderProjetos,
    renderProjeto: renderProjeto,
    renderFavoritos: renderFavoritos,
    renderConfig: renderConfig
  };
})();
