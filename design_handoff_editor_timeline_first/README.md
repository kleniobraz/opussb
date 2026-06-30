# Handoff: Editor integrado — redesign "Timeline-first" (opção 1b)

## Overview
Redesign do editor de vídeo/legendas. Esta entrega documenta a **opção 1b ("Timeline-first")**, escolhida pelo cliente.
O conceito central muda o modelo de edição de legendas:

- A **transcrição** deixa de ser uma lista de cards e vira um **roteiro contínuo (estilo teleprompter)**, onde a palavra/trecho ativo acende e o texto é editável inline.
- As **legendas** viram **blocos numa faixa dedicada da timeline**, posicionados por tempo, sobre a faixa de áudio (waveform).
- Tudo é sincronizado: durante o play, a palavra ativa no roteiro, o overlay no vídeo e o playhead na timeline se movem juntos.

> O arquivo HTML também contém a opção **1a** (lista de cards sincronizados) — **pode ser ignorada**. A opção a implementar é a **1b**.

## About the Design Files
Os arquivos deste pacote são **referências de design feitas em HTML** — um protótipo que demonstra a aparência e o comportamento pretendidos, **não código de produção para copiar diretamente**.

O protótipo foi escrito como um "Design Component" (um runtime próprio de prototipagem: `support.js` + template/lógica embutidos no `.dc.html`). **Não traga `support.js` nem a estrutura `<x-dc>` para o codebase de vocês.** A tarefa é **recriar a UI 1b no ambiente existente (React + Next.js)**, usando os componentes, padrões de estado e libs já estabelecidos no projeto de vocês. A lógica em JS dentro do `.dc.html` é uma referência clara de comportamento — reescreva como hooks/estado React idiomáticos.

## Fidelity
**Alta fidelidade (hifi).** Cores, tipografia, espaçamentos e interações são finais. Recrie a UI fiel ao pixel usando as libs do codebase. Os valores exatos estão em **Design Tokens** abaixo.

## Telas / Views

### Tela única: Editor (com 3 zonas)
Container do editor: **largura 1340px** (responsivo, `max-width:100%`), **altura 806px**, `background:#0b0b0c`, `border:1px solid rgba(255,255,255,0.09)`, `border-radius:16px`, `overflow:hidden`. Sombra: `0 30px 80px rgba(0,0,0,0.45)`.
Dois "glows" radiais decorativos no topo (atrás de tudo, `pointer-events:none`):
- topo-esquerda: `radial-gradient(closest-side, rgba(34,211,211,0.14), transparent)`, 440×300px, deslocado `top:-130px; left:-110px`
- topo-direita: `radial-gradient(closest-side, rgba(43,212,155,0.09), transparent)`, mesmo tamanho, `right:-110px`

Estrutura vertical (flex column): **Topbar (58px)** → **Body (flex:1)** → **Transport (52px)** → **Timeline (198px)**.

---

#### 1. Topbar — altura 58px
`display:flex; align-items:center; gap:14px; padding:0 16px; border-bottom:1px solid rgba(255,255,255,0.07); background:rgba(11,11,12,0.6)`.
Da esquerda para a direita:
- **Botão voltar**: 36×36, `border-radius:9px`, fundo transparente, `border:1px solid rgba(255,255,255,0.1)`, ícone chevron-left (stroke 2). Hover: `background:rgba(255,255,255,0.08); color:#fff`.
- **Bloco de título** (max-width 440, `text-overflow:ellipsis`):
  - Linha 1: "Vale a pena investir em um conselho estratégico?" — Hanken Grotesk 600, 13.5px, `#F3F2EC`.
  - Linha 2 (subtítulo): "Resposta de quem tem 35 anos de mercado" — 400, 11px, `rgba(243,242,236,0.42)`.
- **Spacer** (`flex:1`).
- **Toggle de legenda (segmented control)**: pílula `background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:3px`. Dois botões: "Com legenda" / "Sem legenda", cada um 600/12px, `border-radius:7px; padding:6px 13px`. Ativo: `color:#0b0b0c; background:#E7B917`. Inativo: `color:rgba(243,242,236,0.55); background:transparent`. Transição `all .15s`.
- **Spacer** (`flex:1`).
- **Botão "Salvar"** (secundário): ícone floppy + texto, 600/12.5px, `color:rgba(243,242,236,0.85)`, `background:rgba(255,255,255,0.05)`, `border:1px solid rgba(255,255,255,0.1)`, `border-radius:10px; padding:9px 14px`. Hover: `background:rgba(255,255,255,0.1); color:#fff`.
- **Botão "Exportar"** (primário): ícone download + texto, 700/12.5px, `color:#0b0b0c`, `background:#DCB12C`, `border-radius:10px; padding:10px 17px`. Hover: `background:#edc23a`.

