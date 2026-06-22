/* OpusSB · howit.js
   Faixa "Como funciona" (.howb): entra no scroll (bloco sobe) e então destaca um
   passo por vez, em loop. Autoiniciável, idempotente, respeita prefers-reduced-motion. */
(function () {
  'use strict';

  function initHowit(root) {
    if (root.dataset.sbInit) return;
    root.dataset.sbInit = '1';
    var steps = Array.prototype.slice.call(root.querySelectorAll('.howb-step'));
    if (!steps.length) return;

    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) return;  // cards estáticos e visíveis; sem entrada nem loop

    root.classList.add('howb--reveal');   // esconde o bloco até a entrada (CSS)

    var n = steps.length, STEP_MS = 1100, HOLD_MS = 1100, current = 0, started = false;

    function paint() {
      for (var i = 0; i < steps.length; i++) {
        if (i === current) steps[i].classList.add('is-active');
        else steps[i].classList.remove('is-active');
      }
    }
    function advance() {
      if (current < n - 1) {
        current++; paint(); setTimeout(advance, STEP_MS);
      } else {
        setTimeout(function () { current = 0; paint(); setTimeout(advance, STEP_MS); }, HOLD_MS);
      }
    }
    function start() {
      if (started) return;
      started = true;
      root.classList.add('howb--in');                 // dispara a entrada do bloco (CSS)
      // O loop de destaque começa depois da entrada terminar.
      setTimeout(function () { paint(); setTimeout(advance, STEP_MS); }, 750);
    }

    if (typeof IntersectionObserver === 'undefined') { start(); return; }
    var io = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting) { start(); io.disconnect(); break; }
      }
    }, { threshold: 0.25 });
    io.observe(root);
  }

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll('.howb'), initHowit);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
