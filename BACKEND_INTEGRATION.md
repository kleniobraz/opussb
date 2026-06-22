# OpusSB · Integração Front ↔ Backend

Documento do que foi entregue no **frontend** e do que o **backend** precisa implementar para
"encaixar" nas funcionalidades novas. Stack do front continua **vanilla, sem build** (Nginx serve os
arquivos estáticos direto). Nada do contrato atual foi quebrado.

Data: 2026-06-18.

---

## 1. Como o front lida com o que ainda não existe (modo demonstração)

Toda chamada a uma rota **nova** passa por `js/api.js`. O padrão é:

1. O front faz `fetch` na rota real.
2. Se responder **200 JSON**, usa o dado real.
3. Se responder **404 / erro / falha de rede**, cai num **mock** (dados de exemplo) e liga o badge
   **"Modo demonstração"** (canto inferior esquerdo).
4. `401` em qualquer rota → o front volta para a tela de login (sessão expirada).

**Consequência prática:** assim que o backend implementar cada rota abaixo e devolver o JSON no formato
esperado, o dado real passa a aparecer **sem nenhuma mudança no front**. O badge some quando nenhuma
resposta veio de mock na sessão.

Mutações (favoritar, excluir, cancelar, reprocessar, salvar preferências) também são "fire and forget":
o front tenta o backend e, enquanto não existir, mostra um toast explicando qual rota falta. Favoritos e
preferências já funcionam **localmente** (localStorage) para o usuário não ficar travado.

---

## 2. Rotas REAIS já usadas (contrato atual — mantido)

Sem mudanças obrigatórias, mas há **campos novos opcionais** no upload:

### `POST /api/upload` (multipart/form-data)
Campos atuais: `video`, `min_duration`, `max_duration`.
**Campos opcionais que o front envia:**

| Campo          | Tipo (string no multipart) | Valores                          | Status no backend |
|----------------|----------------------------|----------------------------------|-------------------|
| `captions`     | `"true"` / `"false"`       | queimar legenda no estilo SB     | **HONRADO** (decidido no upload, gravado no job, aplicado em passe único; default ligado) |
| `language`     | string                     | `pt` \| `en` \| `es` \| `auto`   | ignorado (feature de pipeline/N8N, adiada) |
| `aspect_ratio` | string                     | `9:16` \| `1:1` \| `16:9`        | ignorado (saída é sempre 9:16 por enquanto) |

A decisão de legenda é tomada **antes** de processar: o `captions` do upload define se a legenda é
queimada, num passe único (sem reprocessar). O estado fica gravado no job e é devolvido em
`GET /api/status/{id}` e nos objetos `Project` como `"captions": true|false`.

Resposta atual mantida: `202 {job_id}`; erros `400/401/403/429 {error}`.

### `GET /api/status/{job_id}`
Mantém `{status, progress, filename}`. **Enriquecimento recomendado** no estado `done`:
hoje `clips` é uma lista de URLs (string). O front **já aceita os dois formatos**, mas para os cards
mostrarem nota/título/motivo, devolva **lista de objetos `Clip`** (ver seção 4):

```json
{
  "status": "done",
  "progress": 100,
  "filename": "ep14.mp4",
  "clips": [ { "id": "...", "url": "...", "score": 8.4, "title": "...", "reason": "...", "duration": 42 } ],
  "partial_clips": []
}
```

`GET /api/me`, `POST /api/login`, `POST /api/logout`, `POST /api/change_password`: inalterados.

---

## 3. Rotas NOVAS a implementar

Todas exigem **sessão autenticada** (mesmo gate de `/api/me`). Em não autenticado, devolver
`401 {auth:false}` (o front trata mostrando o login). Respostas em JSON.

| # | Método & rota | Usada em | Resposta esperada |
|---|---------------|----------|-------------------|
| 1 | `GET /api/stats` | Dashboard (Início) | `Stats` |
| 2 | `GET /api/projects?search=&sort=&page=` | Página Projetos | `{ projects: Project[], total: int }` |
| 3 | `GET /api/projects/{id}` | Detalhe do projeto | `Project` + `{ clips: Clip[] }` |
| 4 | `DELETE /api/projects/{id}` | Botão Excluir | `200 { ok: true }` |
| 5 | `GET /api/projects/{id}/download_all` | Botão "Baixar todos" | arquivo `.zip` (com `Content-Disposition`) **ou** `200 { url: "<zip presignado>" }` |
| 6 | `POST /api/projects/{id}/reprocess` | Botão Reprocessar | `202 { job_id }` |
| 7 | `POST /api/jobs/{id}/cancel` | Botão Cancelar (durante processamento) | `200 { ok: true }` |
| 8 | `GET /api/clips/favorites` | Página Favoritos | `{ clips: Clip[] }` |
| 9 | `POST /api/clips/{id}/favorite` | Favoritar | `200 { ok: true }` (sem corpo na request) |
| 9 | `DELETE /api/clips/{id}/favorite` | Desfavoritar | `200 { ok: true }` |
| 10 | `GET /api/preferences` | (opcional) | `Preferences` |
| 11 | `PUT /api/preferences` | Salvar preferências | `200 { ok: true }` |