#### 2. Body — flex row, ocupa o espaço restante
**2a. Coluna de prévia (esquerda)** — largura fixa **308px**, `padding:18px 16px; border-right:1px solid rgba(255,255,255,0.07)`, flex column, `align-items:center; gap:11px`.
- Linha de meta (topo): chip "9:16" (`font: JetBrains Mono 500/11px`, `color:rgba(243,242,236,0.72)`, `background:rgba(255,255,255,0.06)`, `border:1px solid rgba(255,255,255,0.09)`, `border-radius:7px; padding:4px 9px`) + texto "prévia ao vivo" (400/11px, `rgba(243,242,236,0.38)`).
- **Player de vídeo**: `aspect-ratio:9/16`, ocupa o resto da altura (`flex:1`), `border-radius:16px; overflow:hidden`, sombra `0 20px 50px rgba(0,0,0,0.55)`. Clique alterna play/pause. **No codebase real, este é o elemento `<video>`.** No protótipo é uma imagem placeholder (`assets/video-clean.png`).
  - **Overlay de legenda** (visível só quando "Com legenda" e há legenda ativa): centralizado, `bottom:40px`, texto Hanken Grotesk **700/17px**, `line-height:1.28`, `color:#fff`, `text-shadow:0 2px 9px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.9)`, `text-align:center`. Conteúdo = texto da legenda ativa.
  - **Affordance de play** (visível quando pausado): círculo 54×54 centralizado, `background:rgba(11,11,12,0.5); backdrop-filter:blur(5px); border:1px solid rgba(255,255,255,0.22)`, ícone play branco 22px.

**2b. Coluna do roteiro (direita)** — `flex:1`, flex column.
- **Header** (`padding:16px 20px 13px; border-bottom:1px solid rgba(255,255,255,0.06)`, flex row, gap 12): "Roteiro" (600/13.5px, `#F3F2EC`) + contador "{n} linhas" (JetBrains Mono 500/10.5px, `rgba(243,242,236,0.4)`) + spacer + **campo de busca** (largura 210px, `background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.09); border-radius:9px; padding:7px 11px`, ícone lupa + placeholder "Buscar no roteiro…" 400/12px `rgba(243,242,236,0.38)`) + **botão "+"** (adicionar trecho): `color:#E7B917; background:rgba(231,185,23,0.12); border:1px solid rgba(231,185,23,0.3); border-radius:9px; padding:7px 11px`, ícone plus. Hover: `background:rgba(231,185,23,0.2)`.
- **Área do roteiro (teleprompter)**: scroll vertical, `padding:22px 26px 26px`, fonte Hanken Grotesk **500/17px**, **`line-height:2.15`** (o line-height alto dá respiro para os "pills" de destaque), cor base `rgba(243,242,236,0.4)`.
  - Cada legenda é um **segmento inline** (um `<span>`) separado por um espaço. `display:inline; padding:3px 5px; border-radius:6px; cursor:text; box-decoration-break:clone`.
  - **Estados de cor do segmento** (efeito teleprompter):
    - **Ativo** (tocando agora): `color:#0b0b0c; background:#E7B917` (pill dourado).
    - **Já passou** (`t >= fim`): `color:rgba(243,242,236,0.92)` (texto cheio).
    - **Ainda vai tocar**: `color:rgba(243,242,236,0.38)` (apagado).
    - Transição `color .16s, background .16s`. Hover: `background:rgba(255,255,255,0.07)`.
  - Cada segmento é **`contentEditable`**: clicar dá seek para o início daquela legenda **e** permite editar o texto; Enter confirma (faz blur); blur faz commit (colapsa espaços e dá trim). Ao focar para editar, o play é pausado.
  - O segmento ativo faz **auto-scroll** suave para ficar visível enquanto toca.

