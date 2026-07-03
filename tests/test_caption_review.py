"""Testes do revisor de legendas: CRUD do glossário + caption_reviews e o ORQUESTRADOR
da amostragem (prepare_frames/read_frames) com vídeo sintético real e API mockada.
Não faz chamadas de rede (o cliente Anthropic é só construído)."""
import os
import shutil
import sqlite3
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFont

import anthropic_client
import db
import caption_reviewer


# ─── Glossário ────────────────────────────────────────────────────────────────

def test_glossary_crud():
    db.init_glossary()
    db.add_glossary("t1", "SeuBoné", "marca", "acento agudo", created_by="a@b.com")
    terms = db.list_glossary()
    assert [t["term"] for t in terms] == ["SeuBoné"]
    assert terms[0]["category"] == "marca" and terms[0]["created_by"] == "a@b.com"

    # upsert por id (edição) preserva a linha
    db.add_glossary("t1", "SeuBoné Personalizados", "produto", None)
    assert db.get_glossary("t1")["term"] == "SeuBoné Personalizados"
    assert len(db.list_glossary()) == 1

    db.delete_glossary("t1")
    assert db.get_glossary("t1") is None
    assert db.list_glossary() == []


def test_glossary_term_unique_case_insensitive():
    db.init_glossary()
    db.add_glossary("t1", "Weevo", "marca")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_glossary("t2", "weevo", "marca")  # mesmo termo, id diferente -> viola o índice


def test_ensure_glossary_is_idempotent():
    db.init_glossary()
    db.ensure_glossary("weevo", "Weevo", "marca")
    db.ensure_glossary("weevo", "OUTRO", "produto")  # não sobrescreve
    assert db.get_glossary("weevo")["term"] == "Weevo"


# ─── caption_reviews ────────────────────────────────────────────────────────────

def test_caption_review_lifecycle():
    db.init_caption_reviews()
    db.create_caption_review("r1", "reels.mp4", "a@b.com", duration=39.0)
    row = db.get_caption_review("r1")
    assert row["status"] == "queued" and row["progress"] == 5
    assert db.count_active_caption_reviews() == 1

    db.update_caption_review("r1", status="done", progress=100, cost_usd=0.1,
                             report_json="[]", frames_count=30)
    row = db.get_caption_review("r1")
    assert row["status"] == "done" and row["cost_usd"] == 0.1 and row["frames_count"] == 30
    assert db.count_active_caption_reviews() == 0
    assert [r["id"] for r in db.list_caption_reviews("a@b.com")] == ["r1"]
    # outro usuário não vê
    assert db.list_caption_reviews("outro@b.com") == []


def test_update_caption_review_ignores_unknown_columns():
    db.init_caption_reviews()
    db.create_caption_review("r2", "x.mp4", "a@b.com")
    # coluna fora da whitelist é ignorada (não injeta no SQL, não quebra)
    db.update_caption_review("r2", status="reading", nao_existe="boom")
    assert db.get_caption_review("r2")["status"] == "reading"


# ─── Orquestrador da amostragem (vídeo sintético real, sem rede) ────────────────

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_has_ffmpeg = shutil.which("ffmpeg") is not None
needs_media = pytest.mark.skipif(not (_has_ffmpeg and os.path.exists(FONT_PATH)),
                                 reason="ffmpeg ou fonte DejaVu ausentes")

VW, VH, VFPS = 360, 640, 12


def _encode(frames_dir, out_path, fps=VFPS):
    subprocess.run(
        ["ffmpeg", "-y", "-framerate", str(fps), "-i",
         os.path.join(frames_dir, "f_%04d.png"), "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", out_path],
        check=True, capture_output=True, timeout=120,
    )


def _make_video(tmp_path, spec_fn, duration, fps=VFPS):
    """spec_fn(t) -> (texto, y_frac, bg). Gera PNGs com PIL e codifica com ffmpeg."""
    fdir = tmp_path / "vframes"
    fdir.mkdir()
    font = ImageFont.truetype(FONT_PATH, 26)
    for i in range(int(duration * fps)):
        text, y_frac, bg = spec_fn(i / fps)
        im = Image.new("RGB", (VW, VH), (bg, bg, bg))
        d = ImageDraw.Draw(im)
        y = int(VH * y_frac)
        for line in text.split("\n"):
            tw = d.textlength(line, font=font)
            d.text(((VW - tw) / 2, y), line, font=font, fill=(255, 255, 255))
            y += 30
        im.save(fdir / f"f_{i:04d}.png")
    out = str(tmp_path / "video.mp4")
    _encode(str(fdir), out, fps=fps)
    return out


