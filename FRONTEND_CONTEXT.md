# OpusSB · FRONTEND_CONTEXT.md

Fonte de verdade do **frontend**. A partir de 2026-06-21 o front está sendo **migrado da SPA
vanilla (`static/`) para um app Next.js (`frontend/`)**. Este doc descreve o app novo (stack,
arquitetura, telas, decisões), o que já foi feito, o que falta, e os toques (aditivos) que a
migração fez no backend. Complementa `BACKEND_CONTEXT.md` / `BACKEND_INTEGRATION.md` (contrato
de API) e a memória do projeto (`frontend-stack-decision`).

> Constraints do dono respeitados em tudo: sem travessão em texto gerado; decisão de
> arquitetura/custo alinhada antes; push na master só com autorização; `.py` editado por
> substituição exata (nunca pela metade); restart do Flask só com `active_jobs=0` + backup.

---

## 0. Estado atual (TL;DR)

- **App novo:** `/opt/opussb/frontend/` (Next.js). **CUTOVER FEITO em 2026-06-21:** produção
  (Nginx:80) agora serve o **Next** via serviço systemd `opussb-web` (`next start` em
  127.0.0.1:3000). A SPA vanilla em `static/` fica intocada para **rollback** (ver §2).
- **Feito e aprovado:** Fase 1 (fatia vertical: casca + login + Home), Fase 2 (todas as views:
  Projetos, Detalhe, Favoritos, Config), **Editor de clipe estilo OpusClip** (rota
  `/editar/[clipId]`), **Fase 3 cutover**, e **aviso de alterações não salvas** no editor.
- **Ondas seguintes (2026-06-22, todas em produção):** rebrand SB Clips + login Zoho + painel
  admin + navbar nova + preloader (§11), player custom do clipe (§12), projetos compartilhados
  (§13), e personalização de fonte da legenda + importar do YouTube + console estilo OpusClip
  + hero preenchendo a viewport (§14). As seções 0-10 são da migração inicial; onde divergirem
  das §11-14, **as §11-14 valem** (são mais recentes).
- **Ondas 2026-06-23/24 (em produção):** corte ajustável por handles (§15), lacuna da Home (§16),
  lazy-load anti-estampida (§18) e **"Baixar todos" / download_all (§19)**. E, como PLANO (nada
  implementado ainda): o **overhaul de usabilidade do editor (§20)** + a Fase 1 do crop por keyframe
  (BACKEND §24), ambos a serem feitos em STAGING (decisão do dono). O reframe por GEOMETRIA (anti-vazio
  multicam) está em BACKEND §24, e o estado atual + próximos passos em BACKEND §25.
- **Onda 2026-06-25 (em produção, §21):** painel da transcrição **para de seguir o vídeo ao editar**;
  diálogo do clipe **maior** + **pausa o card ao abrir** (fim de 2 vídeos juntos); projetos com **poster
  real por clipe** + **"Ver mais"** + **loading lazy**; e a **waveform REAL** na camada de áudio do editor.
  Mais a **legenda sincronizada** (tempo por palavra, BACKEND §26). **Editor estilo OpusClip:** decisão
  fechada (§22).
- **Tela final CLUBCAST (§23) + camada de vídeo + biblioteca de telas finais + encurtar (§24): EM PRODUÇÃO.**
  Tela final por projeto (removível/encurtável por clipe); filmstrip real no editor; subir/trocar/excluir
  telas finais na página do projeto.
- **Painel de saldos no admin (§25, em produção):** aba "Saldos" no /painel com o saldo da RunPod (real,
  via API); o custo da Claude fica parado (a org é "Individual", sem Admin API; ver BACKEND §32/§34).
- **PRÓXIMA GRANDE FRENTE (decidida 2026-06-26, NÃO começada): editor de crop por KEYFRAME estilo OpusClip
  + multi-apresentador**, em STAGING. Decisões e arquitetura em **BACKEND §33** (front: §26 abaixo).
- **Onda 2026-06-30 (em produção):** crop por keyframe LIVE (§27 / BACKEND §35-37) **e** o **editor
  "timeline-first" (roteiro corrido) + layout estilo Premiere** + Ctrl+S + pop-up de sair-sem-salvar +
  música no preview e no export sem legenda + atalho de zoom `-`/`+` + fix de z-index dos diálogos (**§28**;
  backend em BACKEND §38). Só a **Parte 5 (karaokê por PALAVRA)** ficou pendente (o dado `words` já flui).
- **Onda 2026-07-01 (docs/design, SEM deploy de código):** DESIGN aprovado do **pipeline de Podcast completo
  pra YouTube (16:9)**, paralelo ao de clipes (§29 abaixo / BACKEND §39); e **migração de VPS** planejada (o S3
  da Hetzner fica, só a máquina muda; BACKEND §40). Staging parado.
- **Onda 2026-07-01→02 (infra, SEM deploy de código): migração de VPS CONCLUÍDA** — produção agora
  na **Hostinger, Campinas/SP (4 vCPU / 16GB / 200GB)**, IP `187.127.43.75`; DNS + certs Let's
  Encrypt re-emitidos, N8N 2.25.7 local. Front: mesmo código, `next build` refeito na VPS nova,
  unit `opussb-web` recriado igual (root + nvm). Detalhes e lacunas pós-migração: BACKEND §42.
- **Git:** `origin/master` em `055cf59` (docs; o código de produção segue em `4c87293`, deploy 2026-06-30 §28).
  Docs de contexto atualizadas 2026-07-01 (FRONTEND §29 + BACKEND §39-41).
- **Falta (pendente):** validar a tela final num **clipe real da Carbone**; o **editor de crop por keyframe**
  (§26 / BACKEND §33, staging); o **editor estilo OpusClip texto-corrido** (§22, staging); remover a SPA
  `static/`; popstate no editor (§7/§9). Estado completo + git em **BACKEND §34**.

---

## 1. Stack e decisões

- **Next.js 16 (App Router) + React 19 + TypeScript**. **Tailwind v4** (config CSS-first via
  `@theme`, sem `tailwind.config`) + **shadcn/ui** (estilo `radix-nova`, primitivos **Radix**) +
  **lucide-react** (ícones). **TanStack Query v5** (estado de servidor, cache, polling,
  mutations). **three.js** (shader do hero, via dynamic import). **sonner** (toasts).
- **Toolchain:** Node 20 LTS via **nvm sob o root** (`source ~/.nvm/nvm.sh`). O Node 12 do
  sistema fica intocado.
- **Por que Next (e não Vite):** há sinal real de transformar o OpusSB em produto. Next + shadcn
  é o caminho "no-regrets" (marketing/SSR/SEO entram sem reescrever; migra pra Vercel/CF se
  escalar). Ver memória `frontend-stack-decision`. Detalhe técnico: descartado Next export
  estático por causa da rota dinâmica `/projetos/[id]`.
- **Roteamento:** App Router, tudo **client** (`"use client"`, app atrás de login, sem SSG).
  Redirect dos links hash antigos (`#/projetos` → `/projetos`) via `components/hash-redirect.tsx`.
- **Sessão:** cookie same-origin (Nginx em prod; rewrite do Next em dev). Zero CORS/token. 401 de
  qualquer query dispara um evento global que derruba pro `/login`.
- **Sem modo demonstração/mock:** o backend já implementa quase tudo; queries reais + estados de
  erro/vazio. (Falta só `reprocess`, que degrada em toast; `download_all` **FEITO**, §19.)

---

## 2. Rodar / build / deploy

- **Dev:** `cd frontend && source ~/.nvm/nvm.sh && nvm use 20 && npm run dev -- -H 127.0.0.1 -p 3000`.
  `next.config.ts` faz `rewrites` de `/api/*` → `http://127.0.0.1:5001` (Flask), então o cookie
  funciona same-origin. Bind em **127.0.0.1** (o projeto não escuta em 0.0.0.0); acessar por túnel
  SSH (`ssh -L 3000:127.0.0.1:3000 <vps>`).
- **Type-check:** `npx tsc --noEmit`. **Build:** `npm run build` (rodar com o dev parado pra não
  brigar no `.next`; e fora do processamento de job, pelo pico de RAM do build).
- **Backend:** mudança em `.py` exige `systemctl restart opussb`. **Sempre** checar
  `/health` (`active_jobs` deve ser 0) e fazer backup (`opussb.db` com checkpoint do WAL + tarball
  do código em `/opt/opussb_backups/`) antes.
