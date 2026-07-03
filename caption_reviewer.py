"""caption_reviewer.py — pipeline do Revisor de legendas (roda em daemon thread).

Passos: extrai frames (ffmpeg) -> deduplica por diferença visual (método validado no
teste de viabilidade) -> Sonnet 5 (visão) LÊ o texto queimado e reconstrói a legenda
animada palavra-a-palavra -> Sonnet 5 (texto) REVISA contra o glossário + ortografia/
gramática/pontuação/maiúsculas -> grava relatório + tokens/custo na linha e limpa a mídia.

A mídia é efêmera: vídeo + frames vivem em JOB_DIR/<review_id>/ e são apagados no fim.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading

import numpy as np
from PIL import Image

import anthropic_client
import config
import db

log = logging.getLogger("opussb.caption_reviewer")

# Lane PRÓPRIA de concorrência (independente de MAX_CONCURRENT_JOBS): uma revisão nunca
# trava a geração de clipes e vice-versa. Definida aqui pra evitar import circular com app.
_sem = threading.BoundedSemaphore(config.CAPTION_REVIEW_CONCURRENCY)

_READ_MAX_TOKENS = 6000
_REVIEW_MAX_TOKENS = 4000

# ─── Prompts ─────────────────────────────────────────────────────────────────

_READ_SYSTEM = (
    "Você é um OCR fiel. Transcreva, palavra por palavra, TODO o texto QUEIMADO que "
    "aparece nos frames de vídeo, EM ORDEM. NÃO revise, NÃO corrija ortografia, NÃO "
    "complete, NÃO interprete — preserve a grafia, as maiúsculas/minúsculas e os acentos "
    "EXATAMENTE como estão na tela (se a tela escreve errado, transcreva errado).\n\n"
    "A legenda da FALA costuma ser ANIMADA palavra por palavra (as palavras surgem, "
    "crescem ou são destacadas; às vezes há uma 'sombra'/prévia menor da mesma palavra). "
    "Frames consecutivos se sobrepõem. Reconstrua a fala como um texto CONTÍNUO, sem "
    "duplicar os fragmentos que se sobrepõem entre frames.\n\n"
    "Separe o que você lê em duas coisas:\n"
    "- legenda_fala: a legenda da fala reconstruída (texto corrido).\n"
    "- graficos: qualquer OUTRO texto na tela — nomes, cargos/títulos, lower-thirds, "
    "marcas/logos com texto, texto de slide/tela ao fundo. Deduplique repetições entre "
    "frames. Para cada um, 'texto' é o que está escrito e 'tipo' é uma etiqueta curta "
    "(ex.: nome, cargo, marca, slide, lower-third).\n\n"
    "Se um texto estiver cortado/parcial, transcreva o que dá pra ler e marque o corte "
    "com '…' — NUNCA invente o resto."
)

_READ_INSTR = (
    "Acima estão {n} frames de UM vídeo vertical, em ordem cronológica. Leia todo o texto "
    "queimado e devolva a legenda da fala reconstruída (legenda_fala) e a lista de textos "
    "gráficos (graficos), seguindo as regras do sistema. Não corrija nada — só leia."
)

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "legenda_fala": {"type": "string"},
        "graficos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"texto": {"type": "string"}, "tipo": {"type": "string"}},
                "required": ["texto", "tipo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["legenda_fala", "graficos"],
    "additionalProperties": False,
}

_REVIEW_SYSTEM = (
    "Você revisa legendas e textos queimados de vídeos sociais em português do Brasil. "
    "Você recebe (1) a legenda da fala lida do vídeo, (2) a lista de textos gráficos e "
    "(3) um GLOSSÁRIO com os nomes CANÔNICOS de marcas, produtos e pessoas do cliente.\n\n"
    "Aponte APENAS erros de verdade. Não reescreva o texto, não sugira melhorias de estilo "
    "e não reporte nada que seja apenas diferente do glossário sem estar errado.\n\n"
    "LEGENDA DA FALA (origem 'legenda'): foi lida quadro a quadro de uma legenda ANIMADA "
    "palavra a palavra, então quase sempre vem picotada, com palavras repetidas ou frases "
    "incompletas. Isso é ARTEFATO DE CAPTURA, não é erro da tela. NUNCA reporte duplicação de "
    "palavra, fragmento, corte, ordem estranha ou frase incompleta na fala. Só gere finding de "
    "fala se houver um erro claro de ortografia ou acentuação numa palavra bem legível, e ainda "
    "assim só com confianca alta (0.8 ou mais). Na dúvida, não reporte.\n\n"
    "MARCAS, PRODUTOS E PESSOAS (glossário): o glossário traz o nome CANÔNICO. Só é erro quando "
    "a GRAFIA ou a ACENTUAÇÃO na tela diverge do canônico (ex.: 'SeuBonê' quando o certo é "
    "'SeuBoné'; 'Onevo' sem acento se o canônico tiver; 'Wevo' quando o certo é 'Weevo'). O que "
    "NÃO é erro e você NÃO deve reportar:\n"
    "- Forma curta ou nome guarda-chuva (ex.: 'ONEVO' com glossário 'ONEVO Energia'; 'Carbone' "
    "com 'Carbone Educação'). Forma curta está correta.\n"
    "- Diferença só de caixa (ex.: 'CLUB CLASS', 'CARBONE EDUCAÇÃO' em caixa alta). Caixa alta "
    "em título, logo ou arte é estilo, não erro.\n"
    "Regra de ouro para marca: mesma grafia e mesmos acentos = correto, mesmo curto ou em caixa alta.\n\n"
    "Erros reais de ortografia ou acentuação em texto gráfico bem legível (inclusive nomes "
    "próprios que não estão no glossário) devem ser reportados normalmente como 'ortografia'.\n\n"
    "Para cada achado devolva: trecho (o pedaço EXATO como foi lido), origem "
    "('legenda' ou 'grafico'), tipo ('ortografia'|'gramatica'|'marca'|'pontuacao'|'maiuscula'|"
    "'outro'), sugestao (o texto corrigido), explicacao (curtíssima) e confianca (0 a 1). "
    "Se não houver erros, devolva findings vazio."
)

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trecho": {"type": "string"},
                    "origem": {"type": "string", "enum": ["legenda", "grafico"]},
                    "tipo": {"type": "string", "enum": [
                        "ortografia", "gramatica", "marca", "pontuacao", "maiuscula", "outro"]},
                    "sugestao": {"type": "string"},
                    "explicacao": {"type": "string"},
                    "confianca": {"type": "number"},
                },
                "required": ["trecho", "origem", "tipo", "sugestao", "explicacao", "confianca"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


# ─── Extração + dedup de frames (método validado no teste de viabilidade) ─────

def extract_frames(video_path: str, frames_dir: str) -> list[str]:
    """Extrai frames a CAPTION_REVIEW_FPS. Retorna os caminhos ordenados."""
    os.makedirs(frames_dir, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps={config.CAPTION_REVIEW_FPS}",
         "-q:v", "3", os.path.join(frames_dir, "f_%05d.jpg")],
        capture_output=True, text=True, timeout=config.CAPTION_REVIEW_TIMEOUT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg (frames) falhou: {r.stderr[-400:]}")
    return sorted(os.path.join(frames_dir, f) for f in os.listdir(frames_dir) if f.endswith(".jpg"))


def _signatures(path: str):
    """Assinaturas cinza reduzidas: frame cheio + faixa da legenda (y 55%-92%)."""
    im = Image.open(path).convert("L")
    w, h = im.size
    full = np.asarray(im.resize((36, 64)), dtype=np.float32)
    band = im.crop((0, int(h * 0.55), w, int(h * 0.92))).resize((72, 28))
    return full, np.asarray(band, dtype=np.float32)


def dedup_frames(paths: list[str], threshold: float | None = None) -> list[int]:
    """Índices dos frames a MANTER: guarda um frame quando ele difere do último
    mantido acima do threshold, olhando frame cheio E faixa da legenda (pega tanto
    troca de cena quanto mudança só na legenda). Determinístico."""
    if threshold is None:
        threshold = config.CAPTION_REVIEW_DEDUP_THRESHOLD
    kept: list[int] = []
    last = None
    for i, p in enumerate(paths):
        fu, ba = _signatures(p)
        if last is None:
            diff = 1.0
        else:
            d_full = float(np.abs(fu - last[0]).mean()) / 255.0
            d_band = float(np.abs(ba - last[1]).mean()) / 255.0
            diff = max(d_full, d_band)
        if diff > threshold:
            kept.append(i)
            last = (fu, ba)
    return kept


def _cap_frames(indices: list[int], max_frames: int) -> list[int]:
    """Reamostra uniformemente se passar do teto, LOGANDO o descarte (nunca em silêncio)."""
    if len(indices) <= max_frames:
        return indices
    step = len(indices) / max_frames
    picked = [indices[int(i * step)] for i in range(max_frames)]
    log.warning("[caption_review] %d frames deduplicados > teto %d; reamostrado p/ %d (descartados %d)",
                len(indices), max_frames, len(picked), len(indices) - len(picked))
    return picked


def _resize_for_send(src: str, dst: str, long_edge: int) -> None:
    im = Image.open(src)
    w, h = im.size
    scale = long_edge / max(w, h)
    if scale < 1:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    im.convert("RGB").save(dst, "JPEG", quality=88)


def prepare_frames(video_path: str, work_dir: str) -> list[dict]:
    """Extrai + deduplica + redimensiona. Retorna [{path, t}] pronto pro envio."""
    frames_dir = os.path.join(work_dir, "frames")
    send_dir = os.path.join(work_dir, "send")
    os.makedirs(send_dir, exist_ok=True)
    raw = extract_frames(video_path, frames_dir)
    if not raw:
        raise RuntimeError("nenhum frame extraído do vídeo")
    kept = _cap_frames(dedup_frames(raw), config.CAPTION_REVIEW_MAX_FRAMES)
    out = []
    for idx, i in enumerate(kept, 1):
        dst = os.path.join(send_dir, f"send_{idx:03d}.jpg")
        _resize_for_send(raw[i], dst, config.CAPTION_REVIEW_SEND_LONG_EDGE)
        out.append({"path": dst, "t": round(i / config.CAPTION_REVIEW_FPS, 2)})
    return out


# ─── Chamadas ao Claude ───────────────────────────────────────────────────────

def read_frames(frames: list[dict]) -> dict:
    """READ call (Sonnet visão): lê o texto queimado e reconstrói a legenda."""
    content = []
    for i, fr in enumerate(frames, 1):
        content.append(anthropic_client.text_block(f"=== FRAME {i} (t={fr['t']}s) ==="))
        content.append(anthropic_client.image_block(fr["path"]))
    content.append(anthropic_client.text_block(_READ_INSTR.format(n=len(frames))))
    return anthropic_client.call(
        config.CAPTION_REVIEW_MODEL, _READ_SYSTEM, content,
        max_tokens=_READ_MAX_TOKENS, thinking=config.CAPTION_REVIEW_THINKING,
        json_schema=_READ_SCHEMA,
    )


def _glossary_text() -> str:
    terms = db.list_glossary()
    if not terms:
        return "(glossário vazio)"
    lines = []
    for t in terms:
        line = f"- {t['term']}"
        if t.get("category"):
            line += f" ({t['category']})"
        if t.get("notes"):
            line += f" — {t['notes']}"
        lines.append(line)
    return "\n".join(lines)


def review_text(legenda_fala: str, graficos: list[dict]) -> dict:
    """REVIEW call (Sonnet texto): revisa a leitura contra o glossário + regras."""
    graf = "\n".join(f"- {g.get('texto', '')}" for g in graficos) or "(nenhum)"
    payload = (
        "GLOSSÁRIO (nomes canônicos — divergências são erro):\n" + _glossary_text() + "\n\n"
        "LEGENDA DA FALA (lida do vídeo):\n" + (legenda_fala or "(vazio)") + "\n\n"
        "TEXTOS GRÁFICOS (lidos do vídeo):\n" + graf + "\n\n"
        "Revise e devolva os achados (findings) seguindo as regras do sistema."
    )
    return anthropic_client.call(
        config.CAPTION_REVIEW_MODEL, _REVIEW_SYSTEM,
        [anthropic_client.text_block(payload)],
        max_tokens=_REVIEW_MAX_TOKENS, thinking=config.CAPTION_REVIEW_THINKING,
        json_schema=_REVIEW_SCHEMA,
    )


# ─── Orquestrador (daemon thread) ─────────────────────────────────────────────

def run_review(review_id: str, video_path: str) -> None:
    """Pipeline completo de uma revisão. Atualiza a linha e limpa a mídia no fim."""
    work_dir = os.path.dirname(video_path)
    try:
        with _sem:
            db.update_caption_review(review_id, status="extracting", progress=15)
            frames = prepare_frames(video_path, work_dir)
            log.info("[caption_review %s] %d frames após dedup", review_id, len(frames))

            db.update_caption_review(review_id, status="reading", progress=40,
                                     frames_count=len(frames))
            read = read_frames(frames)
            data = read["data"] or {}
            legenda = (data.get("legenda_fala") or "").strip()
            graficos = data.get("graficos") or []

            db.update_caption_review(review_id, status="reviewing", progress=80)
            review = review_text(legenda, graficos)
            findings = (review["data"] or {}).get("findings") or []

            cost = (anthropic_client.price(read["model"], read["usage"])
                    + anthropic_client.price(review["model"], review["usage"]))
            db.update_caption_review(
                review_id, status="done", progress=100,
                legenda_fala=legenda,
                graficos_json=json.dumps(graficos, ensure_ascii=False),
                report_json=json.dumps(findings, ensure_ascii=False),
                read_in_tok=read["usage"].input_tokens, read_out_tok=read["usage"].output_tokens,
                review_in_tok=review["usage"].input_tokens, review_out_tok=review["usage"].output_tokens,
                cost_usd=round(cost, 5),
            )
            log.info("[caption_review %s] concluída: %d achados, %d frames, $%.5f",
                     review_id, len(findings), len(frames), cost)
    except Exception as e:
        log.exception("[caption_review %s] falhou", review_id)
        try:
            db.update_caption_review(review_id, status="error",
                                     error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