### Corpos das requisições

`POST /api/projects/{id}/reprocess` (JSON):
```json
{ "min_duration": 30, "max_duration": 90, "captions": true }
```

`PUT /api/preferences` (JSON — chaves exatamente como o front envia):
```json
{
  "defaultMin": 30,
  "defaultMax": 90,
  "aspect": "9:16",
  "language": "pt",
  "captions": true,
  "autoplayPreview": false
}
```
> Obs.: hoje as preferências vivem no `localStorage` e são **espelhadas** para o backend via `PUT`.
> Se quiser que o servidor seja a fonte da verdade, implemente `GET /api/preferences` no mesmo formato;
> é só ligar a leitura no `js/store.js`/`js/views.js` depois (ponto único, marcado).

---

## 3.1 Legenda editável + mini editor de clipe

A legenda **não** é mais queimada no processamento. O clipe sai como base sem
legenda e a queima é **sob demanda** na rota de vídeo (com cache). Os segmentos
(texto + tempos) **e a posição vertical** ficam editáveis no backend.

> **Atualizado em 2026-06-21:** rota `GET /…/base`, campo `caption_y` (em GET/PUT
> `/captions`) e o objeto `music` na resposta de `/captions`. Tudo **aditivo**, já em
> produção (`test_backend.py` passa). Origem: editor de clipe estilo OpusClip do front Next.

### `GET /api/clips/{clip_id}/video`
Entrega o vídeo do clipe: **302** pra URL presignada do S3 (queima a legenda na 1a
abertura, cacheia, e nas próximas redireciona pro cache). É o que vem em `Clip.url`;
o front usa direto em `<video src>` e no download (segue o redirect sozinho).

### `GET /api/clips/{clip_id}/base`
**302** pra URL presignada do S3 do clipe **SEM** legenda queimada (`clip_NN_base.mp4`).
Usado pelo editor: o preview toca a base limpa e o front **sobrepõe a legenda AO VIVO**
(sem duplicar com a queimada), e usa essa mesma URL no "Exportar sem legenda". Para clipe
antigo (`_9x16.mp4`, legenda já fixa) cai na própria base — por isso o editor trata esse
caso como "Edição indisponível".

### `GET /api/clips/{clip_id}/captions`
Devolve a legenda editável, a música de fundo e a posição vertical da legenda:
```json
{
  "editable": true,
  "segments": [ { "start": 0.0, "end": 1.08, "text": "..." } ],
  "music": { "filename": "trilha.mp3", "volume": 0.15 },
  "caption_y": 0.67
}
```
- `editable:false` = clipe antigo (legenda já fixa) ou sem fala. `start`/`end` em
  segundos relativos ao clipe.
- `music` = `null` quando não há trilha adicionada.
- `caption_y` = fração `0..1` do **centro da legenda a partir do topo** (ex.: `0.67` ≈ 2/3
  abaixo), ou `null` = posição padrão do estilo SB. Ao salvar, o backend faz clamp em
  `0.05..0.95`.

### `PUT /api/clips/{clip_id}/captions`
Salva a legenda corrigida (nome/empresa/etc) **e** a posição vertical, e **invalida o
cache** (o próximo play re-queima com o texto/posição novos). Corpo:
```json
{
  "segments": [ { "start": 0.0, "end": 1.08, "text": "Texto corrigido" } ],
  "caption_y": 0.67
}
```
- `caption_y` é **opcional**: chave ausente = não mexe na posição; `null` = volta ao padrão
  do estilo SB; número = reposiciona (aplicado na queima via `\an5\pos`, centralizado).

Resposta `200 { ok: true, segments: [...] }`. Erros: `400` (segmento mal formado, ex.
`end <= start`, ou `caption_y` não numérico), `409` (clipe não editável), `404` (não é o dono).

### Música de fundo do clipe (mixada na queima sob demanda)
Rotas em `/api/clips/{clip_id}/music`, todas usadas pelo editor:
- **`POST`** (multipart, campo `music`) — adiciona a trilha; valida que tem áudio de verdade
  (ffprobe) e invalida o cache. Responde `{ ok: true, music: { filename, volume } }`.
- **`PUT`** (JSON `{ "volume": 0.0..1.0 }`) — ajusta o volume da mixagem.
- **`DELETE`** — remove a trilha.

Detalhe completo (passe único de queima, cache `_cap.mp4`) no `BACKEND_CONTEXT.md`.

---

## 4. Modelos de dados (JSON)

### `Clip`
```json
{
  "id": "p1-c3",
  "project_id": "p1",
  "url": "/api/clips/p1-c3/video",        // rota de vídeo (302 -> S3, queima sob demanda); pronta para <video> e download
  "thumbnail_url": "https://.../t3.jpg",  // opcional (poster do card; se vazio, usa placeholder)
  "title": "A regra de ouro que ninguém te conta",
  "reason": "Gancho forte nos 3 primeiros segundos.",
  "score": 8.4,                            // 0 a 10 (usado no badge: verde >=8, dourado 6-8, cinza <6)
  "duration": 42,                          // segundos (opcional)
  "favorite": false                        // opcional; se ausente, o front usa o estado local
}
```

