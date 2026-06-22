/* OpusSB · api.js
   Camada única de acesso a dados. Endpoints REAIS (já existem no backend) e endpoints FUTUROS
   (ainda não implementados). Para os futuros, se a chamada falhar (404/erro/rede), caímos num MOCK
   e ligamos o "modo demonstração" (badge). Quando o backend implementar a rota, o dado real passa a
   fluir automaticamente, sem mudar o front. Ver BACKEND_INTEGRATION.md. */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});
  var ui = NS.ui;

  var state = { demo: false };
  function markDemo() {
    if (!state.demo) { state.demo = true; if (ui && ui.setDemo) ui.setDemo(true); }
  }

  function authError(status) { var e = new Error('auth'); e.status = status || 401; return e; }

  function jsonOrThrow(resp) {
    return resp.json().then(function (data) {
      if (!resp.ok) {
        var err = new Error((data && data.error) || ('HTTP ' + resp.status));
        err.status = resp.status; err.data = data;
        throw err;
      }
      return data;
    });
  }

  // GET com fallback para mock quando a rota futura ainda não existe.
  function getOrMock(path, mockFn) {
    return fetch(path, { headers: { 'Accept': 'application/json' } }).then(function (resp) {
      if (resp.status === 401) throw authError(401);
      if (resp.ok) {
        return resp.json().then(function (d) { return d; }, function () { markDemo(); return mockFn ? mockFn() : null; });
      }
      markDemo();
      return mockFn ? mockFn() : null;
    }, function (e) {
      if (e && e.status === 401) throw e;
      markDemo();
      return mockFn ? mockFn() : null;
    });
  }

  // ── REAIS (contrato atual do backend) ─────────────────────────────────
  function me() {
    return fetch('/api/me').then(function (r) {
      if (r.status === 401) throw authError(401);
      return r.json();
    });
  }
  function login(email, password) {
    return fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, password: password })
    }).then(jsonOrThrow);
  }
  function logout() {
    return fetch('/api/logout', { method: 'POST' }).then(function (r) {
      return r.json().then(function (d) { return d; }, function () { return {}; });
    }, function () { return {}; });
  }
  function changePassword(cur, nw) {
    return fetch('/api/change_password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: cur, new_password: nw })
    }).then(jsonOrThrow);
  }
  function upload(formData) {
    return fetch('/api/upload', { method: 'POST', body: formData }).then(function (r) {
      if (r.status === 401) throw authError(401);
      return jsonOrThrow(r);
    });
  }
  function status(jobId) {
    return fetch('/api/status/' + encodeURIComponent(jobId)).then(function (r) {
      if (r.status === 401) throw authError(401);
      if (!r.ok) { var e = new Error('status'); e.status = r.status; throw e; }
      return r.json();
    });
  }

  // ── FUTUROS (mock fallback) ───────────────────────────────────────────
  function getStats() { return getOrMock('/api/stats', function () { return NS.mock.stats(); }); }
  function getProjects(params) {
    var q = '';
    try { if (params) q = '?' + new URLSearchParams(params).toString(); } catch (e) {}
    return getOrMock('/api/projects' + q, function () { return NS.mock.projects(params); });
  }
  function getProject(id) {
    return getOrMock('/api/projects/' + encodeURIComponent(id), function () { return NS.mock.project(id); });
  }
  function getFavorites() { return getOrMock('/api/clips/favorites', function () { return NS.mock.favorites(); }); }

  // Mutations: tentam o backend; nunca bloqueiam (o estado também é persistido localmente pelo chamador).
  function favoriteClip(id, on) {
    var path = '/api/clips/' + encodeURIComponent(id) + '/favorite';
    return fetch(path, { method: on ? 'POST' : 'DELETE' }).then(function (r) { return r.ok; }, function () { return false; });
  }
  function deleteProject(id) {
    return fetch('/api/projects/' + encodeURIComponent(id), { method: 'DELETE' }).then(function (r) { return r.ok; }, function () { return false; });
  }
  function cancelJob(id) {
    return fetch('/api/jobs/' + encodeURIComponent(id) + '/cancel', { method: 'POST' }).then(function (r) { return r.ok; }, function () { return false; });
  }
  function reprocess(id, params) {
    return fetch('/api/projects/' + encodeURIComponent(id) + '/reprocess', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params || {})
    }).then(jsonOrThrow);
  }
  function downloadAllUrl(id) { return '/api/projects/' + encodeURIComponent(id) + '/download_all'; }

  // ── Editor de clipe: legenda (texto+tempo) e música ───────────────────
  function getCaptions(id) {
    return fetch('/api/clips/' + encodeURIComponent(id) + '/captions', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { if (r.status === 401) throw authError(401); return jsonOrThrow(r); });
  }
  function putCaptions(id, segments) {
    return fetch('/api/clips/' + encodeURIComponent(id) + '/captions', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ segments: segments })
    }).then(jsonOrThrow);
  }
  function uploadMusic(id, file, volume) {
    var fd = new FormData();
    fd.append('music', file);
    if (volume != null) fd.append('volume', String(volume));
    return fetch('/api/clips/' + encodeURIComponent(id) + '/music', { method: 'POST', body: fd })
      .then(function (r) { if (r.status === 401) throw authError(401); return jsonOrThrow(r); });
  }
  function setMusicVolume(id, volume) {
    return fetch('/api/clips/' + encodeURIComponent(id) + '/music', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ volume: volume })
    }).then(jsonOrThrow);
  }
  function removeMusic(id) {
    return fetch('/api/clips/' + encodeURIComponent(id) + '/music', { method: 'DELETE' }).then(jsonOrThrow);
  }
  function putPreferences(prefs) {
    return fetch('/api/preferences', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(prefs)
    }).then(function (r) { return r.ok; }, function () { return false; });
  }

  // ── Segurança › sessões ativas (FUTURO) ───────────────────────────────
  // Contrato esperado do backend:
  //   GET    /api/sessions             -> { sessions: [{id, current, browser, os, location, last_active}] }
  //   DELETE /api/sessions/<id>        -> { ok }
  //   POST   /api/sessions/revoke_all  -> { ok }
  function getSessions() {
    return fetch('/api/sessions', { headers: { 'Accept': 'application/json' } }).then(function (r) {
      if (r.status === 401) throw authError(401);
      if (!r.ok) { var e = new Error('sessions'); e.status = r.status; throw e; }
      return r.json();
    });
  }
  function revokeSession(id) {
    return fetch('/api/sessions/' + encodeURIComponent(id), { method: 'DELETE' }).then(jsonOrThrow);
  }
  function revokeAllSessions() {
    return fetch('/api/sessions/revoke_all', { method: 'POST' }).then(jsonOrThrow);
  }

  NS.api = {
    isDemo: function () { return state.demo; },
    me: me, login: login, logout: logout, changePassword: changePassword, upload: upload, status: status,
    getStats: getStats, getProjects: getProjects, getProject: getProject, getFavorites: getFavorites,
    favoriteClip: favoriteClip, deleteProject: deleteProject, cancelJob: cancelJob, reprocess: reprocess,
    downloadAllUrl: downloadAllUrl, putPreferences: putPreferences,
    getCaptions: getCaptions, putCaptions: putCaptions,
    uploadMusic: uploadMusic, setMusicVolume: setMusicVolume, removeMusic: removeMusic,
    getSessions: getSessions, revokeSession: revokeSession, revokeAllSessions: revokeAllSessions
  };

  // ── MOCK (dados de demonstração enquanto o backend não responde) ──────
  var SAMPLE = [
    { id: 'p1', title: 'ClubCast · Como fazer um produto simples se tornar irresistível', filename: 'clubcast-ep14.mp4', created_at: daysAgo(0), duration: 4127, clip_count: 9, avg_score: 8.1, status: 'done' },
    { id: 'p2', title: 'Crescer sem perder a essência: os dilemas da expansão', filename: 'clubcast-ep13.mp4', created_at: daysAgo(1), duration: 3890, clip_count: 7, avg_score: 7.3, status: 'done' },
    { id: 'p3', title: 'Quando tradição e inovação disputam o mesmo CNPJ', filename: 'clubcast-ep12.mp4', created_at: daysAgo(3), duration: 4502, clip_count: 11, avg_score: 7.8, status: 'done' },
    { id: 'p4', title: 'O meu negócio precisa de seguro?', filename: 'clubcast-ep11.mp4', created_at: daysAgo(6), duration: 3320, clip_count: 6, avg_score: 6.6, status: 'done' }
  ];
  var REASONS = [
    'Gancho forte nos 3 primeiros segundos e tensão narrativa.',
    'Declaração polêmica com alto potencial de compartilhamento.',
    'História pessoal com virada emocional clara.',
    'Dado surpreendente que contraria o senso comum.',
    'Frase de efeito curta, fácil de legendar e citar.',
    'Pergunta retórica que prende e gera comentários.'
  ];
  var TITLES = [
    'A regra de ouro que ninguém te conta',
    'O erro de R$1 milhão',
    'Por que você está fazendo isso errado',
    'O segredo dos que crescem rápido',
    'Ninguém fala sobre isso, mas deveria',
    'A pergunta que mudou tudo',
    'Faça isso antes de escalar',
    'O que aprendi do jeito difícil'
  ];

  function daysAgo(n) {
    var d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString();
  }
  function clipsFor(p) {
    var out = [];
    var count = p.clip_count || 6;
    for (var i = 0; i < count; i++) {
      var score = Math.max(3, Math.min(9.6, (p.avg_score || 7) + ((i % 5) - 2) * 0.6));
      out.push({
        id: p.id + '-c' + (i + 1),
        project_id: p.id,
        url: '',                      // demo: sem URL presignada real (placeholder no card)
        thumbnail_url: '',
        title: TITLES[i % TITLES.length],
        reason: REASONS[i % REASONS.length],
        score: Math.round(score * 10) / 10,
        duration: 30 + (i % 4) * 15,
        favorite: false,
        demo: true
      });
    }
    return out;
  }

  NS.mock = {
    stats: function () {
      var clips = 0, sum = 0;
      for (var i = 0; i < SAMPLE.length; i++) { clips += SAMPLE[i].clip_count; sum += SAMPLE[i].avg_score; }
      return {
        videos_processed: SAMPLE.length,
        clips_generated: clips,
        avg_score: Math.round((sum / SAMPLE.length) * 10) / 10,
        processing_count: 0,
        recent: SAMPLE.slice(0, 4),
        demo: true
      };
    },
    projects: function () { return { projects: SAMPLE.slice(), total: SAMPLE.length, demo: true }; },
    project: function (id) {
      var p = SAMPLE[0];
      for (var i = 0; i < SAMPLE.length; i++) { if (String(SAMPLE[i].id) === String(id)) { p = SAMPLE[i]; break; } }
      var copy = {}; for (var k in p) { if (Object.prototype.hasOwnProperty.call(p, k)) copy[k] = p[k]; }
      copy.clips = clipsFor(p);
      copy.demo = true;
      return copy;
    },
    favorites: function () {
      var local = (NS.store ? NS.store.favoriteList() : []);
      return { clips: local, demo: true };
    }
  };
})();
