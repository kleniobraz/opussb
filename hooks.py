"""Scoring de gancho do podcast (BACKEND_CONTEXT §39, Parte 3).

Recebe a transcrição SRT do episódio inteiro, compacta pra blocos "[mm:ss] texto"
(chamada única, ~30k tokens pra 2h) e pede ao HOOK_MODEL as 3 melhores ABERTURAS de
1 a 3 minutos. Cada sugestão passa pelo critério duro da casa (autossuficiência,
fronteira de fala via snap_clip_bounds, duração 60-180s); o que não passa é DESCARTADO
(nunca clampado: encurtar um trecho quebraria a autossuficiência) e há no máximo UMA
retry corretiva, que é também o teto de custo por episódio (2 chamadas).

Roda numa thread disparada pelo /api/callback; o resultado fica em jobs.hooks_json e o
job estaciona em 'aguardando_aprovacao' (o dono escolhe/ajusta na tela de aprovação,
parte futura). Módulo deliberadamente leve: nada de processor/reframer (sem YOLO)."""
import json
import logging

import anthropic_client
import captions
import config
import db

log = logging.getLogger("opussb.hooks")

HOOK_MIN_SECONDS = 60.0
HOOK_MAX_SECONDS = 180.0
_MAX_CALLS = 2
_MAX_TOKENS = 4000

_SYSTEM = """Você é o editor-chefe de um podcast pra empresários (Carbone). Sua tarefa é escolher \
os 3 melhores trechos pra ABRIR o episódio no YouTube: um gancho que aquece o público antes \
da vinheta e do episódio completo.

O gancho é uma ABERTURA, não um trailer de fisgada máxima. Critérios, em ordem de importância:
1. AUTOSSUFICIENTE: o trecho começa e termina numa ideia inteira. Quem assiste entende tudo \
sem depender do que veio antes ou do que vem depois. Este é o critério mais importante.
2. Interessante por si só: um bom papo, uma história, uma opinião com personalidade, um \
assunto que valha a pena pra um empresário.
3. Duração entre 1 e 3 minutos (60 a 180 segundos). Regra dura: nada fora disso.
4. Começa e termina em fronteira de frase limpa, nunca no meio de uma palavra ou frase.

A transcrição vem em blocos "[mm:ss] texto" (minutos:segundos desde o início). Use esses \
tempos pra ancorar início e fim, em SEGUNDOS. Devolva os 3 MELHORES, do melhor pro pior, \
cada um com o texto do trecho e uma justificativa de UMA frase de por que é uma boa abertura."""

HOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        "hooks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "text": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "text", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["hooks"],
    "additionalProperties": False,
}


def _mmss(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m:02d}:{int(seconds % 60):02d}"


def compact_transcript(srt_text: str, block_seconds: float = 30.0) -> str:
    """SRT -> linhas "[mm:ss] texto" agregadas em blocos de ~30s (§39): preserva a âncora
    temporal que o modelo precisa e corta cerca de metade dos tokens do SRT cru."""
    lines = []
    bloco_ini = None
    parts = []
    for cue in captions.parse_srt(srt_text):
        if bloco_ini is None:
            bloco_ini = cue["start"]
        parts.append(cue["text"].strip())
        if cue["end"] - bloco_ini >= block_seconds:
            lines.append(f"[{_mmss(bloco_ini)}] " + " ".join(parts))
            bloco_ini, parts = None, []
    if parts:
        lines.append(f"[{_mmss(bloco_ini or 0.0)}] " + " ".join(parts))
    return "\n".join(lines)


def _texto_do_trecho(cues: list, start: float, end: float) -> str:
    """Texto do gancho derivado do PRÓPRIO SRT (o texto que o modelo devolve serve só de
    ancoragem; o que persiste vem da transcrição, imune a alucinação)."""
    return " ".join(c["text"].strip() for c in cues if c["end"] > start and c["start"] < end)


def _overlap(a0, a1, b0, b1) -> float:
    """Fração de sobreposição relativa ao MENOR intervalo (pega gancho contido em outro)."""
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(1e-9, min(a1 - a0, b1 - b0))


