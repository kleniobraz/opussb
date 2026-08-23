# Guia de Configuração do Workflow N8N — OpusSB

## Visão Geral

Este workflow é o orquestrador central do OpusSB. Ele é acionado por um webhook disparado pelo `processor.py` (Flask) e executa as seguintes etapas:

1. Recebe os dados do job (ID, URL do áudio, durações mínima e máxima)
2. Atualiza o status do job no Flask
3. Submete o áudio para transcrição no RunPod (Faster Whisper)
4. Faz polling do RunPod até que o job seja concluído
5. Envia a transcrição ao Claude API para seleção de clipes virais
6. Retorna os resultados ao Flask via callback

**Pré-requisitos:**
- N8N rodando em `localhost:5678` (Docker)
- Flask rodando em `localhost:5001`
- Chaves de API: `RUNPOD_API_KEY` e `ANTHROPIC_API_KEY`

---

## Step 0: Configurar Credenciais

**Nunca insira chaves de API diretamente nos campos dos nós. Use o gerenciador de credenciais do N8N.**

### Credencial 1 — RunPod Header Auth

1. Acesse **Settings → Credentials → New Credential**
2. Selecione o tipo **Header Auth**
3. Preencha:
   - **Name:** `RunPod API`
   - **Name (header):** `Authorization`
   - **Value:** `Bearer <SUA_RUNPOD_API_KEY>`
4. Clique em **Save**

### Credencial 2 — Anthropic Header Auth

1. Acesse **Settings → Credentials → New Credential**
2. Selecione o tipo **Header Auth**
3. Preencha:
   - **Name:** `Anthropic API`
   - **Name (header):** `x-api-key`
   - **Value:** `<SUA_ANTHROPIC_API_KEY>`
4. Clique em **Save**

---

## Step 1: Webhook Trigger

**Tipo de nó:** `Webhook`

Este nó recebe a chamada do `processor.py` iniciando o processamento.

| Campo | Valor |
|---|---|
| HTTP Method | `POST` |
| Path | `opussb` |
| Authentication | None (acesso interno apenas) |
| Response Mode | `Respond with Last Node` (ou `When Last Node Finishes`) |

**URL resultante:** `http://localhost:5678/webhook/opussb`

**Payload esperado (enviado pelo processor.py):**
```json
{
  "job_id": "abc123",
  "audio_s3_url": "https://s3.amazonaws.com/bucket/audio.mp3",
  "min_duration": 30,
  "max_duration": 90
}
```

**Observação:** Configure o `processor.py` para enviar o POST para `http://localhost:5678/webhook/opussb`.

---

## Step 2: PATCH Status "transcribing"

**Tipo de nó:** `HTTP Request`

Notifica o Flask que o job entrou na fase de transcrição.

| Campo | Valor |
|---|---|
| Method | `PATCH` |
| URL | `http://127.0.0.1:5001/api/internal/jobs/{{ $json.job_id }}` |
| Authentication | None |
| Send Body | Habilitado |
| Body Content Type | `JSON` |

**Body (JSON):**
```json
{
  "status": "transcribing",
  "progress": 40
}
```

**Expressão N8N para a URL:**
```
http://127.0.0.1:5001/api/internal/jobs/{{ $json.job_id }}
```

---

## Step 3: Submeter Job no RunPod

**Tipo de nó:** `HTTP Request`

Submete o áudio para transcrição no endpoint Faster Whisper do RunPod.

| Campo | Valor |
|---|---|
| Method | `POST` |
| URL | `https://api.runpod.ai/v2/fczh1t4sx02j1s/run` |
| Authentication | **Predefined Credential Type** → selecionar `Header Auth` → `RunPod API` |
| Send Body | Habilitado |
| Body Content Type | `JSON` |

**Body (JSON):**
```json
{
  "input": {
    "audio": "{{ $('Webhook').item.json.body.audio_s3_url }}",
    "model": "turbo",
    "language": "pt",
    "transcription": "srt",
    "word_timestamps": true,
    "enable_vad": true
  }
}
```

**ARMADILHA: a chave é `audio`, NÃO `audio_url`.** A doc do worker diz `audio_url` e está
errada: com `audio_url` o job "completa" em ~150ms com output vazio e SEM erro (crédito
queimado, transcript vazio). Já aconteceu; confira a chave antes de qualquer mudança aqui.

