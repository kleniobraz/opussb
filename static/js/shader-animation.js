/* OpusSB · shader-animation.js
   Porta vanilla do componente React <ShaderAnimation/> (Three.js). Mesmo shader
   (linhas radiais animadas), sem React/JSX/build. Uso:

     var anim = OpusSB.shaderAnimation.mount('#meu-container');
     // ...
     anim.destroy();   // para o loop, remove o canvas e libera a GPU

   O Three.js (static/js/vendor/three.min.js, ~600KB) é carregado SOB DEMANDA na
   1a montagem — não pesa nas páginas que não usam o shader. */
(function () {
  'use strict';
  var NS = (window.OpusSB = window.OpusSB || {});

  var THREE_SRC = '/js/vendor/three.min.js';
  var _threePromise = null;

  // Carrega o Three uma única vez (single-flight); resolve quando window.THREE existe.
  function loadThree() {
    if (window.THREE) return Promise.resolve(window.THREE);
    if (_threePromise) return _threePromise;
    _threePromise = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = THREE_SRC;
      s.async = true;
      s.onload = function () {
        if (window.THREE) resolve(window.THREE);
        // zera o cache: uma falha não pode ficar grudada e matar o shader até reload.
        else { _threePromise = null; reject(new Error('three.min.js carregou mas window.THREE está ausente')); }
      };
      s.onerror = function () { _threePromise = null; reject(new Error('Falha ao carregar ' + THREE_SRC)); };
      document.head.appendChild(s);
    });
    return _threePromise;
  }

  var VERTEX_SHADER = [
    'void main() {',
    '  gl_Position = vec4( position, 1.0 );',
    '}'
  ].join('\n');

  var FRAGMENT_SHADER = [
    '#define TWO_PI 6.2831853072',
    '#define PI 3.14159265359',
    'precision highp float;',
    'uniform vec2 resolution;',
    'uniform float time;',
    'void main(void) {',
    '  vec2 uv = (gl_FragCoord.xy * 2.0 - resolution.xy) / min(resolution.x, resolution.y);',
    '  float t = time*0.05;',
    '  float lineWidth = 0.002;',
    '  vec3 color = vec3(0.0);',
    '  for(int j = 0; j < 3; j++){',
    '    for(int i=0; i < 5; i++){',
    '      color[j] += lineWidth*float(i*i) / abs(fract(t - 0.01*float(j)+float(i)*0.01)*5.0 - length(uv) + mod(uv.x+uv.y, 0.2));',
    '    }',
    '  }',
    '  gl_FragColor = vec4(color[0],color[1],color[2],1.0);',
    '}'
  ].join('\n');

  function resolveEl(target) {
    if (!target) return null;
    if (typeof target === 'string') return document.querySelector(target);
    if (target.nodeType === 1) return target;
    return null;
  }

  // Inicializa a cena no container (já com THREE disponível). Devolve um objeto de
  // estado com tudo que o cleanup precisa. opts:
  //   maxPixelRatio   - teto do devicePixelRatio (perf em telas retina/mobile)
  //   pauseWhenHidden - pausa o loop RAF quando a aba fica oculta (economiza GPU)
  function init(THREE, container, opts) {
    opts = opts || {};
    var camera = new THREE.Camera();
    camera.position.z = 1;

    var scene = new THREE.Scene();
    var geometry = new THREE.PlaneGeometry(2, 2);

    var uniforms = {
      time: { type: 'f', value: 1.0 },
      resolution: { type: 'v2', value: new THREE.Vector2() }
    };

    var material = new THREE.ShaderMaterial({
      uniforms: uniforms,
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER
    });

    var mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    var renderer = new THREE.WebGLRenderer({ antialias: true });
    var pr = window.devicePixelRatio || 1;
    if (opts.maxPixelRatio) pr = Math.min(pr, opts.maxPixelRatio);
    renderer.setPixelRatio(pr);
    container.appendChild(renderer.domElement);

    var state = {
      camera: camera, scene: scene, renderer: renderer,
      geometry: geometry, material: material, uniforms: uniforms,
      animationId: 0, container: container, onResize: null, onVisibility: null,
      stopped: false, paused: false
    };

    function onWindowResize() {
      var width = container.clientWidth;
      var height = container.clientHeight;
      renderer.setSize(width, height);
      uniforms.resolution.value.x = renderer.domElement.width;
      uniforms.resolution.value.y = renderer.domElement.height;
    }
    state.onResize = onWindowResize;

    onWindowResize();
    window.addEventListener('resize', onWindowResize, false);

    function animate() {
      if (state.stopped || state.paused) return;
      state.animationId = requestAnimationFrame(animate);
      // Pula o trabalho de GPU quando o container está oculto (ex.: prefers-reduced-motion
      // liga display:none em runtime). Mantém o RAF agendado e retoma sozinho ao reaparecer.
      if (container.offsetParent === null) return;
      uniforms.time.value += 0.05;
      renderer.render(scene, camera);
    }

    if (opts.pauseWhenHidden) {
      state.onVisibility = function () {
        if (document.hidden) {
          state.paused = true;
          if (state.animationId) cancelAnimationFrame(state.animationId);
        } else if (!state.stopped && state.paused) {
          state.paused = false;
          animate();
        }
      };
      document.addEventListener('visibilitychange', state.onVisibility);
    }

    animate();

    return state;
  }

  function destroyState(state) {
    if (!state || state.stopped) return;
    state.stopped = true;
    if (state.animationId) cancelAnimationFrame(state.animationId);
    if (state.onResize) window.removeEventListener('resize', state.onResize);
    if (state.onVisibility) document.removeEventListener('visibilitychange', state.onVisibility);
    var canvas = state.renderer.domElement;
    try {
      if (state.container && canvas && canvas.parentNode === state.container) {
        state.container.removeChild(canvas);
      }
    } catch (e) { /* já removido */ }
    state.renderer.dispose();
    state.geometry.dispose();
    state.material.dispose();
    // dispose() do r128 NÃO solta o contexto WebGL (depende do GC). Sem forceContextLoss,
    // entrar/sair da Home muitas vezes acumula contextos até o teto do browser (~16) e o
    // hero fica preto. Forçamos a liberação na hora e zeramos o canvas.
    try { if (state.renderer.forceContextLoss) state.renderer.forceContextLoss(); } catch (e) { /* sem extensão */ }
    if (canvas) { canvas.width = 0; canvas.height = 0; }
  }

  // mount(target) -> controlador { destroy() }. target: seletor CSS ou elemento.
  // A montagem é assíncrona (lazy-load do Three); destroy() é seguro de chamar
  // antes mesmo do Three terminar de carregar.
  function mount(target, opts) {
    opts = opts || {};
    var container = resolveEl(target);
    if (!container) {
      console.warn('[shader-animation] container não encontrado:', target);
      return { destroy: function () {} };
    }
    container.classList.add('shader-anim');

    var ctrl = { state: null, cancelled: false };
    loadThree().then(function (THREE) {
      if (ctrl.cancelled) return;          // destroy() chamado antes do Three carregar
      ctrl.state = init(THREE, container, opts);
    }, function (err) {
      console.error('[shader-animation]', err);
      if (typeof opts.onError === 'function') opts.onError(err);
    });

    return {
      destroy: function () {
        ctrl.cancelled = true;
        destroyState(ctrl.state);
        ctrl.state = null;
      }
    };
  }

  NS.shaderAnimation = {
    mount: mount,
    loadThree: loadThree,
    VERTEX_SHADER: VERTEX_SHADER,
    FRAGMENT_SHADER: FRAGMENT_SHADER
  };
})();
