# Prompt de scoring de clipes (nó "Build Claude Body" do N8N)

Extraído do workflow "OpusSB — Transcrição e Seleção de Clipes" em 2026-08-09.
Fonte: banco SQLite do n8n (volume `n8n_data`), workflow id `UGtxQOBzHsfI9Bpg`,
versionId `d9c3e626-6a4e-49ea-a4d9-04a70a5ac0a4`, updatedAt 2026-07-27.

Modelo: `claude-haiku-4-5` | max_tokens: `2048` | 1 mensagem user, sem system prompt.

As variáveis `${minDuration}`, `${maxDuration}` e `${segmentsText}` são interpoladas
pelo nó em tempo de execução: min/max vêm do body do webhook (enviados pelo Flask),
e `segmentsText` é a transcrição do Whisper formatada como
`[INICIOs - FIMs] texto` (uma linha por segmento).

## Prompt (verbatim)

```
Você é um editor de vídeo especialista em cortes virais para Instagram e TikTok. Seu trabalho é selecionar trechos de podcast que funcionem como clipes independentes e completos.

REGRAS INEGOCIÁVEIS:
- Cada clipe deve ter duração entre ${minDuration}s e ${maxDuration}s
- O clipe DEVE começar no início de uma fala completa (nunca no meio de uma frase)
- O clipe DEVE terminar no final de uma ideia completa (nunca cortar no meio de uma resposta)
- Se uma pergunta é feita, inclua a resposta COMPLETA até o ponto final natural
- Nunca termine um clipe com o entrevistado ainda falando
- Ignore trechos de apresentação (nome, empresa) e despedidas
- Corrija erros óbvios de transcrição (ex: nomes próprios errados)

CRITÉRIOS DE SELEÇÃO (em ordem de prioridade):
1. Insight acionável e direto — algo que o espectador pode aplicar imediatamente
2. Declaração polêmica ou provocativa — gera debate e compartilhamento
3. História ou exemplo concreto com começo, meio e fim
4. Pergunta + resposta completa com conclusão clara

TRANSCRIÇÃO COM TIMESTAMPS:
${segmentsText}

Retorne APENAS um JSON válido, sem markdown, sem texto antes ou depois:
[
  {
    "start": 10.02,
    "end": 55.76,
    "title": "Título curto e impactante",
    "score": 9.2,
    "reason": "Por que é viral e por que os cortes estão no lugar certo"
  }
]
```

## Código completo do nó (verbatim)

```javascript
const runpodItem = $('GET RunPod Status').first();
const segments = runpodItem.json.output.segments;
const minDuration = $('Webhook').first().json.body.min_duration;
const maxDuration = $('Webhook').first().json.body.max_duration;
const jobId = $('Webhook').first().json.body.job_id;

const segmentsText = segments.map(s => 
  `[${s.start.toFixed(2)}s - ${s.end.toFixed(2)}s] ${s.text.trim()}`
).join('\n');

const prompt = `Você é um editor de vídeo especialista em cortes virais para Instagram e TikTok. Seu trabalho é selecionar trechos de podcast que funcionem como clipes independentes e completos.

REGRAS INEGOCIÁVEIS:
- Cada clipe deve ter duração entre ${minDuration}s e ${maxDuration}s
- O clipe DEVE começar no início de uma fala completa (nunca no meio de uma frase)
- O clipe DEVE terminar no final de uma ideia completa (nunca cortar no meio de uma resposta)
- Se uma pergunta é feita, inclua a resposta COMPLETA até o ponto final natural
- Nunca termine um clipe com o entrevistado ainda falando
- Ignore trechos de apresentação (nome, empresa) e despedidas
- Corrija erros óbvios de transcrição (ex: nomes próprios errados)

CRITÉRIOS DE SELEÇÃO (em ordem de prioridade):
1. Insight acionável e direto — algo que o espectador pode aplicar imediatamente
2. Declaração polêmica ou provocativa — gera debate e compartilhamento
3. História ou exemplo concreto com começo, meio e fim
4. Pergunta + resposta completa com conclusão clara

TRANSCRIÇÃO COM TIMESTAMPS:
${segmentsText}

Retorne APENAS um JSON válido, sem markdown, sem texto antes ou depois:
[
  {
    "start": 10.02,
    "end": 55.76,
    "title": "Título curto e impactante",
    "score": 9.2,
    "reason": "Por que é viral e por que os cortes estão no lugar certo"
  }
]`;

return [{
  json: {
    claudeBody: JSON.stringify({
      model: 'claude-haiku-4-5',
      max_tokens: 2048,
      messages: [{ role: 'user', content: prompt }]
    }),
    transcript: $('GET RunPod Status').first().json.output.transcription,
    job_id: jobId
  }
}];
```