**`enable_vad: true` (adicionado 2026-08-22):** liga o Silero VAD do faster-whisper, que
pula trechos sem fala em vez de "transcrever" créditos de legendagem alucinados. Origem:
incidente 2026-08-20 (arquivo com 133s de silêncio inicial fez o Whisper turbo alucinar
"Legenda Adriana Zanotto" em 263/263 segmentos do episódio inteiro). Validado por A/B em
2026-08-22 contra os dois áudios reais: no degenerado, o VAD recuperou 4.760 cues de fala
real; no saudável (Vitor Cunha), palavras -0,2% e cobertura de fala +4,4% (nenhuma perda).
Defaults do Silero: só remove silêncio maior que 2s, com 400ms de padding; pausa dramática
e respiração dentro de resposta não são tocadas.

**Resposta esperada do RunPod:**
```json
{
  "id": "<runpod_job_id>",
  "status": "IN_QUEUE"
}
```

### Sub-step: PATCH Flask com runpod_job_id

Após receber a resposta do RunPod, adicione um segundo **HTTP Request** node (PATCH) para registrar o `runpod_job_id` no Flask:

| Campo | Valor |
|---|---|
| Method | `PATCH` |
| URL | `http://127.0.0.1:5001/api/internal/jobs/{{ $('Webhook').item.json.job_id }}` |
| Body Content Type | `JSON` |

**Body:**
```json
{
  "runpod_job_id": "{{ $json.id }}"
}
```

---

## Step 4: Poll RunPod (Counter + Loop)

Esta etapa usa quatro nós encadeados para fazer polling do RunPod de forma segura, evitando loops infinitos.

### Nó 4a — Set (Inicializar Contador)

**Tipo de nó:** `Set`

Inicializa o contador de tentativas antes do loop.

| Campo | Valor |
|---|---|
| Name | `Init Counter` |
| Mode | `Manual Mapping` |

**Campos a definir:**
| Nome | Tipo | Valor |
|---|---|---|
| `counter` | `Number` | `0` |
| `runpod_job_id` | `String` | `{{ $('Submit RunPod Job').item.json.id }}` |

### Nó 4b — IF (Verificar Limite do Counter)

**Tipo de nó:** `IF`

Verifica se o número de tentativas ainda está dentro do limite (máximo 60 tentativas = 10 minutos).

| Campo | Valor |
|---|---|
| Condition | `Number` |
| Value 1 | `{{ $json.counter }}` |
| Operation | `smaller than` |
| Value 2 | `60` |

- **True** → continua para o Wait
- **False** → vai para o nó de erro (Step 5)

### Nó 4c — Wait

**Tipo de nó:** `Wait`

Aguarda 10 segundos entre cada tentativa de polling.

| Campo | Valor |
|---|---|
| Resume | `After Time Interval` |
| Wait Amount | `10` |
| Wait Unit | `Seconds` |

### Nó 4d — HTTP Request (Poll RunPod Status)

**Tipo de nó:** `HTTP Request`

Consulta o status atual do job no RunPod.

| Campo | Valor |
|---|---|
| Method | `GET` |
| URL | `https://api.runpod.ai/v2/fczh1t4sx02j1s/status/{{ $('Init Counter').item.json.runpod_job_id }}` |
| Authentication | `Header Auth` → `RunPod API` |

**Resposta possível do RunPod:**
```json
{
  "status": "IN_PROGRESS",
  "output": null
}
```
ou quando concluído:
```json
{
  "status": "COMPLETED",
  "output": {
    "transcription": "texto completo...",
    "segments": [...]
  }
}
```

### Nó 4e — IF (Verificar Status do RunPod)

**Tipo de nó:** `IF`

Verifica se o job terminou (com sucesso ou falha).

**Condição (usando OR):**

| Condição | Value 1 | Operation | Value 2 |
|---|---|---|---|
| OR | `{{ $json.status }}` | `equals` | `COMPLETED` |
| OR | `{{ $json.status }}` | `equals` | `FAILED` |

- **True** → vai para verificação de FAILED/COMPLETED (próximo IF)
- **False** → incrementa counter e volta ao Wait

### Nó 4f — Set (Incrementar Counter)