#### 3. Transport — altura 52px
`display:flex; align-items:center; gap:14px; padding:0 18px; border-top:1px solid rgba(255,255,255,0.07)`.
- Grupo de transporte (gap 7): **anterior** (ícone skip-back, 34×34 ghost) · **play/pause** (círculo **42×42**, `background:#E7B917; color:#0b0b0c`; hover `#f2c521`; ícone alterna entre triângulo de play e duas barras de pause) · **próximo** (skip-forward, 34×34 ghost).
  - Anterior/Próximo pulam para o início da legenda anterior/seguinte.
- **Tempo**: JetBrains Mono 500/12.5px, `#F3F2EC`, formato `M:SS.cc  /  M:SS.cc` (ex.: `0:12.20  /  0:42.14`).
- Spacer.
- Texto de atalhos: "espaço: tocar · ← → navegar (shift = 5s)" — 400/11px, `rgba(243,242,236,0.36)`.
- Botão **tela cheia** (34×34 ghost, ícone de cantos).
- Divisor vertical 1×22 `rgba(255,255,255,0.1)`.
- Grupo de zoom (gap 9): **zoom-** (30×30 ghost, ícone minus) · **slider de volume** (input range custom, largura 78px, trilho 4px `rgba(255,255,255,0.18)`, thumb 12×12 `#F3F2EC`, valor inicial 72) · **zoom+** (30×30 ghost, ícone plus).
- Botões ghost padrão: `background:transparent; border:none; color:rgba(243,242,236,0.72); border-radius:8px`. Hover: `background:rgba(255,255,255,0.08); color:#fff`.

#### 4. Timeline — altura 198px
`padding:12px 18px 16px; border-top:1px solid rgba(255,255,255,0.07)`.
- **Legenda (legend row)** no topo (gap 18): 4 itens, cada um = quadradinho 9×9 (`border-radius:3px`) + rótulo 500/10.5px `rgba(243,242,236,0.5)`:
  - `#E7B917` "Legendas" · `rgba(231,185,23,0.45)` "Áudio" · `#2BD49B` "Corte" · `#22D3D3` "Aparar".
- **Área rolável horizontal** (`overflow-x:auto`): contém um inner com `width:{100*zoom}%; min-width:100%; position:relative`. Tudo dentro é posicionado em `%` do tempo total (duração 42.14s). Camadas, de cima para baixo:
  1. **Régua (18px)**: ticks a cada 1s. Tick "maior" a cada 5s (linha 1×7 `rgba(255,255,255,0.24)` + rótulo do número em JetBrains Mono 500/9px `rgba(243,242,236,0.4)`); tick "menor" nos demais (linha 1×4 `rgba(255,255,255,0.1)`). Cada tick centralizado em `left:{t/dur*100}%` com `translateX(-50%)`.
  2. **Faixa de legendas (36px, `margin-top:6px`)**: trilho `border-radius:8px; background:rgba(255,255,255,0.02)`. Cada legenda é um **bloco absoluto**: `left:{start/dur*100}%; width:{(end-start)/dur*100}%; top:4px; bottom:4px; min-width:5px; border-radius:5px`, texto 600/10px truncado (`white-space:nowrap; overflow:hidden`), `padding:0 6px`.
     - **Ativo**: `background:#E7B917; color:#0b0b0c; border:1px solid #E7B917`.
     - **Selecionado** (não ativo): borda `#F3F2EC`.
     - **Normal**: `background:rgba(231,185,23,0.26); color:#E7B917; border:1px solid rgba(231,185,23,0.4)`.
     - **Curta** (duração < 0.8s): `background:rgba(231,185,23,0.16)` (mais apagado).
     - Hover: `filter:brightness(1.12)`. Clique = seek para o início + seleciona.
  3. **Faixa de áudio (82px, `margin-top:7px`)**: trilho `border-radius:8px; background:rgba(255,255,255,0.025); overflow:hidden`.
     - **Waveform**: 172 barras de 3px (`border-radius:2px`), distribuídas com `justify-content:space-between`, centralizadas verticalmente. Altura de cada barra entre ~5 e ~59px (ver geração em Design Tokens). Cor: barras **antes do playhead** = `rgba(231,185,23,0.85)`; **depois** = `rgba(231,185,23,0.32)`. Clicar na waveform dá seek (proporcional à posição X).
     - **Regiões aparadas**: retângulos escuros `rgba(8,8,10,0.62)` cobrindo de 0 até `trimIn` e de `trimOut` até o fim.
     - **Handles de aparar (trim)**: barras verticais 7px `#22D3D3`, `border-radius:4px`, `box-shadow:0 0 10px rgba(34,211,211,0.5)`, `cursor:ew-resize`, centradas em `trimIn` e `trimOut`.
     - **Linha de corte**: vertical 1.5px `#2BD49B` em `cut`.
  4. **Chip do corte**: rótulo "corte M:SS.cc" (ex.: "corte 0:29.82"), JetBrains Mono 600/9px `#2BD49B`, `background:#0b0b0c; border:1px solid rgba(43,212,155,0.4); border-radius:5px; padding:2px 5px`, posicionado sobre a régua na posição do corte.
  5. **Playhead**: linha vertical 2px `#E7B917`, `box-shadow:0 0 10px rgba(231,185,23,0.7)`, do topo da faixa de legendas até a base da faixa de áudio (`top:24px; bottom:8px`), em `left:{t/dur*100}%`. No topo, um "diamante" 11×11 `#E7B917` (`rotate(45deg)`). `pointer-events:none`.

