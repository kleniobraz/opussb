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
- **Falta:** waveform/filmstrip reais do editor (bloqueados por CORS do S3 hoje); remover a SPA
  `static/` depois de alguns dias de cutover validado; guarda do botão Voltar do *navegador*
  (popstate) no editor — ver §7 e §9.

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
  erro/vazio. (Faltam só `reprocess` e `download_all`, que degradam em toast.)

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
  Preferences, UploadOptions). **`lib/format.ts`** (fmtBytes/Score/Seconds, timeAgo, hashStr,
  nome/iniciais/saudação).
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
- **Casca (app):** `Topbar` (nav com aba ativa) + `MobileMenu` (drawer via Sheet) + `RequireAuth`
  (loading/guest→login/must_change→senha).
- **Home (`/`):** hero + `ShaderBackground` (three lazy) + `UploadConsole` (dropzone, presets,
  validação de formato/tamanho, opções; aplica os padrões salvos) → `useJobStatus` polling →
  `JobProgress` (barra/status + resultados em `ClipGrid`) + `HowItWorks` (IntersectionObserver) +
  `Recent` (featured + grid dos recentes via `getStats`).
- **Projetos (`/projetos`):** grade + ordenação (Todos/Melhores notas/Mais recentes) + skeleton +
  empty. **Detalhe (`/projetos/[id]`):** cabeçalho com meta + ações (Baixar todos / Reprocessar /
  Excluir, as duas primeiras degradam em toast) + `ClipGrid`. **Favoritos.** **Config** (Tabs:
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
  + UI de alças (custo de storage/pipeline; decisão de produto).
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