- **Cutover (Fase 3, FEITO em 2026-06-21):** unit systemd **`opussb-web`**
  (`/etc/systemd/system/opussb-web.service`): `next start -H 127.0.0.1 -p 3000`,
  WorkingDirectory `frontend/`, `enabled` no boot, `Restart=on-failure`. Roda como **root**
  (o Node 20 vive em `/root/.nvm`, que é `700`, então `www-data` não consegue executá-lo;
  serviço só escuta em 127.0.0.1 atrás do Nginx — hardening futuro: node em path de sistema +
  `User=www-data`). Nginx (`/etc/nginx/sites-enabled/opussb`): `location / { proxy_pass
  http://127.0.0.1:3000 }` (Next serve `/_next/static`); `/api`+`/health` seguem pro Flask;
  guard de `/api/(callback|internal)` mantido. **Rollback:** no arquivo do Nginx, comentar o
  `location /` do proxy e descomentar o bloco `ROLLBACK (SPA vanilla)` logo abaixo, `nginx -t`
  + `systemctl reload nginx`, e `systemctl stop opussb-web`. Backups do cutover (DB + tarball
  do código + config anterior do Nginx) em `/opt/opussb_backups/*-20260621-203913.*`.

---

## 3. Mapa de arquivos (`frontend/`)

```
app/
  layout.tsx                  raiz: html lang=pt-BR class="dark", Space Grotesk, AppProviders, favicon
  globals.css                 Tailwind v4 + tokens da marca (tema shadcn) + import de brand.css
  brand.css                   arte autoral (glows, hero, dropzone, howb, login, cards, editor, etc.)
  (auth)/login/page.tsx       login (split editorial)
  (auth)/senha/page.tsx       troca de senha (primeiro acesso)
  (app)/layout.tsx            RequireAuth + Topbar + MobileMenu + HashRedirect + atalhos
  (app)/page.tsx              Home (-> components/home/home-view)
  (app)/projetos/page.tsx     lista de projetos
  (app)/projetos/[id]/page.tsx detalhe do projeto
  (app)/favoritos/page.tsx
  (app)/config/page.tsx       Tabs: Padrões / Segurança / Conta
  editar/[clipId]/page.tsx    EDITOR full-screen (fora da casca; guarda de auth própria; fonte Inter)
components/
  ui/                         shadcn (button c/ variantes gold/brandGhost + size brand, dialog,
                              sheet, switch, select, slider, tabs, tooltip, sonner, input, label...)
  topbar, mobile-menu, brand-mark, arrow-burst, hash-redirect
  clip-card, clip-grid, clip-video, clip-player-dialog
  project-card (ProjectCard/FeaturedCard/onda generativa), stat-card, confirm-dialog, reprocess-dialog
  upload-console, job-progress
  home/ (hero, how-it-works, recent, home-view), shader-background
  editor/ (clip-editor, preview-stage, transcript-panel, editor-timeline)
lib/ (api.ts, types.ts, format.ts, utils.ts)
providers/ (app-providers, auth-provider, query-provider)
hooks/ (use-projects, use-favorites, use-prefs, use-sessions, use-job-status, use-shortcuts,
        use-filmstrip, use-waveform)
next.config.ts  components.json  tsconfig.json  package.json
```

---

## 4. Tema / marca (Tailwind v4 + shadcn)

- **Marca sagrada preservada:** os tokens exatos do design system v2 (dark `#0a0a0b`, dourado
  `#f5c518`, gradiente `linear-gradient(150deg,#ffd75e,#e0a800)`, Space Grotesk, raios,
  durações/easings, `--green`, surfaces, ink) foram traduzidos pros tokens semânticos do shadcn
  em `app/globals.css` (`:root, .dark`), e expostos como utilitários Tailwind (`bg-gold`,
  `text-ink-soft`, etc.) via `@theme`. App é dark-only (`<html class="dark">`).
- **`app/brand.css`** guarda a **arte autoral** (que não faz sentido em utility-soup): glows,
  hero + pilha de mini-clipes + equalizer + shader, dropzone, console de upload, progress shimmer,
  "Como funciona" animado, login editorial, cards de projeto/clipe, onda generativa, skeletons,
  e todo o layout do **editor**. As classes reusam os mesmos nomes de var do design antigo (via
  aliases `--bg/--surface/--ink/--container/--fs-*` no topo de `brand.css`).
- **Button:** variantes da marca adicionadas ao shadcn: `gold` (gradiente + glow + hover-lift),
  `brandGhost`; size `brand` (CTA encorpado). `destructive` reusado pro "danger".
- **shadcn/Radix** substitui o que era feito à mão: modal/dialog, drawer mobile (Sheet, com
  focus-trap de fábrica), switch, select, slider, tabs, tooltip, toasts (sonner).

---

## 5. Data layer / estado / providers

- **`lib/api.ts`** cliente fetch tipado de todos os endpoints. `credentials: same-origin`. Em 401
  lança `AuthError` e dispara `window` event `opussb:auth-expired`. `ApiError` carrega status/msg.
- **`lib/types.ts`** contrato (Me, Project, Clip, Stats, Session, Captions, Segment, Music,
  Preferences, UploadOptions). Mais recentes: `is_admin`/`status` em Me/LoginResponse + `AdminUser`/
  `SignupResponse` (§11), `Project.owner_email` (§13), `CaptionStyle` + `Captions.style`/`caption_y`
  (§14/§8/§16), `JobState` ganhou `downloading` (§14). **`lib/format.ts`** (fmtBytes/Score/Seconds,
  timeAgo, hashStr, nome/iniciais/saudação, `nameFromEmail`).
- **Providers** (`providers/`): `AuthProvider` (chama `me()` no boot; `login/logout/changePassword`;
  ouve o evento de 401 → desloga), `QueryProvider` (QueryClient; retry off em AuthError),
  `TooltipProvider`, `Toaster` (sonner, tema dark).
- **Hooks** (`hooks/`): `useProjects`/`useProject`, `useFavorites` + `useToggleFavorite` (otimista,
  invalida favoritos), `usePrefs` (via `/api/preferences`, merge com defaults), `useSessions`
  (+ revoke/revokeAll), `useJobStatus` (polling 5s via `refetchInterval` até done/error),
  `useShortcuts` (g h/p/f/c, n, ?), `useFilmstrip`/`useWaveform` (mídia do editor, best-effort).

---

## 6. Telas

- **(auth):** `/login` (split editorial com glows + arrow-burst), `/senha` (troca no primeiro
  acesso). Ambas redirecionam se já autenticado.
- **Casca (app):** `MiniNavbar` (pill flutuante de vidro, ver §11; **substituiu `Topbar`/`MobileMenu`**,
  que seguem no repo sem uso) + `RequireAuth` (loading/guest→login/must_change→senha) + `Preloader`
  pós-login (§11) + rodapé de autoria (§11). O `main` tem `pt-20` (navbar `position:fixed`); na Home
  o hero é puxado sob a navbar (§14).
- **Home (`/`):** hero + `ShaderBackground` (three lazy) + `UploadConsole` (dropzone, presets,
  validação de formato/tamanho, opções; aplica os padrões salvos) → `useJobStatus` polling →
  `JobProgress` (barra/status + resultados em `ClipGrid`) + `HowItWorks` (IntersectionObserver) +
  `Recent` (featured + grid dos recentes via `getStats`).
- **Projetos (`/projetos`):** grade + ordenação (Todos/Melhores notas/Mais recentes) + skeleton +
  empty. **Detalhe (`/projetos/[id]`):** cabeçalho com meta + ações (Baixar todos = §19 / Reprocessar
  degrada em toast / Excluir) + `ClipGrid`. **Favoritos.** **Config** (Tabs:
  Padrões de clipe / Segurança = trocar senha + sessões ativas / Conta = email + "Sua produção"
  com count-up).
- **Clipe:** `ClipCard` (vídeo com overlay "preparando", nota, duração, favoritar, abrir, baixar,
  copiar, **editar** → vai pro editor), `ClipGrid` (busca/filtro de nota/ordenação),
  `ClipPlayerDialog`. `ProjectCard`/`FeaturedCard` (thumb real quando há `thumbnail_url`, senão
  onda generativa).

---

## 7. Editor de clipe (estilo OpusClip) · `/editar/[clipId]`

Tela cheia, fora da casca (guarda de auth própria). Layout inspirado no editor do OpusClip, **só
com o que o nosso backend faz de verdade** (sem botões falsos):
- **Esquerda:** transcrição/legenda editável (cada linha = um segmento: tempo + texto, add/remover)
  + **música de fundo** (adicionar/remover/volume).
- **Centro:** preview 9:16 com a **legenda sobreposta AO VIVO** no estilo SB (fonte Inter, glow),
  aparecendo conforme o vídeo toca. Usa o vídeo **base sem legenda queimada** (rota nova, §8).
- **Embaixo:** timeline com transport (play/pause, início/fim, tempo), **zoom**, régua, trilha de
  vídeo (filmstrip), trilha de áudio (waveform), e a **trilha de legenda com blocos
  arrastáveis/redimensionáveis** + playhead com scrub.
- **Toggle Com legenda / Sem legenda** (esconde o overlay) e **Exportar** (baixa a versão queimada
  ou a **base limpa** quando "Sem legenda"). Esse foi o pedido do "No captions" do OpusClip.
