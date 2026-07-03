"""Testes do revisor de legendas: CRUD do glossário + caption_reviews e o dedup de frames
(determinístico). Não faz chamadas de rede (o cliente Anthropic é só construído)."""
import sqlite3

import numpy as np
import pytest
from PIL import Image

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


# ─── Dedup de frames (determinístico, sem ffmpeg) ───────────────────────────────

def _make_frame(path, band_value, base=100):
    """Frame cinza `base` com um bloco de valor `band_value` na faixa da legenda (y 55-92%)."""
    a = np.full((192, 108), base, dtype=np.uint8)  # 108x192, proporção 9:16
    a[int(192 * 0.55):int(192 * 0.92), :] = band_value
    Image.fromarray(a, mode="L").save(path)


def test_dedup_keeps_only_changed_frames(tmp_path):
    paths = []
    # 0: base; 1,2: iguais (dup); 3: legenda muda forte; 4: igual a 3 (dup)
    for i, band in enumerate([100, 100, 100, 255, 255]):
        p = tmp_path / f"f_{i:03d}.jpg"
        _make_frame(str(p), band)
        paths.append(str(p))
    kept = caption_reviewer.dedup_frames(paths, threshold=0.09)
    assert kept == [0, 3]
    # determinístico
    assert caption_reviewer.dedup_frames(paths, threshold=0.09) == kept


def test_cap_frames_resamples_and_bounds(tmp_path):
    idxs = list(range(200))
    capped = caption_reviewer._cap_frames(idxs, 50)
    assert len(capped) == 50
    assert capped[0] == 0 and capped == sorted(capped)  # uniforme e crescente
    # abaixo do teto não mexe
    assert caption_reviewer._cap_frames([1, 2, 3], 50) == [1, 2, 3]
