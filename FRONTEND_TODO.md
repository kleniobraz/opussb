# OpusSB · TODO do Frontend (mini editor de clipe)

Contexto: o **backend** do mini editor está completo, no ar e testado de ponta a
ponta (legenda editável de texto e tempo + música de fundo, com queima sob demanda
e cache). Já existe um **editor funcional, porém cru** em `static/js/views.js`
(`openEditorModal` / `renderEditor`) com helpers em `static/js/api.js` e CSS básico
no fim de `static/style.css`. As ações abaixo são pra **validar, polir e completar**
a camada visual. Contrato detalhado em `BACKEND_INTEGRATION.md` (seção 3.1).

Stack continua **vanilla, sem build** (Nginx serve `static/` direto). Não há passo
de build: editar os arquivos em `static/` e recarregar (os assets vêm com
`Cache-Control: no-cache`, então reload normal pega a versão nova).

---

## 1. Prioritário (destrava o piloto)

- [ ] **Validar o editor no navegador.** Abrir um projeto, clicar em ✎ Editar num
  clipe e testar o fluxo inteiro: editar texto, mudar início/fim, adicionar música,
  ajustar volume, remover música, salvar. O backend está validado; o modal foi
  validado só por sintaxe (`node --check`), nunca aberto num browser.
- [ ] **Estado de "preparando" no primeiro play.** A 1a abertura de cada clipe leva
  alguns segundos porque o vídeo é renderizado sob demanda (a rota faz 302 só depois
  de queimar). Hoje o `<video>` fica só bufferizando. Mostrar um spinner/poster
  ("preparando legenda…") no primeiro load evita parecer travado. Depois é cache
  (instantâneo).
- [ ] **Recarregar o vídeo após salvar.** Ao salvar uma edição, o backend invalida o
  cache, mas o `<video>` do card continua na URL antiga. Forçar reload com
  cache-buster (`url + (url.indexOf('?')>=0?'&':'?') + 't=' + Date.now()`) depois do
  save, pra refletir a nova versão sem F5.
- [ ] **Ajustar "Copiar link".** `clip.url` agora é uma rota relativa
  (`/api/clips/<id>/video`) que **exige login** e redireciona pro S3 — não é mais um
  link público compartilhável. Decidir: copiar a URL absoluta (`location.origin` +
  path), avisar que precisa de sessão, ou esconder o "copiar" nos clipes.

## 2. Polimento (visual e UX)

- [ ] **Estilo do modal do editor.** O CSS atual (`.editor-*`, `.seg-row` no fim de
  `style.css`) é o mínimo funcional. Casar com o design system SB (espaçamento,
  tipografia, dourado, responsivo).
- [ ] **UX de tempo mais visual.** Hoje início/fim são inputs numéricos (segundos).
  Opcional: arrastar handles sobre o player, ou um botão "usar tempo atual do vídeo".
- [ ] **Indicadores no card.** `/api/status` e o objeto `Project` já trazem
  `captions` (bool); o editor traz `music`. Dá pra mostrar selos ("legenda",
  "música") no card do clipe.
- [ ] **Nome do arquivo no download.** O download sai com o nome da chave S3.
  Melhorar pro título do clipe (atributo `download` no `<a>`), se desejado.

## 3. Contrato de API (já no ar, pronto pra consumir)

```
GET    /api/clips/<id>/captions   -> { editable, segments:[{start,end,text}], music:{filename,volume}|null }
PUT    /api/clips/<id>/captions   { segments:[{start,end,text}] }            -> { ok, segments }
POST   /api/clips/<id>/music      (multipart: music=<arquivo>, volume=0..1)  -> { ok, music:{filename,volume} }
PUT    /api/clips/<id>/music      { volume:0..1 }                            -> { ok, volume }
DELETE /api/clips/<id>/music      -> { ok }
GET    /api/clips/<id>/video      -> 302 pro vídeo renderizado (é o clip.url)
```

Helpers já existentes em `static/js/api.js`: `getCaptions`, `putCaptions`,
`uploadMusic`, `setMusicVolume`, `removeMusic`. Regras:
- `editable:false` = clipe antigo (legenda fixa) ou sem fala; não dá pra editar.
- `start`/`end` em segundos relativos ao clipe; backend rejeita `end <= start` (400).
- Toda edição (texto/tempo/música) **invalida o cache** e o vídeo re-renderiza no
  próximo play. Não há "preview instantâneo" sem dar play de novo.
- Erros: `400` (dados inválidos), `409` (clipe não editável), `404` (não é o dono).

## 4. Pendências de backend (o front já chama, mas ainda não existem)

- [ ] `POST /api/projects/<id>/reprocess` — o front tem o modal, mas a rota ainda não
  existe (cai no toast de aviso).
- [ ] `GET /api/projects/<id>/download_all` — idem (botão "Baixar todos").

Se o front depender dessas duas agora, sinalizar pro backend implementar.