- **Legenda arrastável (vertical, centralizada)** + botão **Resetar posição**. A posição persiste
  por clipe e a queima aplica via `\pos` (ver §8). O default do overlay foi calibrado pra bater com
  a posição real do estilo SB (`alignment 2` + `MARGIN_V 580`, ≈ 67% do topo).

**Melhorias de usabilidade (2026-06-21):**
- **Karaokê:** a linha sendo falada acende e faz auto-scroll no painel; o bloco correspondente
  acende na timeline (depende do fix do `currentTime`, abaixo).
- **Timing fácil por linha:** na linha selecionada, botões **Início/Fim** marcam o ponto no
  instante atual do player + setas de **nudge ±0,1s**. Tempos exibidos com décimos.
- **Atalhos:** **espaço** = play/pause, **← →** = seek (shift = 5s); ignora quando digitando ou com
  diálogo aberto. Dica visível na transport.
- **Timeline:** alças de redimensionar maiores (grip visível) e **snap** (imã ~8px) no playhead e
  nas bordas dos blocos vizinhos ao arrastar.
- **Fix (importante):** o efeito que anexa os listeners do `<video>` dependia só de `previewSrc` e
  rodava antes do vídeo montar (query carregando) → `currentTime`/`duration` ficavam em 0 e a
  legenda não acompanhava. Corrigido adicionando `editable` às deps + sync de metadados na corrida.

**Onda de usabilidade (2026-06-22, pedido do dono: "timeline difícil + tempo/ordem das legendas difíceis"):**
Decisão de design via pesquisa (OpusClip/CapCut/Veed/Kapwing/Descript) + júri: a lista de transcrição
vira o editor de tempo e estrutura de verdade; a timeline é a companheira visual. Sem mudança no backend
(a ordem do array é cosmética pro `build_ass_from_segments`, que emite um evento por segmento; o `save()`
manda `{start,end,text}[]` limpo). Build + `tsc` limpos, restart do `opussb-web` feito.
- **IDs de cliente estáveis** (`EditSeg = {_id} & Segment`, contador de módulo, **nunca persistido**, removido
  no `save()`): seleção/karaokê/split/merge ficam estáveis mesmo quando a lista se reordena. `selected`
  virou `selectedId: string`. A lista e os blocos leem de `ordered = [...segments].sort(byStart)`.
- **Ordem:** lista **sempre cronológica**; `addSeg` insere no playhead (e o render ordenado coloca no lugar);
  **Dividir** (`splitSeg`, tesoura: no playhead se dentro do trecho, ou no caret do texto via **Enter**, cortando
  na palavra mais próxima) e **Juntar** (`mergeSeg`, com a próxima via botão, ou **Backspace** no início junta
  com a anterior). Tudo gera novo array, nunca toca vídeo.
- **Tempo:** componente **`TimeField`** (input com draft local, parseT aceita `M:SS.d`/`SS.d`/segundos, vírgula
  ou ponto; commit no blur/Enter, ESC reverte, seta cima/baixo = ±0,1s, Shift = ±1s). `setSegTime` clampa pra
  não cruzar a outra borda. Início/Fim e os nudges ±0,1s continuam.
- **Timeline:** **playhead auto-follow** (centra `scrollLeft` só enquanto `playing`, com guarda de scroll manual
  ~1,5s); botão **Ajustar** (fit-to-clip) + zoom −/+ e fit-on-load; **clique vs arraste** separados por limiar de
  4px (`startDrag`, seleciona no `up` sem mover) então clicar não empurra o bloco; **largura mínima 26px** no bloco;
  aviso visual de **legenda curta** (`<0,7s`) no painel e na timeline. Breakpoint que esconde a transcrição baixou
  pra 640px (a lista é mais central agora).

**Limitações honestas (sem gambiarra):**
- **Waveform e filmstrip são best-effort.** O vídeo vem do S3 via URL presignada **sem CORS**,
  então decodificar áudio / capturar frames no browser é bloqueado → as trilhas ficam **neutras**
  (não inventamos dado falso). Pra deixar reais: habilitar **CORS no bucket** ou gerar
  sprite/waveform **no backend**.
- **Clipe antigo** (legenda já fixa no `_9x16.mp4`, sem base re-queimável) → "Edição indisponível".
  Estados separados no editor: carregando / erro (mostra a mensagem real) / clipe antigo / editor.
- Presets de estilo de legenda (Karaokê/Beasty/etc. do OpusClip) **fora de escopo** (decisão do dono:
  só o "remover legenda").

---

## 8. Toques no backend (ADITIVOS, já em produção, validados)

Backend quase não foi tocado. As únicas adições (todas seguras, `test_backend.py` passa, restart com
`active_jobs=0` + backup feitos):
- **`GET /api/clips/<id>/base`** (`app.py`): 302 pro `clip_NN_base.mp4` no S3 (clipe **sem** legenda
  queimada). Usado pelo overlay ao vivo do editor e pelo "Exportar sem legenda".
- **Posição vertical da legenda por clipe:** coluna **`caption_y`** em `clip_captions` (`db.py`,
  migração via `_ensure_column`; `get/set_clip_caption_y`). `GET/PUT /api/clips/<id>/captions`
  passam a ler/gravar `caption_y` (fração 0..1 ou null=padrão). A queima
  (`captions.build_ass_from_segments(segments, caption_y)`) aplica `\an5\pos` quando há posição;
  null mantém o estilo SB padrão.

Pendente no backend (não é deste front): `reprocess`, `download_all`, e (opcional) CORS no S3 ou
geração de waveform/filmstrip.

---

## 9. Pendências / TODO

- ~~**Fase 3: cutover**~~ **FEITO (2026-06-21)** — systemd `opussb-web` + Nginx → Next, com
  rollback pro `static/` documentado (§2).
- ~~**Aviso de alterações não salvas**~~ **FEITO (2026-06-21)** — `beforeunload` (reload/fechar
  aba/sair pra fora) + guarda do botão Voltar do editor via `onNavigate` do Link + `ConfirmDialog`.
  **Limite honesto:** o botão Voltar/Avançar do *navegador* (popstate) não é interceptado — fazer
  isso exigiria manipular o histórico (sentinela), que causa "voltar duplo" depois de salvar
  (gambiarra que o dono proíbe). Cobre os caminhos principais; o popstate fica como melhoria.
- ~~Atualizar `BACKEND_INTEGRATION.md` com `caption_y` e a rota `/base`~~ **FEITO (2026-06-21)**
  (§3.1 reescrita, com `/base`, `caption_y` em GET/PUT e as rotas de música, tudo verificado no `app.py`).
- ~~**Editor: melhorias de usabilidade**~~ **FEITO (2026-06-21)** — karaokê, timing por linha,
  atalhos, snap na timeline (§7).
- ~~**Clipe "termina em cima da fala"**~~ **resolvido pra clipes FUTUROS (2026-06-21)** via
  **auto-snap no corte** (backend `captions.snap_clip_bounds` chamado no `processor.py` antes do
  corte: estende o fim até o fim da fala em curso, teto 2,5s; puxa o início, teto 1s). **Não** mexe
  em clipes já gerados (vídeo original já apagado). **Opção C (estender manual no editor, estilo
  OpusClip)** ficou DEFERIDA: exigiria reter o original no S3 + persistir in/out + rota de re-corte
  + UI de alças (custo de storage/pipeline; decisão de produto). **Atualização (2026-06-23): a Opção
  C foi FEITA via handles** (sobra de 10s/lado na base, sem reter o original), ver §15.
- **Waveform/filmstrip reais** no editor (CORS no bucket S3 **ou** sprite/waveform server-side). PENDENTE.
- Remover `static/` (SPA vanilla) só **depois** de alguns dias de cutover validado em produção. PENDENTE.
- Se virar produto: mover o front pro Vercel/Cloudflare (deixa a VPS só com a API Flask).

---

## 10. Verificação (receita)

- **Dev:** `npm run dev` (127.0.0.1:3000) com o Flask vivo; exercitar login, upload + polling,
  Projetos/Detalhe, **editor** (overlay ao vivo, arrastar legenda, Salvar, Exportar com/sem
  legenda), Favoritos, Config (senha, sessões, stats).
- **Build/type-check:** `npx tsc --noEmit` e `npm run build` passam limpos.
- **Backend:** após qualquer `.py`, `systemctl restart opussb` (checar `active_jobs=0` + backup);
  `./venv/bin/python test_backend.py` (puro, DB temporário) deve passar.

---

## 11. Rebrand SB Clips, redesign do login, navbar nova, painel admin, upload com progresso (2026-06-22)