## Interactions & Behavior
- **Play/Pause**: loop por `requestAnimationFrame`. `dt` por frame limitado a 0.05s. Ao chegar no fim, para. Tocar de novo do fim reinicia em 0. (Atalhos de teclado como "espaço" estão documentados no texto da UI — implemente conforme o padrão de vocês.)
- **Seek por clique na waveform**: `r = (clientX - rect.left) / rect.width; t = r * duração`.
- **Selecionar legenda** (clique no segmento do roteiro ou no bloco da timeline): dá seek para `start` e marca `selectedId`.
- **Anterior/Próximo**: vão para o `start` da legenda anterior/seguinte (ou 0 / duração nas pontas).
- **Toggle Com/Sem legenda**: liga/desliga o overlay no vídeo (não altera dados).
- **Zoom**: 1 a 4, passo 0.5. Escala a largura do inner da timeline (`width:{100*zoom}%`) — o container externo faz scroll horizontal. As posições em `%` permanecem corretas.
- **Editar texto** (inline no roteiro): `contentEditable`; foco pausa o play; Enter faz blur; blur faz commit (colapsa whitespace + trim). Importante: ao re-renderizar durante o play, **não** reescrever o texto do nó em edição (preservar o cursor). Em React, controle isso com `key` estável + edição não-controlada (commit no `onBlur`), ou um editor dedicado.
- **Adicionar trecho ("+")**: cria uma legenda nova ao final (start = fim da última, duração 1s) e seleciona.
- **Excluir**: remove a legenda (no protótipo 1b a exclusão fica na 1a; na 1b adicione um affordance de excluir no bloco/segmento conforme padrão de vocês).
- **Auto-scroll**: o segmento ativo do roteiro entra em vista automaticamente durante o play (ajustar `scrollTop` do container; **não** usar `scrollIntoView`).
- **Hover states**: descritos por componente acima.

## State Management
Estado mínimo (sugestão de shape):
```ts
type Caption = { id: number; start: number; end: number; text: string; short: boolean }; // short = (end-start) < 0.8

state = {
  duration: number;     // 42.14 no mock
  t: number;            // tempo atual (s); inicia em 12.20 no mock
  playing: boolean;     // false
  captionsOn: boolean;  // true = "Com legenda"
  selectedId: number | null;
  zoom: number;         // 1..4, passo 0.5
  captions: Caption[];
}
// constantes de timeline (no mock): trimIn = 9.4, trimOut = 39.0, cut = 29.82
```
Derivados (computar no render):
- `activeId` = legenda onde `start <= t < end`.
- `overlayShow` = `captionsOn && activeId != null`; `overlayText` = texto da ativa.
- Posições em `%` = `valor / duration * 100`.
- Cor de cada barra da waveform conforme `(i/total) <= t/duration`.

Dados reais devem vir da transcrição/ASR do backend de vocês; o array no mock é só conteúdo de exemplo. Formatação de tempo: `M:SS.cc` (2 casas) no transport e `M:SS.c` (1 casa) onde houver timecodes curtos.