**Tipo de nó:** `Set`

Incrementa o contador e mantém o `runpod_job_id` para a próxima iteração.

| Nome | Tipo | Valor |
|---|---|---|
| `counter` | `Number` | `{{ $('Init Counter').item.json.counter + 1 }}` |
| `runpod_job_id` | `String` | `{{ $('Init Counter').item.json.runpod_job_id }}` |

**Conectar saída deste nó de volta ao Nó 4b (IF do counter).**

### Nó 4g — IF (COMPLETED ou FAILED?)

**Tipo de nó:** `IF`

Separa o fluxo entre sucesso e falha.

| Campo | Valor |
|---|---|
| Value 1 | `{{ $json.status }}` |
| Operation | `equals` |
| Value 2 | `COMPLETED` |

- **True** → continua para Step 6 (Claude API)
- **False** → vai para Step 5 (tratar falha)

---

## Step 5: Tratar Falha do RunPod

**Tipo de nó:** `HTTP Request`

Notifica o Flask sobre a falha e encerra o workflow.

| Campo | Valor |
|---|---|
| Method | `PATCH` |
| URL | `http://127.0.0.1:5001/api/internal/jobs/{{ $('Webhook').item.json.job_id }}` |
| Body Content Type | `JSON` |

**Body:**
```json
{
  "status": "error",
  "error": "RunPod transcription failed or timed out"
}
```

Após este nó, adicione um nó **Stop and Error** ou simplesmente deixe sem conexão de saída para encerrar o fluxo.

---

## Step 6: Chamar Claude API

**Tipo de nó:** `HTTP Request`

Envia a transcrição ao Claude para seleção dos melhores clipes virais.

| Campo | Valor |
|---|---|
| Method | `POST` |
| URL | `https://api.anthropic.com/v1/messages` |
| Authentication | `Header Auth` → `Anthropic API` |
| Send Headers | Habilitado |
| Send Body | Habilitado |
| Body Content Type | `JSON` |

**Headers adicionais** (além da credencial):

| Nome | Valor |
|---|---|
| `anthropic-version` | `2023-06-01` |
| `Content-Type` | `application/json` |

**Body (JSON):**
```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 2048,
  "messages": [
    {
      "role": "user",
      "content": "Você é um editor de vídeo especialista em conteúdo viral para redes sociais.\n\nAnalise a transcrição abaixo e selecione entre 5 e 10 momentos que seriam clipes virais.\n\nCritérios de seleção:\n- Duração entre {{ $('Webhook').item.json.min_duration }}s e {{ $('Webhook').item.json.max_duration }}s\n- Alto valor emocional ou informativo (insight forte, história, humor, polêmica)\n- Corte limpo: o clipe deve começar e terminar em pontos naturais da fala (nunca no meio de uma palavra ou frase)\n- Linguagem forte e direta\n- Início impactante (in medias res ou declaração forte)\n\nTranscrição:\n{{ $('Poll RunPod Status').item.json.output.transcription }}\n\nResponda APENAS com JSON válido no formato:\n[\n  {\n    \"start\": 10.5,\n    \"end\": 75.2,\n    \"title\": \"Título do clipe\",\n    \"score\": 8.5,\n    \"reason\": \"Por que é viral\"\n  }\n]"
    }
  ]
}
```

**Observação sobre expressões N8N no body JSON:** Como o body é JSON, as expressões `{{ }}` dentro de strings precisam estar no modo de expressão do N8N. Use o modo **Expression** (não Raw JSON) para o campo `content`, ou alterne para o modo **Raw** e habilite **Expression** por campo. Verifique que as aspas internas do JSON do prompt estão escapadas corretamente.

**Resposta esperada do Claude:**
```json
{
  "content": [
    {
      "type": "text",
      "text": "[{\"start\": 10.5, \"end\": 75.2, \"title\": \"...\", \"score\": 8.5, \"reason\": \"...\"}]"
    }
  ]
}
```

### Nó 6b — Set (Extrair Clipes)

**Tipo de nó:** `Set`

Extrai e parseia o JSON retornado pelo Claude.

| Nome | Tipo | Valor |
|---|---|---|
| `clips_json` | `String` | `{{ $json.content[0].text }}` |
| `transcript` | `String` | `{{ $('Poll RunPod Status').item.json.output.transcription }}` |

