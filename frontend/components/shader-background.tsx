"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

// Porta React do <ShaderAnimation/> (Three.js). three e carregado via dynamic import
// (code-split, igual ao lazy-load antigo). Ciclo de vida no useEffect; forceContextLoss
// no cleanup evita acumulo de contextos WebGL ao entrar/sair da Home. Respeita
// prefers-reduced-motion (nem monta).
type ThreeNS = typeof import("three");

interface ShaderState {
  renderer: import("three").WebGLRenderer;
  geometry: import("three").BufferGeometry;
  material: import("three").Material;
  uniforms: { time: { value: number }; resolution: { value: import("three").Vector2 } };
  animationId: number;
  container: HTMLElement;
  onResize: () => void;
  onVisibility?: () => void;
  stopped: boolean;
  paused: boolean;
}

const VERTEX_SHADER = `void main() { gl_Position = vec4( position, 1.0 ); }`;
const FRAGMENT_SHADER = `#define TWO_PI 6.2831853072
#define PI 3.14159265359
precision highp float;
uniform vec2 resolution;
uniform float time;
void main(void) {
  vec2 uv = (gl_FragCoord.xy * 2.0 - resolution.xy) / min(resolution.x, resolution.y);
  float t = time*0.05;
  float lineWidth = 0.002;
  vec3 color = vec3(0.0);
  for(int j = 0; j < 3; j++){
    for(int i=0; i < 5; i++){
      color[j] += lineWidth*float(i*i) / abs(fract(t - 0.01*float(j)+float(i)*0.01)*5.0 - length(uv) + mod(uv.x+uv.y, 0.2));
    }
  }
  gl_FragColor = vec4(color[0],color[1],color[2],1.0);
}`;

function initShader(THREE: ThreeNS, container: HTMLElement): ShaderState {
  const camera = new THREE.Camera();
  camera.position.z = 1;
  const scene = new THREE.Scene();
  const geometry = new THREE.PlaneGeometry(2, 2);
  const uniforms = {
    time: { value: 1.0 },
    resolution: { value: new THREE.Vector2() },
  };
  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
  });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  container.appendChild(renderer.domElement);

  const state: ShaderState = {
    renderer,
    geometry,
    material,
    uniforms,
    animationId: 0,
    container,
    onResize: () => {},
    stopped: false,
    paused: false,
  };

  function onResize() {
    renderer.setSize(container.clientWidth, container.clientHeight);
    uniforms.resolution.value.x = renderer.domElement.width;
    uniforms.resolution.value.y = renderer.domElement.height;
  }
  state.onResize = onResize;
  onResize();
  window.addEventListener("resize", onResize, false);

  function animate() {
    if (state.stopped || state.paused) return;
    state.animationId = requestAnimationFrame(animate);
    if (container.offsetParent === null) return; // pula GPU quando oculto
    uniforms.time.value += 0.05;
    renderer.render(scene, camera);
  }

  state.onVisibility = () => {
    if (document.hidden) {
      state.paused = true;
      if (state.animationId) cancelAnimationFrame(state.animationId);
    } else if (!state.stopped && state.paused) {
      state.paused = false;
      animate();
    }
  };
  document.addEventListener("visibilitychange", state.onVisibility);

  animate();
  return state;
}

function destroyShader(state: ShaderState) {
  if (state.stopped) return;
  state.stopped = true;
  if (state.animationId) cancelAnimationFrame(state.animationId);
  window.removeEventListener("resize", state.onResize);
  if (state.onVisibility) document.removeEventListener("visibilitychange", state.onVisibility);
  const canvas = state.renderer.domElement;
  try {
    if (canvas.parentNode === state.container) state.container.removeChild(canvas);
  } catch {
    /* ja removido */
  }
  state.renderer.dispose();
  state.geometry.dispose();
  state.material.dispose();
  try {
    state.renderer.forceContextLoss();
  } catch {
    /* sem extensao */
  }
  canvas.width = 0;
  canvas.height = 0;
}

export function ShaderBackground({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const container = ref.current;
    if (!container) return;
    let cancelled = false;
    let state: ShaderState | null = null;
    import("three")
      .then((THREE) => {
        if (cancelled || !container) return;
        state = initShader(THREE, container);
        requestAnimationFrame(() => {
          if (!cancelled) container.classList.add("is-on");
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (state) destroyShader(state);
    };
  }, []);

  return <div ref={ref} className={cn("hero-shader", className)} aria-hidden="true" />;
}