> Tudo deployado em produção (https://sbclips.space). Build via `npm run build` + `systemctl
> restart opussb-web`. Backend correspondente: ver §14 do `BACKEND_CONTEXT.md`.

### Rebrand "OpusSB" para "SB Clips"
Todo texto VISÍVEL virou "SB Clips" (navbar, login, troca de senha, preloader, título da aba em
`app/layout.tsx`, copy do hero, marca-d'água do card). Comentários de código e o nome interno
(serviços systemd, repo, backend) seguem "OpusSB". O ícone da marca (tesoura) não mudou.

### Auth no front (espelha o backend novo)
- `lib/types.ts`: `Me`/`LoginResponse` ganharam `is_admin` e `status`; novos `AdminUser`,
  `SignupResponse`.
- `lib/api.ts`: `authProviders`, `signup`, `uploadWithProgress` (XHR com progresso real), e admin
  (`adminUsers/adminApprove/adminReject/adminSetActive/adminSetAdmin`).
- `providers/auth-provider`: `logout` limpa o flag do preloader (`sessionStorage`).

### Login (`app/(auth)/login/page.tsx`)
- Botão **"Entrar com Zoho"** = `components/ui/shiny-button.tsx` (CTA dourado, borda em gradiente
  cônico animado). Só aparece se `authProviders().zoho` for true (oculto até configurar as creds).
  Logo do Zoho inline (simple-icons, CC0).
- Botão **"Entrar"** (senha) = `components/ui/motion-button.tsx` (círculo que expande no hover;
  neutro em repouso, dourado no hover, pra não competir com o Zoho).
- **Cadastro manual** no mesmo card (toggle): chama `signup`, mostra "aguardando aprovação". Lê
  `?error=` do callback do Zoho via `window.location` (NÃO usa `useSearchParams`, que no Next 16
  exige `<Suspense>` no prerender e quebraria o build).

### Preloader pós-login (`components/ui/preloader.tsx`, framer-motion)
Animação de entrada montada no `(app)/layout.tsx`: saudações multilíngues + beat final "SB Clips"
em dourado, fundo preto neutro, sobe revelando o app. Toca UMA vez por login (flag em
`sessionStorage`, limpo no logout). Dep nova `framer-motion` (o `ease` cubic-bezier foi tipado
como tupla pra passar no TS do Next 16).

### Navbar `components/ui/mini-navbar.tsx` substitui a Topbar
Pill flutuante de vidro escuro no topo-centro: rotas reais com hover de slide vertical + item ativo
dourado, marca SB, chip do usuário + botão Sair, e menu mobile próprio (hambúrguer). O `(app)/layout`
usa `MiniNavbar` no lugar de `Topbar` e dá `pt-20` no `main` (a navbar flutua, `position:fixed`).
`Topbar`/`MobileMenu` antigos ficaram no repo SEM uso (rollback).

### Painel admin `app/(app)/painel/page.tsx` (só admin)
Abas Pendentes (Aprovar/Recusar) e Usuários (Ativar/Desativar, promover admin), via TanStack Query.
Gate de admin no cliente (redireciona não-admin pra `/`) + o backend valida em todo `/api/admin/*`.
Link "Painel" na navbar só aparece pra admin.

### Upload com progresso (`components/upload-console.tsx`)
`api.uploadWithProgress` (XHR) dá % real + ETA (calculado pela velocidade de envio). Durante o
upload o rodapé do console troca os controles pela barra-cometa dourada (`.upload-*` em brand.css)
com "X% / tamanho / ~tempo restante", e ao chegar em 100% mostra "preparando o processamento". O
botão "Gerar clipes" ganhou faíscas no hover (`.btn-stars`).

### Rodapé de autoria (`(app)/layout.tsx`)
Assinatura discreta "Cortado com 💛 por Klenio Braz" (link pro Instagram), no fim de todas as
páginas do app (`.app-footer`).

### Componentes e CSS novos
Novos em `components/ui/`: `shiny-button`, `motion-button`, `preloader`, `mini-navbar`. Todo o CSS
de apoio vive em `app/brand.css` (`.shiny-cta`, `.btn-stars`, `.upload-*`, `.admin-*`,
`.auth-divider/link/success`, `.app-footer`). Dep nova: `framer-motion`. Build (`next build`) passa
limpo; deploy via `systemctl restart opussb-web`.

### Observação de processo (regra do dono)
Este foi o primeiro commit a incluir o diretório `frontend/` inteiro (até então estava untracked).
`frontend/.gitignore` (do create-next-app) cobre `node_modules`/`.next`/`.env*`. Push na `master`
só com autorização (regra 9).

---

## 12. Player custom do clipe (2026-06-22, pedido do dono: "não saber qual botão clicar")

`components/clip-video.tsx` deixou de usar `<video controls>` (o chrome nativo trazia play/volume/
tela-cheia/PiP/menu de 3 pontos, que competiam e se sobrepunham aos botões do app: ❤️, nota e o
antigo `clip-max`). Agora tem controles PRÓPRIOS, claros:
- **Play/pause central grande** (só aparece pausado + pronto, classe `.is-paused.is-ready`); clicar no
  vídeo também alterna.
- **Barra própria** (`.clip-controls`, visível no hover e fixa quando pausado): play/pause, tempo
  `m:ss / m:ss`, **scrub** (`<input type=range>` `.clip-seek` com preenchimento dourado via gradiente
  inline calculado de `cur/dur`), mudo, e **UM** botão de expandir.
- **Expandir por contexto** via prop nova `onExpand?`: no card (`clip-card.tsx`) passa
  `onExpand={() => setOpen(true)}` (abre o `ClipPlayerDialog`); no diálogo, sem `onExpand`, faz
  **tela cheia** no próprio wrapper (`requestFullscreen`/`exitFullscreen`, ícone troca Expand/Shrink
  via listener de `fullscreenchange`).
- `clip-card.tsx`: removidos o botão `clip-max` duplicado e a badge `.clip-dur` (a barra já mostra o
  tempo); mantidos ❤️ (`.clip-fav`, topo-esq) e a nota (topo-dir). Imports `Maximize2`/`fmtSeconds`
  retirados. O editor (`preview-stage.tsx`) não usa o `ClipVideo`, então ficou intacto.
- CSS novo em `app/brand.css` (`.clip-bigplay`, `.clip-controls`, `.clip-ctrl`, `.clip-time`,
  `.clip-seek` + thumbs webkit/moz), tudo reusando `--gold`. Sem mudança no backend; `tsc` + `next
  build` limpos; deploy via `systemctl restart opussb-web` (build + restart juntos pra não deixar o
  `.next` inconsistente sob o `next start` no ar).

---

## 13. Projetos compartilhados (uso interno) (2026-06-22)

> Decisão do dono: a biblioteca de Projetos passa a ser COMPARTILHADA entre o time (todos veem
> e editam tudo); só o dono (quem subiu) e os admins podem excluir. Escopo: muda SÓ a tela de
> Projetos. Home/Recentes e o contador "Sua produção" continuam PESSOAIS. Backend correspondente:
> ver §15 do `BACKEND_CONTEXT.md`. Deployado em produção (build + `systemctl restart opussb-web`).

- **`lib/types.ts`**: `Project` ganhou `owner_email?: string | null` (quem subiu; null = legado).
- **`lib/format.ts`**: novo `nameFromEmail` ("klenio.braz" vira "Klenio Braz"; vazio se sem email).
- **`components/project-card.tsx`**: a meta do card passou a mostrar "· por \<nome\>" quando há
  `owner_email` (componente puro, sem hook).
- **`app/(app)/projetos/[id]/page.tsx`**: usa `useAuth`; a meta mostra "· enviado por \<nome\>";
  `canDelete = user && (user.is_admin || user.email === p.owner_email)` e o botão **Excluir** só
  renderiza quando `canDelete`. O backend reforça (403), então esconder o botão é só cosmético.
  **Reprocessar** segue visível pra todos (no-op por ora); **Baixar todos** agora funciona (§19).
- Não mexe em favoritos/preferências (seguem individuais). `tsc --noEmit` e `next build` limpos.

---

## 14. Personalização de legenda + importar do YouTube + console estilo OpusClip (2026-06-22)

Três frentes, deployadas juntas (backend correspondente: §16 e §17 do `BACKEND_CONTEXT.md`).

### Personalização de fonte da legenda (editor)
Painel **"Estilo da legenda"** em `editor/clip-editor.tsx` (Tamanho, Espaçamento, Sombra, Cor +
"Resetar estilo"), refletido AO VIVO no overlay (`editor/preview-stage.tsx`, mapeado pra mesma
referência 1080x1920 da queima). `lib/types.ts` ganhou `CaptionStyle`; `api.putCaptions` aceita o
`style`. CSS `.editor-style*` em `brand.css`.

### Importar do YouTube (`upload-console.tsx`)
> **STATUS (2026-06-22): DESATIVADO na UI.** O segmento "Link do YouTube" aparece com selo
> **"Em breve"** e desabilitado (cursor not-allowed), via `const YOUTUBE_ENABLED = false` no topo
> de `upload-console.tsx`, porque o bloqueio anti-robô do IP de datacenter deixa o download instável
> (ver BACKEND §17). **Reativar = flip pra `true` + rebuild**; todo o código abaixo e o backend
> continuam intactos. CSS do estado: `.src-seg-btn:disabled` + `.src-seg-soon` em `brand.css`.

Seletor de fonte **"Enviar arquivo / Link do YouTube"** (segmented control com thumb dourado
deslizante, `.src-seg*`); modo YouTube troca a dropzone por um campo de link. `api.uploadYoutube`
(`lib/api.ts`); estado novo **`downloading`** em `JobState` (types) e no `STATUS_LABELS`
(`job-progress.tsx`, "Baixando do YouTube..."). Glifo do YouTube inline (lucide desta versão, 1.21,
não exporta `Youtube`).

### Console estilo OpusClip + hero (seguindo mockup do dono)
`upload-console.tsx` redesenhado: cabeçalho "Gerar novos clipes" + subtítulo com o seletor de fonte
à direita; eyebrow "Predefinição"; presets em duas tonalidades (nome + duração); Formato com
descritor ("9:16 · Vertical"); alinhamento uniforme; botão **dourado "✦ Gerar Clipes"** (`Button`
variant gold + `Sparkles`). O hero (`home/hero.tsx`) tem eyebrow com traço dourado (`.hero-eyebrow`)
e título em duas linhas (saudação + nome em dourado, `.hero-greeting`). O `.hero` agora preenche a
viewport (`min-height: 100vh`, puxado sob a navbar com `margin-top: -5rem`) pra arte ir até o topo
(sem barra preta) e a parte branca ("Como funciona") ficar abaixo da dobra.

> Nota: o botão animado escuro (sparkle/shimmer, `.gen-btn` em `brand.css`) chegou a ser feito mas o
> mockup final pediu o botão dourado; o `.gen-btn` segue no CSS, sem uso (rollback fácil). Subtítulo
> do hero mantido SEM travessão (regra do dono), apesar do mockup ter um.

---

## 15. Corte ajustável na timeline (handles) (2026-06-23)

> Pedido do dono: a IA às vezes erra o corte; o editor passa a deixar ajustar o in/out arrastando
> alças na timeline. Backend correspondente: §18 do `BACKEND_CONTEXT.md`. Sem reter o vídeo original:
> cada clipe sai com ~10s de sobra de cada lado (o `base.mp4` vira a "janela") e o corte oficial fica
> em `[in,out]` dentro dela. Deployado (build + `systemctl restart opussb-web`).

- **`lib/types.ts`**: `Captions` ganhou `bounds?: {window_dur, in, out} | null` (+ interface
  `CaptionBounds`). `null` = clipe sem handles (legado).
- **`lib/api.ts`**: `putCaptions` aceita um 5º arg `cut: {in,out} | null` (manda `in`/`out` no PUT
  quando há handles; omitido = não mexe no corte).
- **`components/editor/editor-timeline.tsx`**: máscara escurecida nas sobras (`.editor-trim-mask`
  is-head/is-tail) + duas **alças de trim** verde-água (`.editor-trim-handle`) arrastáveis + rótulo
  da duração do corte (`.editor-trim-dur`). `startTrim` reusa o padrão de `startDrag` (snap no
  playhead/bordas/blocos, clamp pra não cruzar a outra alça, mínimo `MIN_CUT=1s`). Props novas:
  `hasHandles`, `cutIn`, `cutOut`, `onChangeCut`.
- **`components/editor/clip-editor.tsx`**: estado `hasHandles`/`cutIn`/`cutOut` (de `data.bounds`);
  o **play vira prévia do corte** (começa em `in`, pausa em `out`); o scrub segue livre pra inspecionar
  as sobras; seek inicial pro `in` ao abrir; `save()` manda o corte via `putCaptions`; **"Exportar sem
  legenda" passou a usar `/video?nocap=1`** (o corte cru) em vez de `/base` (que agora é a janela
  inteira com sobras, usada só pelo preview ao vivo).
- **`preview-stage.tsx`**: intacto (a legenda ao vivo usa `currentTime` e os segmentos no mesmo eixo
  da janela).
- **`app/brand.css`**: `.editor-trim-mask`, `.editor-trim-handle`, `.editor-trim-dur`.
- Os segmentos da legenda agora cobrem a janela inteira (incluindo as sobras): aparecem na lista e na
  timeline, então a legenda fora do corte pode ser vista (e entra se o corte for estendido).
- `tsc --noEmit` e `next build` limpos. **Clipe ANTIGO** (sem `bounds`): editor sem alças (degrada,
  como antes). As alças só aparecem em clipes gerados a partir do deploy do backend de §18.

## 16. Lacuna na Home durante o processamento (2026-06-23)

Bug visual: com um job processando, a Home mostrava uma lacuna preta gigante entre o hero e o card
de progresso (e a faixa branca do "Como funciona" aparecia lá embaixo). Causa: o `.hero` tem
`min-height: 100vh` (§14), e em processamento o `home-view` escondia o `UploadConsole` e renderizava
o `JobProgress` DEPOIS do hero, então o hero ficava um bloco vazio de tela cheia empurrando o card
pra baixo da dobra.

- **`components/home/home-view.tsx`**: com job ativo, o `JobProgress` agora entra DENTRO do hero (no
  lugar onde ficava o `UploadConsole`), e o `<section>` ganha a classe `hero--busy`. O `JobProgress`
  é renderizado direto (sem `.upload-wrap`), porque o `.progress-card` já tem container centralizado
  próprio (evita dupla restrição de largura).
- **`app/brand.css`**: `.hero--busy { min-height: 0; padding-bottom: 44px; }` derruba o 100vh forçado
  enquanto há job, então o card fica logo abaixo da saudação. Backend correspondente: BACKEND §19
  (semáforo de queima, reframe mais rápido e travado).

> **STATUS (2026-06-23): FEITO e deployado.** O front foi rebuildado (`npm run build`) e o `opussb-web`
> reiniciado com `active_jobs=0`, então a lacuna não aparece mais em produção. Deploy junto do lazy-load (§18).

## 17. Pendências do front (handoff 2026-06-23)

1. ~~**Deployar a lacuna da Home (§16)**~~ **FEITO (2026-06-23)** — rebuild + restart do `opussb-web`
   com `active_jobs=0`. Ver §16 (status atualizado).
2. ~~**Lazy-load dos clipes**~~ **FEITO (2026-06-23)** — ver §18.

## 18. Lazy-load dos clipes na grade (anti-estampida) (2026-06-23)

Resolve a pendência da §17.2. **Antes:** o `ClipGrid` montava um `ClipVideo` por clipe e cada `<video>`
nascia com `src` + `preload="metadata"`, então pedia `/api/clips/<id>/video` **ao renderizar** -> quando
um job terminava e a grade aparecia, disparava **N queimas de uma vez** (a estampida que tomava a CPU e
travava o site). O backend já tem rede (semáforo de queima BACKEND §19.1 + cap de CPU §21.2), mas o ideal
é não disparar tudo de uma vez.

- **`components/clip-video.tsx`**: o `src` só é anexado ao `<video>` na **primeira intenção de tocar**
  (clique no play), não na renderização. Estado novo `activated` (nasce `true` quando `autoPlay`, p.ex. no
  diálogo); enquanto idle o `<video>` fica `src={undefined}` + `preload="none"` (zero request). No primeiro
  play, `activated` vira true (anexa o src -> só AGORA o backend queima) e um efeito em `[activated]` chama
  `.play()` quando o elemento remonta com o src (`wantPlayRef`). Resultado: **uma queima por vez**, conforme
  o usuário assiste, em vez de N de cara.
- **`app/brand.css`**: classe `.is-idle` no wrapper enquanto não-ativado. Sem ela a CSS mostraria, na grade,
  N spinners "preparando legenda…" falsos e eternos (o `.clip-prep` só some com `.is-ready`) e nenhum botão
  de play (o `.clip-bigplay` só aparece com `.is-paused.is-ready`). As regras novas
  (`.clip-video.is-idle .clip-bigplay { display: grid }` + `.clip-video.is-idle > .clip-prep { opacity: 0 }`)
  mostram o play idle (que dispara a queima) e escondem o spinner até o load real começar.
- O diálogo (`ClipPlayerDialog`, `autoPlay`) e o editor (não usa `ClipVideo`) seguem iguais. `tsc --noEmit`
  e `next build` limpos; deploy via `npm run build` + `systemctl restart opussb-web` (com `active_jobs=0`).

## 19. "Baixar todos os clipes" (download_all) (2026-06-23)

Liga o botão **"Baixar todos"** do detalhe do projeto (antes caía em toast). Backend correspondente:
BACKEND §23 (sem zip no servidor; o front baixa um a um, as queimas seguem serializadas pelo semáforo).

- **`app/(app)/projetos/[id]/page.tsx`**: `downloadAll` agora bate em `GET /api/projects/<id>/download_all`,
  recebe `{clips: [{url, filename}]}` e dispara **um download por clipe, escalonado (700ms)**, criando um
  `<a download>` por item e clicando programaticamente. Cada `url` aponta pra `/api/clips/<id>/video?dl=1`,
  que o backend entrega já como anexo (`Content-Disposition: attachment`), então o navegador BAIXA em vez de
  abrir o vídeo numa aba. Toast de progresso ("Baixando N clipes..."); vazio e erro tratados.
- O navegador pede "permitir múltiplos downloads" na 1a vez (o usuário permite); o escalonamento evita a
  rajada. Mudança só de DOM/React (sem API nova do Next, respeitando o `AGENTS.md`). `tsc --noEmit` +
  `next build` limpos; deploy via `npm run build` + `systemctl restart opussb-web`.

## 20. Plano: overhaul de usabilidade do editor (PLANEJAMENTO, nada implementado) (2026-06-24)

> Pedido do dono: a usabilidade do editor (`/editar/[clipId]`) ainda está ruim / "não do jeito que eu
> queria". Por ora é **SÓ PLANO** (nenhuma linha de código). **Regra nova do dono: tudo isto (e a Fase 1 do
> crop, BACKEND §24) será feito em SERVIDOR DE TESTES/staging primeiro, NÃO direto em produção.** Plano em
> fases, de ponta a ponta (backend ao front).

**Princípios (engenharia senior):** (1) não redesenhar no escuro: AUDITAR e fixar as dores reais antes;
(2) separar ENABLERS (backend/infra que destrava) de POLIMENTO; (3) entregar em fases pequenas,
independentes e REVERSÍVEIS, validando cada uma com o dono; (4) reusar a arquitetura atual
(transcrição-como-editor + timeline + queima sob demanda), sem rewrite; (5) staging primeiro, prod intocada.

**Fase 0 — Descoberta e fundação (sem mudança visível):**
- Auditoria heurística do editor atual: mapear cada interação (editar transcrição, tempo, dividir/juntar,
  arrastar legenda, alças de trim, estilo, música, exportar) e pontuar a fricção.
- **Sentar com o dono / observar uma edição real** pra capturar as dores ESPECÍFICAS (o "não do jeito que
  eu queria" é o input que falta hoje; sem isso, o resto vira chute).
- Definir critérios de sucesso + métrica (tempo pra editar um clipe, nº de cliques errados, satisfação).
- Montar o **ambiente de TESTES/staging**: instância separada (ex.: unit `opussb-web-staging` + branch de
  staging + prefixo S3 de teste + DB de teste), pra Fase 1 e o editor nunca tocarem prod. Documentar.
- Saída: lista priorizada de dores + critérios de sucesso + staging pronto.

**Fase 1 — Quick wins (front, alto impacto, baixo risco, NÃO precisam de backend):**
- Layout/espaçamento, affordances mais claras, descoberta dos atalhos, guarda do botão Voltar do navegador
  (popstate, pendência da §9), estados de loading/erro/vazio, responsividade, e os ajustes da "timeline
  difícil". Cada um deployável e reversível sozinho.

**Fase 2 — Enablers de backend (destravar o que hoje é "neutro"/falso):**
- **Waveform + filmstrip REAIS** na timeline (hoje neutros por falta de CORS no S3, §7). Decisão senior:
  (a) habilitar CORS no bucket (barato, mas expõe o bucket a fetch do browser -> revisão de segurança), ou
  (b) gerar waveform (JSON) + filmstrip (sprite) **no backend, no processamento** (mais storage/compute,
  porém sem expor o bucket e confiável). Recomendo (b). Destrava uma timeline de verdade (pistas de áudio +
  scrub visual), que é uma das maiores alavancas de usabilidade.

**Fase 3 — Editor de crop por KEYFRAME (a "Fase 1" do reframe, BACKEND §24):**
- A correção manual de enquadramento É uma feature de usabilidade do editor. Integra aqui: reter o 16:9 +
  caixa de crop arrastável + trilha de keyframes na timeline, aplicada por re-render via `reframe_core`.
  Depende do OK de storage do dono. Reusa a timeline e o padrão de overlay/arraste que já existem.

**Fase 4 — Redesign do fluxo do editor (informado pela Fase 0, NÃO no escuro):**
- Com a auditoria na mão, um redesign coeso do layout/fluxo se necessário: melhor arranjo dos painéis, troca
  de MODO clara (legenda / crop / trim), timeline unificada, hierarquia visual.

**Fase 5 — Validação e hardening:**
- Re-teste de usabilidade com o dono contra os critérios da Fase 0; performance; casos de borda (clipe
  antigo sem base re-queimável, clipe sem fala); acessibilidade básica.

**Transversal:** staging primeiro em TUDO; cada fase shippável e reversível; sem travessão; deploys de
backend com backup + `active_jobs=0`.

> **STATUS: PLANO. Nada implementado.** Próximo passo concreto quando o dono retomar: **Fase 0** (fixar as
> dores específicas + montar o staging). As fases 1-5 dependem do output da Fase 0 (a priorização pode mudar).
>
> **ATUALIZAÇÃO 2026-06-25:** a Fase 0 ACONTECEU (auditoria + dores fixadas com o dono + staging montado como
> git worktree `/opt/opussb-staging`). Vários itens da Fase 1/2 já foram entregues fora do plano original (§21:
> painel para de seguir o vídeo, waveform real). A direção do editor mudou pra OpusClip texto-corrido (§22).
> Onde §20 divergir de §21-23, **valem as §21-23** (mais recentes).

---

## 21. Onda de ajustes 2026-06-25 (deployada em produção)

Tudo via `npm run build` + restart do `opussb-web` (e restart `opussb` pros backends), `active_jobs=0`,
com backup. Backend correspondente: BACKEND §26-28. Commits LOCAIS no `master`, **NÃO pushados** (BACKEND §30).

- **Painel da transcrição para de seguir o vídeo ao editar** (`components/editor/transcript-panel.tsx`): o
  auto-scroll do karaokê (`scrollIntoView`) agora é guardado quando o foco está num campo
  (textarea/input/contentEditable), então não "puxa" a tela no meio da correção. Resolve o "saco de corrigir
  legenda lá" / "painel fica acompanhando o vídeo". Commit `9bae09e`.
- **Diálogo do clipe maior + fim de 2 vídeos juntos** (`clip-player-dialog.tsx`, `clip-video.tsx`,
  `clip-card.tsx`): `ClipVideo` ganhou prop **`paused`**; o card passa `paused={open}` pra pausar quando o
  diálogo abre (antes tocavam os dois). O diálogo dimensiona o vídeo pela **altura** (`h-[68vh] max-h-[620px]
  w-auto`, layout centralizado, modal 880 -> 560). Commit `891b7b3`.
- **Projetos: poster por clipe + "Ver mais" + lazy** (`clip-video.tsx`, `clip-card.tsx`, `clip-grid.tsx`,
  `project-card.tsx`, `brand.css`): `ClipVideo` ganhou prop **`poster`** (1 frame real enquanto idle, via rota
  `/api/clips/<id>/thumb`, BACKEND §27) em vez do card preto. `ClipGrid` ganhou **"Ver mais"** (`PAGE_SIZE=9`,
  client-side, reseta na busca/filtro) pra não renderizar/baixar poster de todos de uma vez. `loading="lazy"`
  nas thumbs do projeto e nos posters. `.clip-poster`/`.clip-more` em brand.css. Commit `cda4dcc`.
- **Editor: camada de ÁUDIO real** (`hooks/use-waveform.ts`, `editor/clip-editor.tsx`): `use-waveform` passou
  a **buscar a rota `/api/clips/<id>/waveform`** (MESMA ORIGEM; o WebAudio do navegador era bloqueado por CORS
  do S3) e o `clip-editor` chama `useWaveform(editable ? clipId : null)` (gera só ao abrir aquele clipe). A
  faixa de áudio deixou de ser neutra. Backend §28. Commit `b1e7f07`. (O **filmstrip de VÍDEO segue neutro**;
  mesma ideia sob demanda fica pra depois, decisão do dono.)

## 22. Editor estilo OpusClip: DECISÃO + plano (2026-06-25) [refina §20]

Pedido do dono (mostrou prints do OpusClip): a transcrição vira **texto corrido** (não a lista de
linhas-com-relógio de hoje), com **palavra acesa ao vivo** (karaokê por palavra) e **pausas inline** (pílulas
tipo `0.22s`). Decisão fechada nesta sessão (pesquisa + painel multi-lente + probe real do Whisper):
- **RE-SKIN, não rewrite:** troca o `transcript-panel.tsx` por um bloco de texto corrido onde palavras são
  spans, mantendo o MESMO modelo `{start,end,text}` por baixo (o `save()` re-serializa pro schema; o backend
  `_validate_segments` descarta chave que não seja start/end/text).
- **Tempo de PALAVRA REAL** (`clip_words`, BACKEND §26), nunca interpolação (enganaria justo no ponto que o
  dono mostrou).
- **"Remover pausa / jump-cut" = v2** (custo: corte não-linear + re-render, ideal na GPU). FORA do v1.
- **Em STAGING** (worktree `/opt/opussb-staging`). A timeline (faixas) continua na visão nova, então §21
  (waveform) e a tela final (§23) não são retrabalho.
- Aberto (gate): karaokê palavra-a-palavra no EXPORT (libass `\k`) muda `captions.py`; decidir se entra.

## 23. Tela final (CLUBCAST) no editor — FEITO no código, deploy pendente de OK (2026-06-25)

4a camada do editor (vídeo / áudio / legenda / **tela final**): um bloco de ~5s no fim. **Decisão do dono
(esta sessão): escopo POR PROJETO** (a Clubcast é do cliente Carbone; não existe campo de cliente, então
ligar global estamparia clipe de qualquer cliente). Então: a tela final é o **padrão do projeto** (default
nenhuma), os clipes **herdam**, e é **removível/forçável por clipe** no editor. Entra em **todo entregável**
(player, baixar, exportar com E sem legenda). Mecânica de render + backend: **BACKEND §29**.

**FEITO (front, `tsc --noEmit` limpo):**
- **`lib/types.ts`**: `EndScreenOption`; `Captions` ganhou `end_screen` (override do clipe: null/"none"/id),
  `end_screen_effective` (o que será anexado), `project_end_screen` (default do projeto) e `end_screens`
  (biblioteca); `Project` ganhou `end_screen`.
- **`lib/api.ts`**: `getEndScreens`, `putClipEndScreen` (rota dedicada, não re-queima a legenda),
  `putProjectEndScreen`.
- **Editor (`components/editor/clip-editor.tsx`)**: componente `EndScreenControl` (no painel, ao lado de
  Música/Estilo): botão **Adicionar/Remover** + nota do estado (herda do projeto / removida neste clipe /
  definida neste clipe). Ação **imediata** (`putClipEndScreen`, fora do Salvar geral; re-renderiza no
  próximo play). Passa o rótulo efetivo pra timeline.
- **Timeline (`components/editor/editor-timeline.tsx`)**: 4a trilha `editor-track--end` (só visual,
  `pointer-events:none`, não mexe no scrub/trim): um bloco verde-água ancorado no fim do corte indicando
  a tela final, ou "sem tela final".
- **Projeto (`app/(app)/projetos/[id]/page.tsx`)**: seletor **"Tela final" (Nenhuma / Clubcast)** que grava
  o padrão do projeto (`putProjectEndScreen`) e invalida `["project",id]` + `["captions"]` (editores abertos
  releem a herança). Só aparece se a biblioteca (`getEndScreens`) tiver opções.
- **`app/brand.css`**: `.editor-track--end`/`.editor-end-block`/`.editor-end-empty`/`.editor-endscreen-note`
  e `.project-endscreen*`.

**STATUS: EM PRODUÇÃO + pushado** (`02821f9`). Depois: ligar a tela final nos projetos da Carbone pelo seletor.

## 24. Camada de vídeo (filmstrip) + biblioteca de telas finais + encurtar (2026-06-25)

Três pedidos do dono no editor (backend: BACKEND §31).

### Camada de VÍDEO (filmstrip) — DEPLOYADA (commit `0e72eb8`, pushado)
A trilha de vídeo do editor ficava vazia (canvas tainted pelo CORS do S3). `hooks/use-filmstrip.ts`
passou a buscar a rota server-side `/api/clips/<id>/filmstrip` (sprite) e retorna a URL; a
`editor-timeline.tsx` desenha a trilha `editor-track--film` esticando o sprite (`.editor-film-strip`,
`background-size:100% 100%`). `clip-editor` chama `useFilmstrip(editable ? clipId : null)`. **No ar.**

### Biblioteca de telas finais + encurtar — EM PRODUÇÃO (commit `09c5d6e`, pushado)
- **`lib/types.ts`**: `EndScreenOption.duration`; `Captions.end_screen_dur`.
- **`lib/api.ts`**: `uploadEndScreen(file,label)`, `deleteEndScreen(id)`; `putClipEndScreen(id, {end_screen?, dur?})`
  (assinatura virou patch).
- **Projeto (`projetos/[id]/page.tsx`)**: ao lado do seletor "Tela final", botão **+ Enviar** (upload .mp4
  até 10s, já aplica ao projeto) e **Excluir** (tira da biblioteca). `.project-es-btn`.
- **Editor**: o `EndScreenControl` (liga/desliga) segue; a **4a trilha vira arrastável** — alça direita
  (`.editor-end-handle`) encurta a tela final (bloco com largura = duração; persiste no soltar via
  `putClipEndScreen({dur})`, sem re-queimar). `clip-editor` mantém `endDur` + passa
  `endScreenDur`/`endScreenAssetDur`/`onChangeEndDur`/`onCommitEndDur` pra timeline.
- `tsc --noEmit` limpo. **EM PRODUÇÃO** (`next build` + restart feitos; commit `09c5d6e` pushado).

## 25. Painel de saldos (creditos) no admin (2026-06-25)

Aba "Saldos" no /painel (so admin; backend BACKEND §32). `lib/types.ts` ganhou `AdminCredits`;
`lib/api.ts` ganhou `adminCredits(refresh?)`. `app/(app)/painel/page.tsx`: componente `CreditsPanel`
+ nova `TabsTrigger`/`TabsContent` "credits". Card da RunPod (saldo USD + gasto/h, fica vermelho se
< US$5) e card da Claude (link pro Console enquanto nao houver Admin key) + botao "Atualizar"
(`refresh=1`). CSS `.credits-*` em `brand.css`. `tsc` limpo. **EM PRODUCAO** (deploy + commit nesta leva).

## 26. Editor de crop por KEYFRAME + multi-apresentador: DECISOES (2026-06-26)

Proxima grande frente do editor (estilo OpusClip). **So decisoes; nada implementado; em STAGING.**
Backend + arquitetura completa: **BACKEND §33** (e estado/git em BACKEND §34). Resumo do lado front:
- O editor passa a mostrar a **fonte 16:9** do clipe (rota nova tipo `/src169`) com uma **caixa 9:16
  arrastavel** por cima (so HORIZONTAL/pan, sem zoom) -> igual o print do OpusClip que o dono mandou.
- **Preview instantaneo via CSS** (a caixa e so um recorte visual; nao re-renderiza ao arrastar). O
  render final (9:16) sai do servidor por re-render do reframe_core no "Aplicar".
- **Trilha de keyframes** na timeline (4a/5a camada): o auto vira a trajetoria base e SEMEIA keyframes;
  o usuario move/adiciona/remove pra corrigir so onde errou. **Toggle Auto/Manual por clipe**.
- **Multi-apresentador** (split / 3 / 4, o "Ativar layout" do print): no escopo, mas FASE POSTERIOR.
- Reusa a timeline/preview/drag que ja existem (handles §15/§18, blocos de legenda). Decidir data model
  e rotas no design pass (BACKEND §33 "A FAZER").

## 27. Editor de crop por KEYFRAME: IMPLEMENTADO + EM PRODUCAO (2026-06-30) [implementa §26]

O editor de enquadramento do §26, agora FEITO e LIVE em prod (commit `7eff531`; backend BACKEND §35/§36).
Feito em STAGING e mesclado pra prod com o resto da Fase 6.

- **`components/editor/crop-stage.tsx` (NOVO):** palco 16:9 (busca o `/src169` do clipe) com a **caixa 9:16
  arrastavel SO horizontal** por cima (recorte via CSS, largura fixa `CROP_BOX_W_FRAC = 81/256` da
  largura). Enquanto toca, a caixa SEGUE a trajetoria dos keyframes. Props: `videoRef`, `src`, `cx`,
  `onDragCx`/`onCommitCx`, etc.
- **`components/editor/clip-editor.tsx`:** toggle **Legenda / Enquadramento** no header (so quando
  `crop_editable`). Reusa o MESMO `videoRef`/timeline (o 16:9 e a mesma janela do base), preservando o
  playhead na troca de modo (`restoreTimeRef`). Helpers `cxAt`/`upsertKf` (merge por `KF_MERGE_EPS`).
  Arrastar a caixa cria/atualiza um keyframe no tempo atual. **"Aplicar"** = `putKeyframes` + poll do estado
  do re-render + cache-bust (`cropVersion` no `?v=` da preview, pra trocar o base re-renderizado). **"voltar
  ao automatico"** = `putKeyframes({reset:true})`. O guard de "sair sem salvar" cobre `cropDirty`.
- **`components/editor/editor-timeline.tsx`:** 5a trilha (`.editor-track--key`) com os keyframes como
  losangos (clicar = seleciona + busca o tempo; selecionado mostra o "x" pra excluir).
- **`lib/types.ts`:** `CropKeyframe {t,cx}`, `CropKeyframes {mode,kf,auto?}`, `RerenderStatus`; `Captions`
  ganhou `keyframes?` + `crop_editable?`. **`lib/api.ts`:** `getKeyframes(id)` + `putKeyframes(id, {kf}|
  {reset:true})`.
- **`app/brand.css`:** `.editor-crop-*` (palco/mascara/caixa/grip/barra) + `.editor-track--key`/`.editor-kf`
  (dourado, pra distinguir das trilhas aqua de trim/tela-final).
- Clipe SEM 16:9 retido (legado) -> `crop_editable=false` -> sem o toggle (degrada gracioso). `next build` +
  restart do `opussb-web` feitos. `tsc`/build limpos.

**Excluir usuario no painel (subiu junto, trabalho do dono):** `app/(app)/painel/page.tsx` ganhou a acao de
excluir (so habilitada pra conta `rejected`/`inactive`), `lib/api.ts` ganhou `adminDelete(email)`,
`components/upload-console.tsx` teve ajustes. Backend: BACKEND §36.

**PENDENTE:** validacao visual num podcast real do dono (1o upload pos-deploy). Multi-apresentador
(split/3/4) segue fase posterior.

## 28. Editor timeline-first (roteiro corrido) + layout Premiere + refinamentos (2026-06-30) [implementa §22; backend BACKEND §38]

O redesign do editor que a §22 decidiu (OpusClip texto-corrido), feito em STAGING (`/opt/opussb-staging`,
Flask 5002 + next dev 3001, clipe semeado isolado sob prefixo S3 `staging/`) EM PARTES validadas com o dono,
e DEPLOYADO em prod (commit `4c87293`, merge ff). Substitui a lista de cards por um roteiro corrido e
reorganiza o editor num layout estilo Premiere. **Modo Enquadramento e o contrato de dados/save intocados.**

**Roteiro corrido (`components/editor/transcript-panel.tsx`, reescrito):** a transcricao virou um roteiro
continuo (teleprompter): cada trecho e um `<span>` inline editavel separado por espaco. Cor por estado: ativo
= pilula dourada (`var(--gold)` #F5C518, a cor da MARCA, nao o #E7B917 do mock); ja passou = texto cheio; vai
tocar = apagado. **Edicao contentEditable NAO-controlada** (valor inicial congelado + sync externo so fora de
foco) -> cursor-safe durante o play (nunca reescreve o no em edicao). Commit no `onBlur` (colapsa espacos +
trim); Enter confirma. **Auto-scroll por `scrollTop`** (NAO `scrollIntoView`, refina a §21), com guarda de
foco editavel. Busca no roteiro (case/acento-insensitive) + botao "+". Barra contextual enxuta do trecho
selecionado: inicio->fim (digitavel via `TimeField`) + Dividir/Juntar/Excluir (tirados os botoes Inicio/Fim e
os nudges +-0,1s, redundantes agora que arrastar o bloco na timeline funciona bem).

**Layout final estilo PREMIERE (`clip-editor.tsx` + `brand.css`):** previa 9:16 SOZINHA na coluna DIREITA
(altura inteira; `aspect-ratio:9/16`, cap `48vw`, min `260px`); a ESQUERDA empilha roteiro (cima) + timeline
(baixo) com **divisoria arrastavel** (`.editor-split`, `startTimelineResize`) cuja altura persiste em
localStorage (`opussb_editor_timelineH`). Vale pros 2 modos (no Enquadramento o palco 16:9 encaixa pela
largura na coluna 9:16, com tarja, sem distorcer). Substituiu um layout 1b de 3 colunas que tinha sido feito
no caminho. Musica/Estilo/Tela final sairam da coluna do roteiro pra um painel lateral **"Ajustes"** (Sheet)
acionado por botao na topbar (limpa a coluna do roteiro).

**Timeline usavel (`editor-timeline.tsx`):** zoom default por DENSIDADE (`round(46*nSeg/dur)`, clamp 28..120)
no lugar de fit-to-screen, pra os blocos nascerem pegaveis (rola na horizontal em vez de colar); `ZOOM_MAX`
48->160 (exportados `ZOOM_MIN`/`ZOOM_MAX`). Blocos: min 34px + seam de 2px, alcas de redimensionar SEMPRE
visiveis. Faixas mais altas e empilhadas: Legendas 88 (sob a regua, a que se arrasta) -> Video 76 -> Audio
104; a timeline rola na VERTICAL se a divisoria a encolher abaixo do conteudo. **Filmstrip na proporcao certa**
(CSS `background-size:auto 100%` + `repeat-x`, sem esticar/achatar o rosto) + sprite de **36 frames** (backend,
era 12, BACKEND §31.1) pra menos repeticao no zoom + `cache:no-store` no `use-filmstrip` (o browser reusava o
sprite velho cacheado). Waveform com cor antes/depois do playhead (overlay que escurece o futuro, sem
redesenhar o canvas por frame) + playhead com diamante.

**Karaoke por palavra (`words`):** `lib/types.ts` ganhou `Word {start,end,word}` + `Captions.words?`; o GET
/captions ja entrega (BACKEND §38). **O consumo VISUAL (palavra acesa no roteiro/overlay) ficou PENDENTE
(Parte 5)** - hoje o destaque e por SEGMENTO; o dado ja flui.

**Refinamentos (mesma leva):**
- **Ctrl/Cmd+S** salva (preventDefault pra nao abrir o "salvar pagina" do Chrome); funciona com foco num
  campo (blur comita a edicao inline, salva no proximo tick) via um `saveRef` sempre atual.
- **Musica no preview AO VIVO:** `<audio>` paralelo sincronizado com o video (play/pause/seek juntos, loop),
  volume = o configurado; so quando ha musica e o preview NAO e o video ja queimado (senao dobraria). Rota +
  musica no export sem legenda: BACKEND §38.
- **Atalho de zoom da timeline:** `-` afasta, `+`/`=` aproxima (x1.25, clamp `ZOOM_MIN..MAX`); respeita as
  guardas de foco/dialogo dos outros atalhos.
- **Pop-up "Sair sem salvar?" dedicado** (NAO usa o `ConfirmDialog` compartilhado, que tambem serve
  `projetos/[id]`): estilo da marca (icone de alerta dourado; "Continuar editando" = acao principal dourada,
  "Sair sem salvar" = secundaria/perigo), fade+zoom. [refina a §9/§7]
- **FIX z-index (bug bloqueante):** `.editor-shell` e `position:fixed z-index:100`; o Sheet e o Dialog do
  shadcn portam no `<body>` com z-50, entao a gaveta Ajustes e o pop-up de sair abriam ATRAS do editor
  (invisiveis - "o botao nao fazia nada"). `components/ui/sheet.tsx` e `dialog.tsx`: overlay+content de z-50
  pra **z-[200]** (acima do shell, abaixo dos toasts). Conserta TODOS os dialogos dentro do editor.

`tsc --noEmit` + `next build` limpos. **EM PRODUCAO** (`4c87293`; merge ff staging->master, `next build` +
restart com `active_jobs=0` + backup `*-20260630-211141`, rollback ref `964d265`). Esta secao IMPLEMENTA o
plano da §22 e SUPERSEDE o arranjo de 3 colunas que tinha sido tentado antes.

## 29. Pipeline de Podcast pra YouTube (16:9): implicacoes de front (DESIGN, 2026-07-01) [backend BACKEND §39]

Feature nova, pipeline PARALELO ao de clipes (nao mexe no editor 9:16). Backend detalhado em BACKEND §39
(arquitetura, estados, concat, custo). O front ainda NAO comecou (e a Parte 5 do plano); o que esta previsto:

- **Upload de podcast + escolher vinheta:** variante do `UploadConsole` (o upload com progresso ja existe), com
  um seletor de vinheta (biblioteca dinamica, molde da de telas finais, §24).
- **Tela de aprovar gancho:** apos a transcricao o job entra em `aguardando_aprovacao`; o front mostra as 3
  OPCOES de gancho (inicio/fim + razao) e deixa escolher 1 e AJUSTAR in/out REUSANDO os handles de trim do
  `editor-timeline.tsx` (§15/§28). Aprovar dispara a montagem.
- **Acompanhamento:** `useJobStatus` (polling 5s) estendido pros estados novos (`analisando_gancho`,
  `aguardando_aprovacao`, `montando`).
- **Reusa:** `UploadConsole`, os handles de trim, `useJobStatus`, o CRUD da biblioteca de telas finais, e
  `lib/api.ts` + `types.ts`. NAO toca o editor de clipe 9:16.
