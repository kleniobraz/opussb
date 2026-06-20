/* OpusSB · howit.js
   Anima a faixa "Como funciona" (.howb): destaca um passo por vez, em loop.
   Autoiniciável, idempotente, respeita prefers-reduced-motion. */
(function () {
  'use strict';

  function initHowit(root) {
    if (root.dataset.sbInit) return;
    root.dataset.sbInit = '1';
    var steps = Array.prototype.slice.call(root.querySelectorAll('.howb-step'));
    if (!steps.length) return;

    var n = steps.length, STEP_MS = 1100, HOLD_MS = 1100, current = 0;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function paint() {
      for (var i = 0; i < steps.length; i++) {
        if (i === current) steps[i].classList.add('is-active');
        else steps[i].classList.remove('is-active');
      }
    }
    if (reduce) return; // cards estáticos já estilizados; sem animação

    function advance() {
      if (current < n - 1) {
        current++; paint(); setTimeout(advance, STEP_MS);
      } else {
        setTimeout(function () { current = 0; paint(); setTimeout(advance, STEP_MS); }, HOLD_MS);
      }
    }
    paint();
    setTimeout(advance, STEP_MS);
  }

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll('.howb'), initHowit);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
