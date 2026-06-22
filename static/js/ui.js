/* OpusSB · ui.js
   Componentes de UI reutilizáveis: toasts, modal, confirmação, skeletons, estados vazios,
   helpers de formatação e badge de "modo demonstração". Sem dependências externas. */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function attr(s) { return escapeHtml(s); }

  function fmtBytes(bytes) {
    if (bytes == null || isNaN(bytes)) return '';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0, n = bytes;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
  }

  function fmtScore(score) {
    var r = Math.round(score * 10) / 10;
    return (r % 1 === 0) ? String(r) : r.toFixed(1);
  }
  function scoreClass(score) {
    if (score >= 8) return 'score-high';
    if (score >= 6) return 'score-mid';
    return 'score-low';
  }
  function fmtSeconds(s) {
    s = Math.round(s || 0);
    var m = Math.floor(s / 60), ss = s % 60;
    return m + ':' + (ss < 10 ? '0' : '') + ss;
  }
  function timeAgo(iso) {
    if (!iso) return '';
    var t = new Date(iso).getTime();
    if (isNaN(t)) return '';
    var sec = Math.floor((Date.now() - t) / 1000);
    if (sec < 60) return 'agora';
    var min = Math.floor(sec / 60); if (min < 60) return 'há ' + min + ' min';
    var h = Math.floor(min / 60); if (h < 24) return 'há ' + h + ' h';
    var days = Math.floor(h / 24); if (days < 30) return 'há ' + days + ' d';
    return new Date(t).toLocaleDateString('pt-BR');
  }

  // ── Toasts ────────────────────────────────────────────────────────────
  function toast(message, type, opts) {
    var root = document.getElementById('toast-root');
    if (!root) return function () {};
    opts = opts || {};
    var el = document.createElement('div');
    el.className = 'toast toast-' + (type || 'info');
    el.setAttribute('role', 'status');
    var ic = { success: '✓', error: '!', info: 'i' }[type || 'info'] || 'i';
    el.innerHTML = '<span class="toast-ic" aria-hidden="true">' + ic + '</span><span class="toast-msg">' + escapeHtml(message) + '</span>';
    if (opts.actionLabel) {
      var b = document.createElement('button');
      b.className = 'toast-action'; b.type = 'button'; b.textContent = opts.actionLabel;
      b.addEventListener('click', function () { try { if (opts.onAction) opts.onAction(); } finally { dismiss(); } });
      el.appendChild(b);
    }
    root.appendChild(el);
    var ttl = opts.ttl || (type === 'error' ? 6000 : 3500);
    var timer = setTimeout(dismiss, ttl);
    function dismiss() {
      clearTimeout(timer);
      el.classList.add('toast-out');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }
    return dismiss;
  }

  // ── Modal ─────────────────────────────────────────────────────────────
  var modalState = { lastFocus: null, onClose: null };
  function modalOpen(html, opts) {
    opts = opts || {};
    var root = document.getElementById('modal-root');
    if (!root) return null;
    modalState.lastFocus = document.activeElement;
    modalState.onClose = opts.onClose || null;
    root.innerHTML =
      '<div class="modal-backdrop" data-close="1"></div>' +
      '<div class="modal-card ' + (opts.cardClass || '') + '" role="document" tabindex="-1">' +
        '<button class="modal-x" type="button" aria-label="Fechar" data-close="1">&times;</button>' +
        '<div class="modal-body">' + html + '</div>' +
      '</div>';
    root.classList.remove('hidden');
    document.body.classList.add('modal-open');
    root.addEventListener('click', backdropClick);
    document.addEventListener('keydown', escClose);
    var card = root.querySelector('.modal-card');
    if (card && card.focus) card.focus();
    return root;
  }
  function backdropClick(e) {
    if (e.target && e.target.getAttribute && e.target.getAttribute('data-close') === '1') modalClose();
  }
  function escClose(e) { if (e.key === 'Escape') modalClose(); }
  function modalClose() {
    var root = document.getElementById('modal-root');
    if (!root || root.classList.contains('hidden')) return;
    root.classList.add('hidden');
    root.innerHTML = '';
    document.body.classList.remove('modal-open');
    root.removeEventListener('click', backdropClick);
    document.removeEventListener('keydown', escClose);
    if (modalState.onClose) { try { modalState.onClose(); } catch (e) {} }
    if (modalState.lastFocus && modalState.lastFocus.focus) { try { modalState.lastFocus.focus(); } catch (e) {} }
  }

  function confirm(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var html =
        '<h3 class="modal-title">' + escapeHtml(opts.title || 'Confirmar') + '</h3>' +
        '<p class="modal-text">' + escapeHtml(opts.message || '') + '</p>' +
        '<div class="modal-actions">' +
          '<button class="btn btn-ghost" type="button" data-act="cancel">' + escapeHtml(opts.cancelLabel || 'Cancelar') + '</button>' +
          '<button class="btn ' + (opts.danger ? 'btn-danger' : 'btn-gold') + '" type="button" data-act="ok">' + escapeHtml(opts.okLabel || 'Confirmar') + '</button>' +
        '</div>';
      modalOpen(html, { cardClass: 'modal-sm' });
      var root = document.getElementById('modal-root');
      var cancel = root.querySelector('[data-act="cancel"]');
      var ok = root.querySelector('[data-act="ok"]');
      if (cancel) cancel.addEventListener('click', function () { modalClose(); resolve(false); });
      if (ok) ok.addEventListener('click', function () { modalClose(); resolve(true); });
    });
  }

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.focus(); ta.select();
        document.execCommand('copy'); document.body.removeChild(ta); resolve();
      } catch (e) { reject(e); }
    });
  }

  function skeletonGrid(count, kind) {
    var cells = '';
    for (var i = 0; i < (count || 6); i++) cells += '<div class="skel ' + (kind || 'skel-card') + '"></div>';
    return '<div class="skel-grid">' + cells + '</div>';
  }

  function emptyState(opts) {
    opts = opts || {};
    return '<div class="empty">' +
      '<div class="empty-icon" aria-hidden="true">' + (opts.icon || '🎬') + '</div>' +
      '<p class="empty-title">' + escapeHtml(opts.title || 'Nada por aqui ainda') + '</p>' +
      (opts.text ? '<p class="empty-text">' + escapeHtml(opts.text) + '</p>' : '') +
      (opts.actionHtml || '') +
      '</div>';
  }

  // Badge de modo demonstração: aparece quando algum dado veio de mock (backend ainda não implementou)
  function setDemo(on) {
    var b = document.getElementById('demo-badge');
    if (b && on) b.classList.remove('hidden');
  }

  NS.ui = {
    escapeHtml: escapeHtml, attr: attr,
    fmtBytes: fmtBytes, fmtScore: fmtScore, scoreClass: scoreClass, fmtSeconds: fmtSeconds, timeAgo: timeAgo,
    toast: toast, modalOpen: modalOpen, modalClose: modalClose, confirm: confirm,
    copyToClipboard: copyToClipboard, skeletonGrid: skeletonGrid, emptyState: emptyState, setDemo: setDemo
  };
})();
