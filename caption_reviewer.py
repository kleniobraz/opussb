"""caption_reviewer.py — pipeline do Revisor de legendas (roda em daemon thread).

Arquitetura: DETECÇÃO separada de LEITURA. A detecção (caption_sampler) roda local
na CPU, de graça, sobre uma varredura densa do vídeo: acha a faixa da legenda
dinamicamente, segmenta em blocos de frase e escolhe o estado FINAL de cada bloco
(a frase completa na tela). A leitura (Sonnet, cara) recebe só os frames escolhidos:
estados de legenda como recorte AMPLIADO da faixa, cortes de cena como quadro
inteiro. Teto de custo em US$ por revisão, corte por prioridade, descarte logado.

Passos: varredura (ffmpeg thumbs) -> detecção (caption_sampler) -> busca dos frames
escolhidos (ffmpeg) -> crop-e-amplia -> Sonnet 5 (visão) LÊ o texto queimado ->
Sonnet 5 (texto) REVISA contra o glossário + ortografia/gramática/pontuação/
maiúsculas -> grava relatório + tokens/custo na linha e limpa a mídia.

A mídia é efêmera: vídeo + frames vivem em JOB_DIR/<review_id>/ e são apagados no fim.

REFINO FUTURO (registrado, não construído — puxar SÓ se o recall medido pós-deploy
ainda deixar escapar erro real): (1) pré-tratamento da faixa da legenda estilo OCR
(contraste/binarização); (2) leitura cruzada do mesmo bloco em 2-3 frames com voto
de maioria. Decisão do dono em 2026-07-03: não construir refino antes de medir.
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
import caption_sampler
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
    "As imagens vêm em DOIS tipos, indicados no marcador de cada uma:\n"
    "- LEGENDA: recorte AMPLIADO da faixa onde a legenda da fala vive. Cada recorte é o "
    "estado final de uma frase (a frase completa na tela, antes de trocar). Estão em "
    "ordem cronológica.\n"
    "- CENA: o quadro inteiro (contexto, nomes, cargos, lower-thirds, marcas, slides).\n\n"
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
    "Acima estão {n} imagens de UM vídeo vertical, em ordem cronológica (recortes de "
    "LEGENDA e quadros de CENA, conforme os marcadores). Leia todo o texto queimado e "
    "devolva a legenda da fala reconstruída (legenda_fala) e a lista de textos gráficos "
    "(graficos), seguindo as regras do sistema. Não corrija nada — só leia."
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


# ─── Orquestração da amostragem (detecção pura em caption_sampler) ────────────

# Constantes do ESTIMADOR de custo (medidas em produção no e2e de 2026-07-03:
# read_in=2167 com 1 frame de 1229 tokens -> overhead ~940; review_in=1770 num vídeo
# curto). Margens folgadas de propósito: estimar pra cima nunca corta cobertura à toa
# além do necessário, estimar pra baixo furaria o teto.
_EST_OVERHEAD_IN = 1100     # system + instrução da chamada de leitura
_EST_MARKER_TOKENS = 22     # marcador "=== LEGENDA t=Xs ===" por imagem
_EST_READ_OUT = 700         # saída da leitura (medido 52-500)
_EST_REVIEW_IN = 3000       # entrada da revisão (glossário + texto lido)
_EST_REVIEW_OUT = 600       # saída da revisão


def _run_ffmpeg(args: list[str], what: str) -> None:
    r = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True,
                       timeout=config.CAPTION_REVIEW_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg ({what}) falhou: {r.stderr[-400:]}")


def _scan_thumbs(video_path: str, scan_dir: str) -> list[str]:
    """Passada 1: thumbnails em cinza a SCAN_FPS pra detecção local (barata)."""
    os.makedirs(scan_dir, exist_ok=True)
    vf = (f"fps={config.CAPTION_REVIEW_SCAN_FPS},"
          f"scale={config.CAPTION_REVIEW_SCAN_WIDTH}:-2,format=gray")
    _run_ffmpeg(["-i", video_path, "-vf", vf, "-q:v", "5",
                 os.path.join(scan_dir, "s_%05d.jpg")], "varredura")
    return sorted(os.path.join(scan_dir, f) for f in os.listdir(scan_dir) if f.endswith(".jpg"))


def _iter_gray(paths: list[str]):
    for p in paths:
        yield np.asarray(Image.open(p).convert("L"), dtype=np.uint8)


def _fetch_frame(video_path: str, t: float, dst: str) -> None:
    """Passada 2: busca UM frame em qualidade cheia no timestamp escolhido."""
    _run_ffmpeg(["-ss", f"{max(t, 0.0):.3f}", "-i", video_path,
                 "-frames:v", "1", "-q:v", "3", dst], "busca de frame")


def _crop_upscale(src: str, dst: str, y0f: float, y1f: float) -> None:
    """Recorte-e-amplia: corta a faixa da legenda do frame cheio e AMPLIA (texto
    grande lê melhor). LANCZOS, teto na borda longa."""
    im = Image.open(src)
    w, h = im.size
    band = im.crop((0, int(y0f * h), w, int(y1f * h)))
    bw, bh = band.size
    scale = min(config.CAPTION_REVIEW_CROP_UPSCALE,
                config.CAPTION_REVIEW_CROP_MAX_LONG_EDGE / max(bw, bh))
    if scale > 1:
        band = band.resize((round(bw * scale), round(bh * scale)), Image.LANCZOS)
    band.convert("RGB").save(dst, "JPEG", quality=90)


def _resize_for_send(src: str, dst: str, long_edge: int) -> None:
    im = Image.open(src)
    w, h = im.size
    scale = long_edge / max(w, h)
    if scale < 1:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    im.convert("RGB").save(dst, "JPEG", quality=88)


def prepare_frames(video_path: str, work_dir: str) -> list[dict]:
    """Varredura -> detecção -> busca -> crop-e-amplia -> orçamento.
    Retorna [{path, t, kind}] pronto pro envio ('caption' = recorte da faixa,
    'scene' = quadro inteiro)."""
    scan_dir = os.path.join(work_dir, "scan")
    send_dir = os.path.join(work_dir, "send")
    os.makedirs(send_dir, exist_ok=True)
    fps = config.CAPTION_REVIEW_SCAN_FPS

    thumbs = _scan_thumbs(video_path, scan_dir)
    if not thumbs:
        raise RuntimeError("nenhum frame extraído do vídeo")
    row_diffs, d_full, edges, n = caption_sampler.scan_profile(_iter_gray(thumbs))

    band = None
    if n >= 8:
        band = caption_sampler.detect_band(
            row_diffs, edges, d_full=d_full,
            scene_threshold=config.CAPTION_REVIEW_SCENE_THRESHOLD,
            score_floor=config.CAPTION_REVIEW_BAND_SCORE_FLOOR)

    if band is not None:
        y0f, y1f, score = band
        log.info("[caption_review] banda da legenda: y=%.0f%%-%.0f%% (score %.1f)",
                 y0f * 100, y1f * 100, score)
        # segunda leitura dos thumbs, só a fatia da banda (RAM pequena)
        first = np.asarray(Image.open(thumbs[0]).convert("L"))
        th = first.shape[0]
        band_stack = np.stack([a[int(y0f * th):max(int(y0f * th) + 2, int(y1f * th))]
                               for a in _iter_gray(thumbs)])
        states = caption_sampler.segment_states(
            band_stack, d_full,
            band_threshold=config.CAPTION_REVIEW_BAND_DIFF_THRESHOLD,
            scene_threshold=config.CAPTION_REVIEW_SCENE_THRESHOLD,
            min_scene_gap=max(1, int(0.5 * fps)))
    else:
        log.warning("[caption_review] banda da legenda NÃO detectada; modo degradado "
                    "(frames cheios + reforço uniforme)")
        states = caption_sampler.degrade_states(
            d_full, scene_threshold=config.CAPTION_REVIEW_SCENE_THRESHOLD,
            reinforce_every=max(1, int(config.CAPTION_REVIEW_DEGRADE_INTERVAL * fps)),
            min_gap=max(1, int(0.5 * fps)))

    if not states:  # vídeo estático sem texto: garante ao menos 1 quadro de contexto
        states = [{"index": n - 1, "kind": "scene", "words": None}]

    # Busca em qualidade cheia + materialização (crop ampliado ou quadro 1280)
    out: list[dict] = []
    for i, st in enumerate(states, 1):
        t = round(st["index"] / fps, 2)
        raw = os.path.join(send_dir, f"raw_{i:03d}.jpg")
        dst = os.path.join(send_dir, f"send_{i:03d}.jpg")
        _fetch_frame(video_path, t, raw)
        if st["kind"] == "caption":
            _crop_upscale(raw, dst, y0f, y1f)
        else:
            _resize_for_send(raw, dst, config.CAPTION_REVIEW_SEND_LONG_EDGE)
        os.remove(raw)
        with Image.open(dst) as im:
            w, h = im.size
        out.append({"path": dst, "t": t, "kind": st["kind"], "words": st["words"],
                    "_dims": (w, h), "_state": st})

    # Orçamento: teto em US$ + teto de imagens da API, corte por prioridade, logado.
    price_in, price_out = config.CLAUDE_PRICING.get(config.CAPTION_REVIEW_MODEL, (3.0, 15.0))
    base = caption_sampler.estimate_cost(
        [], overhead_in=_EST_OVERHEAD_IN, marker_tokens=_EST_MARKER_TOKENS,
        read_out=_EST_READ_OUT, review_in=_EST_REVIEW_IN, review_out=_EST_REVIEW_OUT,
        price_in=price_in, price_out=price_out)
    img_costs = [(caption_sampler.image_tokens(*o["_dims"]) + _EST_MARKER_TOKENS)
                 * price_in / 1e6 for o in out]
    kept, dropped = caption_sampler.apply_budget(
        [o["_state"] for o in out], img_costs, base_cost=base,
        max_cost=config.CAPTION_REVIEW_MAX_COST_USD,
        max_images=config.CAPTION_REVIEW_MAX_IMAGES)
    if dropped:
        log.warning("[caption_review] orçamento (US$ %.2f / %d imgs): descartados %d de %d "
                    "estados: %s", config.CAPTION_REVIEW_MAX_COST_USD,
                    config.CAPTION_REVIEW_MAX_IMAGES, len(dropped), len(out),
                    ", ".join(f"{out[i]['kind']}@{out[i]['t']}s" for i in dropped))
        for i in dropped:
            os.remove(out[i]["path"])
    est = base + sum(img_costs[i] for i in kept)
    log.info("[caption_review] %d imagens selecionadas (%d legenda, %d cena), custo "
             "estimado US$ %.4f", len(kept),
             sum(1 for i in kept if out[i]["kind"] == "caption"),
             sum(1 for i in kept if out[i]["kind"] == "scene"), est)
    return [{k: v for k, v in out[i].items() if not k.startswith("_")} for i in kept]


# ─── Chamadas ao Claude ───────────────────────────────────────────────────────

def read_frames(frames: list[dict]) -> dict:
    """READ call (Sonnet visão): lê o texto queimado e reconstrói a legenda."""
    content = []
    for i, fr in enumerate(frames, 1):
        kind = "LEGENDA" if fr.get("kind") == "caption" else "CENA"
        content.append(anthropic_client.text_block(f"=== {kind} {i} (t={fr['t']}s) ==="))
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
            log.info("[caption_review %s] %d imagens após amostragem por mudança de legenda",
                     review_id, len(frames))

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
