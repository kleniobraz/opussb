#!/usr/bin/env python3
"""Testes unitários do backend do OpusSB (puros, sem rede e sem tocar dados reais).

Rode com:  ./venv/bin/python test_backend.py

Usa um DB_PATH temporário (definido ANTES de importar app/db), então o import do app
cria o schema num banco descartável, nunca no opussb.db de produção (regra 10). Cobre:
  - a montagem do comando de queima (regressão do bug de áudio sumido);
  - validação de segmentos de legenda;
  - parsing tolerante do clips_json;
  - extração de legenda do SRT + render do .ass (round-trip com edição).
"""
import os
import sys
import tempfile

# DB descartável ANTES de importar qualquer módulo que toque o banco.
_TMP_DB = tempfile.NamedTemporaryFile(prefix="opussb_test_", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DB_PATH"] = _TMP_DB.name

import app          # noqa: E402
import captions     # noqa: E402
import processor    # noqa: E402

_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        _failures.append(msg)


# ─── 1. Comando de queima: o áudio NUNCA pode sumir (regressão do bug) ─────────
def test_burn_cmd():
    print("[burn cmd]")

    # Só legenda (caso do bug): tem que mapear o áudio original (0:a?) e copiá-lo.
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", None, None, "out.mp4")
    check("0:a?" in cmd, "legenda-só mapeia o áudio original (0:a?)")
    i = cmd.index("-c:a")
    check(cmd[i + 1] == "copy", "legenda-só copia o áudio (sem recodificar)")
    check("[v]" in cmd, "legenda-só mapeia o vídeo filtrado [v]")
    check("+faststart" in cmd, "legenda-só usa +faststart")

    # Só música: mixa fala+música e recodifica em aac.
    cmd = app._build_burn_cmd("base.mp4", None, "m.mp3", 0.2, "out.mp4")
    check("[a]" in cmd, "música-só mapeia o áudio mixado [a]")
    i = cmd.index("-c:a")
    check(cmd[i + 1] == "aac", "música-só recodifica em aac")
    check("-shortest" in cmd, "música-só usa -shortest")

    # Legenda + música: vídeo filtrado e áudio mixado.
    cmd = app._build_burn_cmd("base.mp4", "cap.ass", "m.mp3", 0.2, "out.mp4")
    check("[v]" in cmd and "[a]" in cmd, "legenda+música mapeia [v] e [a]")

    # Sem legenda e sem música: vídeo direto, áudio original preservado.
    cmd = app._build_burn_cmd("base.mp4", None, None, None, "out.mp4")
    check("0:v" in cmd and "0:a?" in cmd, "sem nada mapeia 0:v e preserva 0:a?")


# ─── 2. Validação de segmentos de legenda ─────────────────────────────────────
def test_validate_segments():
    print("[validate segments]")
    ok = app._validate_segments([{"start": 0, "end": 1.5, "text": " oi "}])
    check(ok == [{"start": 0.0, "end": 1.5, "text": "oi"}], "segmento válido normaliza e faz trim")

    for bad, why in [
        ("nao-lista", "não-lista"),
        ([{"start": 1, "end": 0.5, "text": "x"}], "end <= start"),
        ([{"start": float("nan"), "end": 1, "text": "x"}], "start NaN"),
        ([{"start": 1.0001, "end": 1.0002, "text": "x"}], "intervalo some no arredondamento"),
        ([{"start": 0, "end": 1}], "text ausente é ok"),  # text default "" -> válido
    ]:
        try:
            app._validate_segments(bad)
            raised = False
        except ValueError:
            raised = True
        if why == "text ausente é ok":
            check(not raised, "segmento sem text não quebra (vira string vazia)")
        else:
            check(raised, f"rejeita: {why}")


# ─── 3. Parsing tolerante do clips_json ───────────────────────────────────────
def test_parse_clips_json():
    print("[parse clips_json]")
    check(processor._parse_clips_json(None) == [], "None -> []")
    check(processor._parse_clips_json([{"start": 1}]) == [{"start": 1}], "lista passa direto")
    check(processor._parse_clips_json('[{"start":1,"end":2}]') == [{"start": 1, "end": 2}], "json puro")
    fenced = '```json\n[{"start":1,"end":2}]\n```'
    check(processor._parse_clips_json(fenced) == [{"start": 1, "end": 2}], "json com cerca ```json")
    check(processor._parse_clips_json("lixo {[") == [], "lixo -> []")
    check(processor._parse_clips_json('{"nao":"lista"}') == [], "objeto (não-lista) -> []")


# ─── 4. SRT -> segmentos editáveis -> .ass (round-trip com edição) ─────────────
def test_captions_roundtrip():
    print("[captions srt -> ass]")
    srt = ("1\n00:00:05,740 --> 00:00:09,680\nQual o maior erro em marketing hoje?\n\n"
           "2\n00:00:10,020 --> 00:00:15,760\nDepositar confiança numa estratégia só.\n")
    segs = captions.segments_for_clip(srt, 5.74, 16.0)
    check(len(segs) > 0, "extrai segmentos do clipe")
    check(all(s["start"] >= 0 for s in segs), "tempos relativos ao início do clipe (>= 0)")

    ass = captions.build_ass_from_segments(segs)
    check(ass and "Inter Medium" in ass and "Dialogue:" in ass, "render do .ass com fonte SB")

    segs[0]["text"] = "Texto Corrigido"
    ass2 = captions.build_ass_from_segments(segs)
    check("Texto Corrigido" in ass2, "edição do texto reflete no .ass")

    # Defesa: segmento com tempo inválido é pulado no render.
    bad = [{"start": 2.0, "end": 1.0, "text": "invertido"}, {"start": 0.0, "end": 1.0, "text": "bom"}]
    ass3 = captions.build_ass_from_segments(bad)
    check(ass3 is not None and "invertido" not in ass3 and "bom" in ass3, "render pula segmento inválido")
    check(captions.build_ass_from_segments([]) is None, "lista vazia -> None")


def main():
    for t in (test_burn_cmd, test_validate_segments, test_parse_clips_json, test_captions_roundtrip):
        t()
    print()
    if _failures:
        print(f"FALHOU: {len(_failures)} verificação(ões)")
        sys.exit(1)
    print("TODOS OS TESTES PASSARAM")


if __name__ == "__main__":
    try:
        main()
    finally:
        os.unlink(_TMP_DB.name)
        for ext in ("-wal", "-shm"):
            p = _TMP_DB.name + ext
            if os.path.exists(p):
                os.unlink(p)
