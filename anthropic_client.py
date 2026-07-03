"""anthropic_client.py — cliente Claude único e reutilizável (Messages API).

Greenfield: até aqui o Claude só era chamado via N8N; este é o primeiro cliente
Python no processo. Genérico de propósito pra o revisor de legendas E o futuro
pipeline de podcast reusarem — call() / text_block() / image_block() / price().
"""
from __future__ import annotations

import base64
import json
import logging
import os

import anthropic

import config

log = logging.getLogger("opussb.anthropic")

# Cliente único, montado no import. ANTHROPIC_API_KEY é obrigatória em config
# (os.environ[...]), então o app já não sobe sem ela. O SDK re-tenta 429/5xx com
# backoff exponencial (max_retries=2 default), no espírito do retry manual do processor.
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

_MEDIA_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(path: str, media_type: str | None = None) -> dict:
    """Bloco de imagem base64 a partir de um arquivo local."""
    if media_type is None:
        media_type = _MEDIA_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _extract_json(text: str):
    """Parse tolerante: tenta json.loads direto; senão pega o 1º objeto/array
    balanceado no texto (espelha a tolerância de processor._parse_clips_json)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
    return None


def call(model, system, content, *, max_tokens, thinking="disabled", json_schema=None):
    """Uma chamada messages.create. `content` é a lista de blocos (text/image) do
    turno do usuário. Com json_schema, pede saída estruturada E parseia (com fallback
    tolerante se o modelo/SDK não honrar o schema). Retorna {text, data, usage, model}."""
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    if thinking:
        kwargs["thinking"] = {"type": thinking}
    if json_schema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
    try:
        resp = _client.messages.create(**kwargs)
    except (anthropic.BadRequestError, TypeError) as e:
        # Se o output_config for rejeitado (SDK antigo/modelo sem structured outputs),
        # refaz SEM ele — o prompt já pede JSON e o parse tolerante recupera. Nunca deixa
        # a feature cair por causa do structured output.
        if json_schema is not None and "output_config" in str(e).lower():
            log.warning("[anthropic] output_config rejeitado, refazendo sem schema: %s", e)
            kwargs.pop("output_config", None)
            resp = _client.messages.create(**kwargs)
        else:
            raise
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    data = _extract_json(text) if json_schema is not None else None
    return {"text": text, "data": data, "usage": resp.usage, "model": resp.model}


def price(model: str, usage) -> float:
    """Custo em USD de uma usage pela tabela config.CLAUDE_PRICING (input, output).

    Tolera id datado/resolvido pela API (ex.: se 'claude-sonnet-5' voltar como
    'claude-sonnet-5-20260101'): casa pelo prefixo do alias. Sem isso, um id fora
    da tabela cairia no default (0, 0) e o custo seria contado como zero em
    silêncio, justamente a informação que a revisão existe pra registrar.
    """
    pricing = config.CLAUDE_PRICING
    pair = pricing.get(model)
    if pair is None:
        # Casa o alias mais longo que prefixa o id retornado (as chaves não se
        # sobrepõem hoje; o max protege caso passem a se sobrepor).
        cands = [(k, v) for k, v in pricing.items() if model.startswith(k)]
        pair = max(cands, key=lambda kv: len(kv[0]))[1] if cands else None
    if pair is None:
        log.warning("[anthropic] sem preço p/ modelo %r; custo contado como 0", model)
        pair = (0.0, 0.0)
    pin, pout = pair
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (inp * pin + out * pout) / 1_000_000
