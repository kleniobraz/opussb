/* OpusSB · router.js
   Roteador hash-based simples (#/, #/projetos, #/projetos/:id, #/favoritos, #/config). */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});

  var routes = [];   // { segs: [], handler, name }
  var onAfter = null;

  function register(path, handler, name) {
    var segs = String(path).split('/').filter(Boolean);
    routes.push({ segs: segs, handler: handler, name: name || path });
  }

  function parseHash() {
    var h = window.location.hash || '';
    h = h.replace(/^#/, '').replace(/^\//, '');
    if (h === '') return [];
    return h.split('/').filter(Boolean).map(function (s) {
      try { return decodeURIComponent(s); } catch (e) { return s; }
    });
  }

  function match(segs) {
    for (var i = 0; i < routes.length; i++) {
      var r = routes[i];
      if (r.segs.length !== segs.length) continue;
      var params = {}, ok = true;
      for (var j = 0; j < r.segs.length; j++) {
        var rs = r.segs[j];
        if (rs.charAt(0) === ':') params[rs.slice(1)] = segs[j];
        else if (rs !== segs[j]) { ok = false; break; }
      }
      if (ok) return { route: r, params: params };
    }
    return null;
  }

  function resolve() {
    var segs = parseHash();
    var m = match(segs);
    if (!m) { m = match([]); }            // fallback para home
    if (!m) return;
    try { m.route.handler(m.params || {}); } catch (e) { if (window.console) console.error(e); }
    if (onAfter) { try { onAfter(m.route.name, m.params || {}); } catch (e2) {} }
    var route = '#/' + segs.join('/');
    if (NS.store) NS.store.setLastRoute(route);
  }

  function go(hash) {
    var target = hash.charAt(0) === '#' ? hash : ('#' + (hash.charAt(0) === '/' ? hash : '/' + hash));
    if (window.location.hash === target) { resolve(); }
    else { window.location.hash = target; }
  }

  function start(afterCb) {
    onAfter = afterCb || null;
    window.addEventListener('hashchange', resolve);
    resolve();
  }

  function current() { return parseHash(); }

  NS.router = { register: register, go: go, start: start, current: current, resolve: resolve };
})();