### `Project`
```json
{
  "id": "p1",
  "title": "ClubCast · Ep. 14",
  "filename": "clubcast-ep14.mp4",
  "created_at": "2026-06-18T12:00:00Z",   // ISO 8601
  "status": "done",                        // done | processing | error
  "thumbnail_url": "https://.../cover.jpg",// opcional
  "clip_count": 9,
  "avg_score": 8.1,
  "duration": 4127
}
```

### `Stats`
```json
{
  "videos_processed": 12,
  "clips_generated": 84,
  "avg_score": 7.4,
  "processing_count": 0,
  "recent": [ /* até 4 Project */ ]
}
```

### `Preferences`
Mesmo shape do corpo de `PUT /api/preferences` (seção 3).

---

## 5. Regras de negócio respeitadas (não inventar features)

- **Autenticação é só email + senha** (sem OAuth/Google/Apple, sem self-signup, sem link mágico).
  Contas seguem criadas via `manage_users.py`. A tela de login só reflete isso.
- **1 job por vez**: o upload continua sujeito ao `429` do backend. Não há fila no front.
- **Sem créditos / planos / storage / tutoriais**: nada disso foi adicionado (são features inexistentes
  do OpusClip que ficaram de fora de propósito).
- **HTTP sem TLS**: nada depende de contexto seguro além do que já era usado. `navigator.clipboard`
  (copiar link) tem fallback via `execCommand` quando indisponível.
- A faixa "Como funciona" e o player são informativos/decorativos, sem prometer ações inexistentes.

---

## 6. O que foi entregue no front (resumo das funcionalidades)

Arquitetura virou uma **SPA vanilla** com roteamento por hash e navegação na sidebar:

1. **Roteador hash** (`#/`, `#/projetos`, `#/projetos/:id`, `#/favoritos`, `#/config`) + sidebar com itens reais e estado ativo.
2. **Dashboard (Início)**: cards de estatísticas + projetos recentes (`GET /api/stats`).
3. **Envio aprimorado**: validação client-side de formato e tamanho (15GB), exibição do tamanho do arquivo, drag-and-drop.
4. **Presets de formato/duração** (Shorts/Reels/TikTok/Longo) que preenchem os campos.
5. **Opções de processamento** (legendas, idioma, formato de saída) enviadas no upload.
6. **Página Projetos** (histórico) com grid, skeleton, vazio e erro (`GET /api/projects`).
7. **Detalhe do projeto** com clips e ações (`GET /api/projects/:id`).
8. **Busca, filtro por nota e ordenação** nos grids de clipes (client-side).
9. **Favoritar clipes** (localStorage + `POST/DELETE /api/clips/:id/favorite`) e **página Favoritos**.
10. **Player modal** do clipe (assistir, copiar link, baixar, favoritar).
11. **Baixar todos** os clipes do projeto (`GET /api/projects/:id/download_all`).
12. **Cancelar processamento** durante o job (`POST /api/jobs/:id/cancel`).
13. **Reprocessar** projeto com novos parâmetros (`POST /api/projects/:id/reprocess`).
14. **Configurações**: trocar senha + preferências (durações/formato/idioma padrão) persistidas localmente e espelhadas no backend.
15. **Toasts** de feedback, **modal** reutilizável e **confirmações**.
16. **Skeleton loaders**, **estados vazios** e **estados de erro** consistentes.
17. **Atalhos de teclado** (`n` novo, `g h/p/f/c` navegação, `?` ajuda, `Esc` fechar).
18. **Badge de modo demonstração** enquanto as rotas novas não existem.
19. **Polimento responsivo** e acessibilidade (foco no modal, `aria`, navegação por teclado).

### Arquivos do front
- `static/index.html` — shell multi-view, sidebar, hero/upload, roots de toast/modal/badge.
- `static/style.css` — design system SB (dark + dourado + Space Grotesk) + componentes novos.
- `static/app.js` — core: fluxos reais (auth/upload/status/resultado) + roteamento + atalhos.
- `static/js/store.js` — preferências/favoritos/sessão de UI (localStorage).
- `static/js/ui.js` — toasts, modal, skeletons, formatadores.
- `static/js/api.js` — camada de dados (rotas reais + futuras com fallback de mock).
- `static/js/router.js` — roteador hash.
- `static/js/views.js` — componentes de clipe e as páginas (dashboard/projetos/detalhe/favoritos/config).

### Ordem de prioridade sugerida para o backend
1. Enriquecer `GET /api/status` com objetos `Clip` (destrava nota/título/motivo no card — maior impacto visível).
2. `GET /api/projects` + `GET /api/projects/:id` (histórico, o coração da navegação).
3. `GET /api/stats` (dashboard).
4. Favoritos (`GET /api/clips/favorites`, `POST/DELETE /api/clips/:id/favorite`).
5. Ações: `download_all`, `cancel`, `reprocess`, `DELETE projeto`.
6. Preferências (`PUT`/`GET /api/preferences`).