## Design Tokens

**Cores**
| Uso | Valor |
|---|---|
| Fundo do editor | `#0b0b0c` |
| Fundo da página | `#08080a` |
| Tinte de painel | `rgba(255,255,255,0.012)` |
| Borda principal / sutil / forte | `rgba(255,255,255,0.07)` / `0.06` / `0.1` |
| Borda externa do card | `rgba(255,255,255,0.09)` |
| **Dourado primário (ativo/acento)** | `#E7B917` |
| Dourado botão (Exportar) | `#DCB12C` (hover `#edc23a`) |
| Dourado play hover | `#f2c521` |
| Douradas translúcidas | `rgba(231,185,23, 0.55 / 0.4 / 0.32 / 0.26 / 0.16 / 0.12)` |
| **Ciano (aparar/trim)** | `#22D3D3` (glow `rgba(34,211,211,0.5)`) |
| **Verde (corte/cut)** | `#2BD49B` (borda `rgba(43,212,155,0.4)`) |
| Texto primário | `#F3F2EC` |
| Texto secundário/apagado | `rgba(243,242,236, 0.92 / 0.55 / 0.42 / 0.4 / 0.38 / 0.36)` |
| Overlay de legenda (vídeo) | `#fff` + `text-shadow:0 2px 9px rgba(0,0,0,0.9)` |
| Glow decorativo (teal / green) | `rgba(34,211,211,0.14)` / `rgba(43,212,155,0.09)` |

**Tipografia**
- UI: **Hanken Grotesk** (400, 500, 600, 700) — Google Fonts.
- Monoespaçada (timecodes, números, rótulos técnicos): **JetBrains Mono** (400, 500, 600) — Google Fonts.
- Escala: título 13.5/600 · subtítulo 11/400 · seção 13.5/600 · contador/labels mono 9–10.5/500 · roteiro 17/500 com line-height **2.15** · bloco de legenda 10/600 · tempo do transport 12.5/500 mono.

**Espaçamento / raios / sombras**
- Raios: botões/inputs 7–10px · cards/trilhos 8px · player de vídeo 16px · editor 16px.
- Sombras: editor `0 30px 80px rgba(0,0,0,0.45)` · vídeo `0 20px 50px rgba(0,0,0,0.55)`.
- Alturas das zonas: Topbar 58 · Transport 52 · Timeline 198 · faixa de legendas 36 · faixa de áudio 82 · régua 18.

**Geração da waveform (mock)** — para reproduzir o desenho de exemplo (substitua por dados reais de áudio):
```js
const wave = Array.from({length:172}, (_,i) => {
  const v = Math.abs(Math.sin(i*0.55)*0.5 + Math.sin(i*0.21+1)*0.32 + Math.sin(i*1.7)*0.22 + Math.sin(i*0.07)*0.16);
  return Math.min(1, 0.12 + v*0.9);           // 0..1
});
const barHeightPx = Math.round(5 + value*54); // ~5..59px
```

## Assets
- `assets/video-clean.png` — **placeholder** do frame de vídeo (retrato 9:16). No app real, troque por um elemento `<video>` com a mídia do projeto; o overlay de legenda fica por cima.
- Ícones: todos são **SVG inline** desenhados no protótipo (chevron, floppy/salvar, download/exportar, play, pause, skip-back, skip-forward, tela cheia, plus, minus, lupa, lixeira). Use a biblioteca de ícones de vocês (ex.: lucide/heroicons) — os equivalentes são óbvios pelos nomes.
- Fontes: via Google Fonts (`Hanken Grotesk`, `JetBrains Mono`).

## Files
- `Editor integrado.dc.html` — protótipo de referência. Contém **1a** (ignorar) e **1b** (implementar). A lógica de comportamento está na classe `Component` ao final do arquivo; a marcação da 1b está na seção com `id="1b"`.
- `assets/video-clean.png` — placeholder do vídeo.

> Dica para o Claude Code: abra o `.dc.html`, localize o bloco `id="1b"` para a estrutura/estilos e a classe `Component` para o comportamento (loop de play, seek, seleção, edição, zoom). Recrie como componentes React no padrão do codebase; não copie `support.js` nem as tags `<x-dc>`/`<sc-for>`/`<sc-if>` (são do runtime do protótipo).