def _valida(brutos: list, srt_text: str, cues: list, dur_total: float, aceitos: list):
    """Aplica o critério duro a cada sugestão. Retorna (novos_validos, rejeicoes), onde
    rejeicoes é [(sugestao, motivo)] legível pra alimentar a retry corretiva."""
    validos, rejeicoes = [], []
    for h in brutos:
        if not isinstance(h, dict):
            rejeicoes.append((h, "formato inválido"))
            continue
        try:
            start, end = float(h["start"]), float(h["end"])
        except (KeyError, TypeError, ValueError):
            rejeicoes.append((h, "start/end ausentes ou não numéricos"))
            continue
        if not (0 <= start < end <= dur_total + 1.0):
            rejeicoes.append((h, f"fora da duração do episódio (0 a {dur_total:.0f}s)"))
            continue
        try:
            start, end = captions.snap_clip_bounds(srt_text, start, end)
        except (KeyError, TypeError, ValueError):
            pass
        dur = end - start
        if not (HOOK_MIN_SECONDS <= dur <= HOOK_MAX_SECONDS):
            rejeicoes.append((h, f"duração de {dur:.0f}s viola a regra dura de 60 a 180s"))
            continue
        ja_tem = aceitos + validos
        if any(_overlap(start, end, v["start"], v["end"]) > 0.5 for v in ja_tem):
            rejeicoes.append((h, "sobrepõe um gancho já aceito"))
            continue
        validos.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": _texto_do_trecho(cues, start, end),
            "reason": str(h.get("reason") or "").strip(),
        })
    return validos, rejeicoes


def suggest_hooks(srt_text: str):
    """Chamada única compacta + no máximo UMA retry corretiva (teto de custo). Retorna
    (hooks válidos em ordem de preferência do modelo, meta com modelo/usage/custo)."""
    cues = captions.parse_srt(srt_text)
    if not cues:
        raise RuntimeError("transcrição vazia ou ilegível (SRT sem cues)")
    dur_total = cues[-1]["end"]
    pedido = ("Transcrição do episódio em blocos [mm:ss]:\n\n" + compact_transcript(srt_text)
              + "\n\nDevolva os 3 melhores ganchos, com start e end em segundos.")

    validos = []
    correcao = None
    total_in = total_out = 0
    custo = 0.0
    model_usado = config.HOOK_MODEL
    for _ in range(_MAX_CALLS):
        content = [anthropic_client.text_block(pedido if correcao is None else pedido + "\n\n" + correcao)]
        resp = anthropic_client.call(config.HOOK_MODEL, _SYSTEM, content,
                                     max_tokens=_MAX_TOKENS, json_schema=HOOKS_SCHEMA)
        model_usado = resp.get("model") or config.HOOK_MODEL
        usage = resp.get("usage")
        total_in += getattr(usage, "input_tokens", 0) or 0
        total_out += getattr(usage, "output_tokens", 0) or 0
        custo += anthropic_client.price(model_usado, usage)

        data = resp.get("data") or {}
        brutos = data.get("hooks") if isinstance(data, dict) else None
        novos, rejeicoes = _valida(brutos or [], srt_text, cues, dur_total, validos)
        validos.extend(novos[: 3 - len(validos)])
        if len(validos) >= 3:
            break
        motivos = "; ".join(
            f"[{r[0].get('start') if isinstance(r[0], dict) else '?'}s-"
            f"{r[0].get('end') if isinstance(r[0], dict) else '?'}s]: {r[1]}"
            for r in rejeicoes[:5]
        ) or "nenhuma sugestão utilizável"
        correcao = (f"Da sua resposta anterior aproveitei {len(validos)} gancho(s). "
                    f"Sugestões rejeitadas e motivos: {motivos}. "
                    f"Sugira {3 - len(validos)} gancho(s) NOVO(s), em trechos DIFERENTES dos "
                    f"anteriores, respeitando a regra dura de 60 a 180 segundos.")

    meta = {"model": model_usado,
            "usage": {"input_tokens": total_in, "output_tokens": total_out},
            "cost_usd": round(custo, 6)}
    return validos[:3], meta


def run_podcast_hooks(job_id: str) -> None:
    """Thread pós-callback do podcast: transcript -> ganchos -> 'aguardando_aprovacao'.
    Falha vira status='error' (libera a vaga da fila; retry manual fica pra parte futura)."""
    job = db.get_job(job_id)
    if not job or job.get("job_type") != "podcast":
        return
    try:
        validos, meta = suggest_hooks(job.get("transcript") or "")
        if not validos:
            raise RuntimeError(
                "o modelo não devolveu nenhum gancho dentro do critério (60-180s, autossuficiente)")
        db.update_job(
            job_id,
            hooks_json=json.dumps({"hooks": validos, **meta}, ensure_ascii=False),
            status="aguardando_aprovacao",
            progress=85,
        )
        log.info("[hooks] job %s: %d gancho(s) via %s, custo US$%.4f",
                 job_id, len(validos), meta["model"], meta["cost_usd"])
    except Exception as e:
        log.warning("[hooks] job %s falhou: %s", job_id, e)
        db.update_job(job_id, status="error", error=f"análise de gancho falhou: {e}")