@needs_media
def test_prepare_frames_video_com_legenda_e_corte(tmp_path):
    """Frase acumulativa com cauda curta + corte de cena: o estado final da frase tem
    que virar crop AMPLIADO e o corte tem que virar quadro de cena."""
    words = ["estamos", "formando", "o", "grupo", "sb", "inteligencia"]

    def spec(t):
        if t < 3.3:  # palavras entram a cada 0.5s; última vive 0.5+0.3=~0.3s extra
            n = min(len(words), int(t / 0.5) + 1)
            txt = " ".join(words[:n])
            if len(txt) > 20:
                txt = txt[:txt.rfind(" ", 0, 20)] + "\n" + txt[txt.rfind(" ", 0, 20) + 1:]
            return txt, 0.70, 20
        if t < 4.0:
            return "", 0.70, 20
        return "NOVA CENA", 0.70, 200  # corte: fundo escuro -> claro

    video = _make_video(tmp_path, spec, duration=5.5)
    frames = caption_reviewer.prepare_frames(video, str(tmp_path))

    kinds = {f["kind"] for f in frames}
    assert "caption" in kinds, f"nenhum crop de legenda: {frames}"
    assert "scene" in kinds, f"corte de cena não virou quadro de cena: {frames}"
    for f in frames:
        assert os.path.exists(f["path"])
    # o estado final da frase (com a última palavra) foi capturado
    janela = [f for f in frames if f["kind"] == "caption" and 2.4 <= f["t"] <= 3.4]
    assert janela, f"estado final da frase (t~2.5-3.3) não capturado: {frames}"
    # crop veio AMPLIADO (2x a largura do vídeo, dentro do teto)
    with Image.open(janela[0]["path"]) as im:
        assert im.size[0] == VW * 2, f"crop não ampliado: {im.size}"
        assert im.size[0] < im.size[1] * 8, "crop degenerado (faixa fina demais)"


@needs_media
def test_prepare_frames_sem_legenda_degrada_pra_quadro_cheio(tmp_path, caplog):
    def spec(t):
        return "", 0.5, int(60 + 50 * ((t * 7) % 2))  # fundo pulsando, sem texto

    video = _make_video(tmp_path, spec, duration=5.0)
    import logging
    with caplog.at_level(logging.WARNING, logger="opussb.caption_reviewer"):
        frames = caption_reviewer.prepare_frames(video, str(tmp_path))
    assert frames and all(f["kind"] == "scene" for f in frames)
    assert any("degradado" in r.message for r in caplog.records), \
        "modo degradado tem que ser LOGADO"


@needs_media
def test_prepare_frames_fonte_fps_baixo_nao_quebra(tmp_path):
    """Regressão do -ss além do fim: fonte a 2fps faz a varredura de 8fps estender o
    último frame além do último pts, e a busca full-res tem que RECUAR no tempo em
    vez de morrer com FileNotFoundError (medido no ffmpeg 4.4.2)."""
    video = _make_video(tmp_path, lambda t: ("TESTE FIM DE VIDEO", 0.70, 20),
                        duration=5.0, fps=2)
    frames = caption_reviewer.prepare_frames(video, str(tmp_path))
    assert frames, "fonte de fps baixo tem que produzir ao menos 1 imagem"
    for f in frames:
        assert os.path.exists(f["path"])


def test_read_frames_marca_legenda_e_cena(tmp_path, monkeypatch):
    p1, p2 = str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")
    Image.new("RGB", (64, 32), (0, 0, 0)).save(p1)
    Image.new("RGB", (64, 64), (0, 0, 0)).save(p2)
    captured = {}

    def fake_call(model, system, content, **kw):
        captured["content"] = content
        return {"text": "{}", "data": {"legenda_fala": "", "graficos": []},
                "usage": None, "model": model}

    monkeypatch.setattr(anthropic_client, "call", fake_call)
    out = caption_reviewer.read_frames([
        {"path": p1, "t": 1.0, "kind": "caption"},
        {"path": p2, "t": 2.5, "kind": "scene"},
    ])
    assert out["data"] == {"legenda_fala": "", "graficos": []}
    texts = [b["text"] for b in captured["content"] if b.get("type") == "text"]
    assert any("LEGENDA 1" in t for t in texts)
    assert any("CENA 2" in t for t in texts)
    n_imgs = sum(1 for b in captured["content"] if b.get("type") == "image")
    assert n_imgs == 2
