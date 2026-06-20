/* OpusSB · store.js
   Persistência local (preferências, favoritos, sessão de UI) via localStorage.
   Tudo aqui é "ponte": o backend pode assumir esses dados depois (ver BACKEND_INTEGRATION.md). */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});

  var KEYS = {
    prefs: 'opussb.prefs.v1',
    favorites: 'opussb.favorites.v1',
    lastRoute: 'opussb.lastRoute.v1'
  };

  var DEFAULT_PREFS = {
    defaultMin: 30,
    defaultMax: 90,
    aspect: '9:16',
    captions: true,
    language: 'pt',
    autoplayPreview: false
  };

  function read(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      if (!raw) return fallback;
      return JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; }
  }

  function getPrefs() {
    var p = read(KEYS.prefs, {}) || {};
    var out = {};
    for (var k in DEFAULT_PREFS) {
      if (Object.prototype.hasOwnProperty.call(DEFAULT_PREFS, k)) {
        out[k] = (p[k] !== undefined && p[k] !== null) ? p[k] : DEFAULT_PREFS[k];
      }
    }
    return out;
  }
  function setPrefs(patch) {
    var cur = getPrefs();
    for (var k in patch) {
      if (Object.prototype.hasOwnProperty.call(patch, k)) cur[k] = patch[k];
    }
    write(KEYS.prefs, cur);
    return cur;
  }

  // favoritos: mapa clipId -> snapshot do clipe (para exibir offline em "Favoritos")
  function getFavorites() { return read(KEYS.favorites, {}) || {}; }
  function isFavorite(id) { if (!id) return false; var f = getFavorites(); return !!f[id]; }
  function setFavorite(id, snapshot, on) {
    if (!id) return on;
    var f = getFavorites();
    if (on) { f[id] = snapshot || { id: id }; } else { delete f[id]; }
    write(KEYS.favorites, f);
    return on;
  }
  function favoriteList() {
    var f = getFavorites(); var arr = [];
    for (var id in f) {
      if (Object.prototype.hasOwnProperty.call(f, id)) {
        var v = f[id];
        if (v && typeof v === 'object') arr.push(v);
      }
    }
    return arr;
  }

  function getLastRoute() { return read(KEYS.lastRoute, null); }
  function setLastRoute(r) { write(KEYS.lastRoute, r); }

  NS.store = {
    KEYS: KEYS,
    DEFAULT_PREFS: DEFAULT_PREFS,
    getPrefs: getPrefs,
    setPrefs: setPrefs,
    getFavorites: getFavorites,
    isFavorite: isFavorite,
    setFavorite: setFavorite,
    favoriteList: favoriteList,
    getLastRoute: getLastRoute,
    setLastRoute: setLastRoute
  };
})();
