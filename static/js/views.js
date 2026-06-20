/* OpusSB · views.js
   Componentes de clipe (card, grid com busca/filtro/ordenação, player modal, favoritos) e as páginas
   Dashboard, Projetos, Detalhe do projeto, Favoritos e Configurações. Renderiza em containers do index.html. */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});
  var ui = NS.ui, api = NS.api, store = NS.store;

  var HEART = '♥', EXPAND = '⤢', COPY = '⧉', DL = '⤓', PLAY = '▶';

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
      ? '<video src="' + safeUrl + '" controls playsinline preload="metadata"></video>'
      : '<div class="clip-ph" data-act="max"><span class="clip-ph-glyph" aria-hidden="true">' + PLAY + '</span><span class="clip-ph-note">prévia no modo demonstração</span></div>';
    var reason = clip.reason ? '<p class="clip-reason">' + ui.escapeHtml(clip.reason) + '</p>' : '';
    var dl = clip.url
      ? '<a class="clip-download" href="' + safeUrl + '" download>' + DL + ' Baixar</a>'
      : '<button class="clip-download" type="button" disabled>' + DL + ' Baixar</button>';
    var copy = '<button class="clip-copy" type="button" data-act="copy" title="Copiar link"' + (clip.url ? '' : ' disabled') + '>' + COPY + '</button>';
    var edit = clip.url ? '<button class="clip-edit" type="button" data-act="edit" title="Editar legenda e música">✎ Editar</button>' : '';
    return '<article class="clip-card" data-clip-id="' + ui.attr(clip.id) + '">' +
        '<div class="clip-video">' + media + badge + dur + favBtn + maxBtn + '</div>' +
        '<div class="clip-body"><h3 class="clip-title">' + title + '</h3>' + reason + '</div>' +
        '<div class="clip-actions">' + dl + copy + edit + '</div>' +
      '</article>';
  }

  // ── Player modal ──────────────────────────────────────────────────────
  function openClipModal(clip) {
    var media = clip.url
      ? '<video src="' + ui.escapeHtml(clip.url) + '" controls playsinline autoplay></video>'
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
          (clip.url ? '<a class="btn btn-gold" href="' + ui.escapeHtml(clip.url) + '" download>' + DL + ' Baixar</a>' : '<button class="btn btn-gold" disabled>' + DL + ' Baixar</button>') +
          '<button class="btn btn-ghost" data-act="copy"' + (clip.url ? '' : ' disabled') + '>' + COPY + ' Copiar link</button>' +
          '<button class="btn btn-ghost clip-fav-btn' + (favOn ? ' is-on' : '') + '" data-act="fav">' + HEART + ' ' + (favOn ? 'Favorito' : 'Favoritar') + '</button>' +
        '</div>' +
      '</div></div>';
    ui.modalOpen(html, { cardClass: 'modal-clip' });
    var root = document.getElementById('modal-root');
    if (!root) return;
    var copyBtn = root.querySelector('[data-act="copy"]');
    if (copyBtn && clip.url) copyBtn.addEventListener('click', function () {
      ui.copyToClipboard(clip.url).then(function () { ui.toast('Link copiado', 'success'); }, function () { ui.toast('Não consegui copiar', 'error'); });
    });
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
        '<input type="file" accept="audio/*,.mp3,.m4a,.aac,.wav,.ogg" data-music-file hidden></label>' +
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
      (previewUrl ? '<div class="editor-preview"><video src="' + ui.escapeHtml(previewUrl) + '" controls playsinline preload="metadata"></video></div>' : '') +
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
        if (clip.url) ui.copyToClipboard(clip.url).then(function () { ui.toast('Link copiado', 'success'); }, function () { ui.toast('Não consegui copiar', 'error'); });
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
  function projectCard(p) {
    var avg = p.avg_score != null ? '<span class="score-badge ' + ui.scoreClass(p.avg_score) + '">' + ui.fmtScore(p.avg_score) + '</span>' : '';
    var thumb = p.thumbnail_url
      ? '<img class="project-thumb-img" src="' + ui.attr(p.thumbnail_url) + '" alt="">'
      : '<span class="project-thumb-mark" aria-hidden="true">OpusSB</span>';
    var meta = (p.clip_count != null ? p.clip_count + ' clipes · ' : '') + ui.timeAgo(p.created_at);
    return '<a class="project-card" href="#/projetos/' + ui.attr(p.id) + '">' +
      '<div class="project-thumb">' + thumb + avg + '</div>' +
      '<div class="project-body"><p class="project-title">' + ui.escapeHtml(p.title || p.filename || 'Projeto') + '</p>' +
      '<p class="project-meta">' + ui.escapeHtml(meta) + '</p></div>' +
    '</a>';
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
      : '<span class="featured-mark" aria-hidden="true">OpusSB</span>';
    return '<div class="featured-card">' +
      '<a class="featured-thumb" href="' + href + '">' + thumb + '<span class="featured-tag">Último projeto</span></a>' +
      '<div class="featured-body">' +
        '<div class="featured-score-row"><span class="featured-score" style="color:' + scoreColor + '">' + score + '</span><span class="featured-score-label">nota média dos clipes</span></div>' +
        '<div class="featured-title">' + ui.escapeHtml(p.title || p.filename || 'Projeto') + '</div>' +
        '<div class="featured-meta">' + ui.escapeHtml(meta) + '</div>' +
        '<a class="btn btn-ghost" href="' + href + '">Abrir projeto →</a>' +
      '</div></div>';
  }

  // ── Dashboard / Início ────────────────────────────────────────────────
  function statsHtml(s) {
    function card(label, value) { return '<div class="stat-card"><p class="stat-value">' + value + '</p><p class="stat-label">' + label + '</p></div>'; }
    return '<div class="stats-grid">' +
      card('Vídeos processados', s.videos_processed != null ? s.videos_processed : '—') +
      card('Clipes gerados', s.clips_generated != null ? s.clips_generated : '—') +
      card('Nota média', s.avg_score != null ? ui.fmtScore(s.avg_score) : '—') +
      card('Em processamento', s.processing_count != null ? s.processing_count : '0') +
    '</div>';
  }
  function renderHome() {
    var statsEl = document.getElementById('home-stats');
    var recentEl = document.getElementById('home-recent');
    if (statsEl) statsEl.innerHTML = ui.skeletonGrid(4, 'skel-stat');
    if (recentEl) recentEl.innerHTML = '';
    if (!api) return;
    api.getStats().then(function (s) {
      if (!s) { if (statsEl) statsEl.innerHTML = ''; return; }
      if (statsEl) statsEl.innerHTML = statsHtml(s);
      if (recentEl) {
        var recent = s.recent || [];
        if (!recent.length) { recentEl.innerHTML = ''; return; }
        var head = '<div class="section-head"><div><p class="eyebrow">Recentes</p><h2 class="results-title">Seus projetos</h2></div><a class="link-gold" href="#/projetos">Ver todos →</a></div>';
        var rest = recent.slice(1);
        var grid = rest.length ? '<div class="projects-grid">' + rest.map(projectCard).join('') + '</div>' : '';
        recentEl.innerHTML = head + featuredCard(recent[0]) + grid;
      }
    }, function (e) {
      if (e && e.status === 401) { if (NS.core) NS.core.requireAuth(); return; }
      if (statsEl) statsEl.innerHTML = '';
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
        el.innerHTML = '<div class="clip-bands" style="margin-bottom:24px;">' +
          chip('all', 'Todos · ' + list.length) + chip('score', 'Melhores notas') + chip('recent', 'Mais recentes') + '</div>' +
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
        '<button class="btn btn-gold" type="button" data-act="dlall">' + DL + ' Baixar todos</button>' +
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
  function configHtml(p) {
    function opt(v, l, sel) { return '<option value="' + v + '"' + (sel ? ' selected' : '') + '>' + l + '</option>'; }
    var langs = [['pt', 'Português'], ['en', 'Inglês'], ['es', 'Espanhol'], ['auto', 'Detectar automaticamente']];
    var langOpts = langs.map(function (x) { return opt(x[0], x[1], p.language === x[0]); }).join('');
    var aspects = [['9:16', '9:16 (Reels/Shorts/TikTok)'], ['1:1', '1:1 (Feed)'], ['16:9', '16:9 (YouTube)']];
    var aspectOpts = aspects.map(function (x) { return opt(x[0], x[1], p.aspect === x[0]); }).join('');
    return '<div class="config-layout">' +
      '<div class="config-subnav">' +
        '<div class="config-subnav-item is-active">Padrões de clipe</div>' +
        '<div class="config-subnav-item">Segurança</div>' +
        '<div class="config-subnav-item">Conta e equipe</div>' +
      '</div>' +
      '<div class="config-cards">' +
      '<section class="card-block"><p class="eyebrow">Preferências de geração</p><h2 class="results-title">Padrões de clipe</h2>' +
        '<div class="field row"><div><label for="cfg-min">Duração mínima (s)</label><input type="number" id="cfg-min" value="' + p.defaultMin + '" min="5" max="300"></div>' +
        '<div><label for="cfg-max">Duração máxima (s)</label><input type="number" id="cfg-max" value="' + p.defaultMax + '" min="10" max="600"></div></div>' +
        '<div class="field"><label for="cfg-aspect">Formato padrão</label><select id="cfg-aspect">' + aspectOpts + '</select></div>' +
        '<div class="field"><label for="cfg-lang">Idioma da transcrição</label><select id="cfg-lang">' + langOpts + '</select></div>' +
        '<label class="switch"><input type="checkbox" id="cfg-cap" ' + (p.captions ? 'checked' : '') + '><span>Gerar legendas por padrão</span></label>' +
        '<label class="switch"><input type="checkbox" id="cfg-autoplay" ' + (p.autoplayPreview ? 'checked' : '') + '><span>Tocar prévia automaticamente</span></label>' +
        '<button class="btn btn-gold" id="cfg-save" type="button">Salvar preferências</button>' +
      '</section>' +
      '<section class="card-block"><p class="eyebrow">Segurança</p><h2 class="results-title">Trocar senha</h2>' +
        '<form id="cfg-pass-form">' +
          '<div class="field"><label for="cfg-cur">Senha atual</label><input type="password" id="cfg-cur" autocomplete="current-password" required></div>' +
          '<div class="field"><label for="cfg-new">Nova senha (mín. 8)</label><input type="password" id="cfg-new" autocomplete="new-password" minlength="8" required></div>' +
          '<div class="field"><label for="cfg-new2">Repita a nova senha</label><input type="password" id="cfg-new2" autocomplete="new-password" minlength="8" required></div>' +
          '<button class="btn btn-gold btn-block" type="submit">Salvar nova senha</button>' +
          '<div id="cfg-pass-error" class="error hidden"></div>' +
        '</form>' +
      '</section>' +
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