**Observação:** O campo `clips_json` ficará como string JSON. O Flask fará o parse no lado dele. Se preferir parsear no N8N, use um nó **Code** com:
```javascript
const text = $input.item.json.content[0].text;
const clips = JSON.parse(text);
return { clips_json: JSON.stringify(clips), transcript: $('Poll RunPod Status').item.json.output.transcription };
```

---

## Step 7: PATCH Status "cutting"

**Tipo de nó:** `HTTP Request`

Notifica o Flask que os clipes foram selecionados e o corte está em andamento.

| Campo | Valor |
|---|---|
| Method | `PATCH` |
| URL | `http://127.0.0.1:5001/api/internal/jobs/{{ $('Webhook').item.json.job_id }}` |
| Body Content Type | `JSON` |

**Body:**
```json
{
  "status": "cutting",
  "progress": 65
}
```

---

## Step 8: POST /api/callback

**Tipo de nó:** `HTTP Request`

Envia os resultados finais ao Flask para que o corte de vídeo seja iniciado.

| Campo | Valor |
|---|---|
| Method | `POST` |
| URL | `http://127.0.0.1:5001/api/callback` |
| Authentication | None |
| Body Content Type | `JSON` |

**Body:**
```json
{
  "job_id": "{{ $('Webhook').item.json.job_id }}",
  "transcript": "{{ $('Extrair Clipes').item.json.transcript }}",
  "clips_json": "{{ $('Extrair Clipes').item.json.clips_json }}"
}
```

**Observação:** Substitua `'Extrair Clipes'` pelo nome exato que você deu ao nó Set do Step 6b.

---

## Diagrama do Fluxo

```
[Webhook]
    ↓
[PATCH "transcribing" (40%)]
    ↓
[POST RunPod Submit]
    ↓
[PATCH Flask runpod_job_id]
    ↓
[Set counter=0]
    ↓
[IF counter < 60] ──FALSO──→ [PATCH "error"] → FIM
    ↓ VERDADEIRO
[Wait 10s]
    ↓
[GET RunPod Status]
    ↓
[IF COMPLETED ou FAILED] ──FALSO──→ [Set counter+1] ──→ (volta ao IF counter)
    ↓ VERDADEIRO
[IF COMPLETED] ──FALSO──→ [PATCH "error"] → FIM
    ↓ VERDADEIRO
[POST Claude API]
    ↓
[Set clips_json + transcript]
    ↓
[PATCH "cutting" (65%)]
    ↓
[POST /api/callback]
```

---

## Dicas e Solução de Problemas

### Testar o Webhook manualmente
```bash
curl -X POST http://localhost:5678/webhook/opussb \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-001", "audio_s3_url": "https://exemplo.com/audio.mp3", "min_duration": 30, "max_duration": 90}'
```

### Erros comuns

| Problema | Causa provável | Solução |
|---|---|---|
| Webhook não recebe dados | Modo de produção desabilitado | Ativar workflow (toggle no canto superior direito) |
| `401 Unauthorized` no RunPod | Credencial errada ou expirada | Verificar o token em Settings → Credentials |
| `401 Unauthorized` no Anthropic | Header `x-api-key` incorreto | Confirmar nome do header na credencial |
| Claude retorna texto fora do JSON | Modelo adicionou texto antes/depois | Usar nó Code com regex para extrair o JSON: `text.match(/\[[\s\S]*\]/)[0]` |
| Loop infinito no polling | Nó de incremento não conectado ao IF do counter | Verificar as conexões do nó Set (increment) → IF (counter < 60) |
| Flask retorna 404 no PATCH | `job_id` incorreto ou Flask não está rodando | Verificar se Flask está em `localhost:5001` e se o job existe |

### Variáveis de referência entre nós

No N8N, para referenciar dados de um nó anterior específico (não o imediatamente anterior), use:
```
{{ $('Nome Exato do Nó').item.json.campo }}
```

Use o nome do nó exatamente como aparece no canvas. Espaços e maiúsculas importam.

### Salvar e Ativar o Workflow

1. Clique em **Save** (ícone de disquete)
2. Ative o workflow com o toggle **Active** no canto superior direito
3. O webhook só funciona em modo ativo (não em modo de teste manual para chamadas externas)
