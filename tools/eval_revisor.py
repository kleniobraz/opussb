#!/usr/bin/env python
"""tools/eval_revisor.py — avaliação de RECALL do Revisor de legendas (camada 2).

⚠️  ESTE SCRIPT CUSTA DINHEIRO REAL. Cada vídeo dispara 2 chamadas Sonnet 5
⚠️  (leitura por visão + revisão) POR VERSÃO avaliada: ~US$ 0,10-0,40 por rodada,
⚠️  ~US$ 1,50-3,50 numa avaliação completa. NÃO roda no pytest (de propósito:
⚠️  vive em tools/, execução manual). Rodar SÓ com a palavra do Klenio, na
⚠️  janela combinada. Use --teto-usd como trava de gasto.

O que ele faz
-------------
Pra cada arquivo de ground truth (*.gt.yaml), roda o MESMO vídeo em duas versões
do pipeline e compara com os erros conhecidos:
  - "antigo": código da master (worktree git temporário e descartável, criado e
    removido pelo próprio script; ou --repo-antigo pra apontar outro checkout);
  - "novo": o código deste checkout (a branch em que o script está).

Chama as funções do pipeline DIRETO (prepare_frames -> read_frames -> review_text):
NENHUMA linha é criada no DB de produção. O glossário REAL é lido (só leitura),
porque os findings de produção dependem dele. A mídia temporária é apagada no fim
de cada rodada, como em produção.

Métricas (por vídeo e agregado)
-------------------------------
  - RECALL de findings: erros conhecidos pegos / erros conhecidos. A métrica que manda.
  - FALSO POSITIVO: findings que não casam com nenhum erro conhecido. Métrica de
    primeira classe: em vídeo sem erro (erros: []), é a única que importa.
  - COBERTURA de palavras: % das palavras do texto real presente na legenda_fala
    (diagnóstico de captura; exige "texto_legenda" no ground truth).
  - CUSTO real (US$) por rodada e total, pela mesma tabela do custo logado.

Pro vídeo original (Fixado Godoy), inclua "original_review_id" no ground truth:
o script puxa o relatório antigo guardado no DB (read-only) e o compara com as
mesmas métricas, lado a lado com as duas rodadas novas.

Ground truth: um YAML por vídeo, preenchido à mão (aceita comentários com #).
Veja o modelo completo em tools/eval_data/exemplo.gt.yaml. Campos:
  video               caminho do .mp4, relativo ao próprio .gt.yaml (obrigatório)
  descricao           livre, aparece no relatório (opcional)
  original_review_id  id de caption_reviews pro diff contra o DB (opcional)
  texto_legenda       texto fiel da legenda queimada, habilita a cobertura (opcional)
  erros               lista de {trecho, correto, tipo?}; [] = vídeo limpo (mede FP)

Uso
---
    ./venv/bin/python tools/eval_revisor.py tools/eval_data/*.gt.yaml \
        [--teto-usd 5.0] [--so-versao ambas|antigo|novo] [--out-dir tools/eval_out] \
        [--repo-antigo /caminho/checkout-master]

Os resultados brutos de cada rodada (legenda_fala, findings, custo) ficam em
--out-dir pra inspeção manual depois.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


# ─── Worker: roda UMA (versão, vídeo) num subprocess isolado ──────────────────
# Precisa ser subprocess porque as duas versões têm módulos com o MESMO nome
# (caption_reviewer, config...); importar as duas no mesmo interpretador colidiria.
# O pai chama com cwd=REPO (o load_dotenv() do config acha o .env real) e
# DB_PATH apontando pro banco real (leitura do glossário; o default do db.py é
# relativo ao diretório do MÓDULO, que no worktree da master não tem banco).

def _worker(repo: str, video: str, out_path: str) -> None:
    # O load_dotenv() do config procura o .env a partir do diretório do MÓDULO
    # (worktree temporário não tem .env); carrega o real explicitamente ANTES.
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    sys.path.insert(0, repo)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(levelname)s %(message)s")
    import anthropic_client  # noqa: E402 (do repo alvo, via sys.path)
    import caption_reviewer  # noqa: E402

    work = tempfile.mkdtemp(prefix="eval_revisor_")
    try:
        frames = caption_reviewer.prepare_frames(video, work)
        read = caption_reviewer.read_frames(frames)
        data = read["data"] or {}
        legenda = (data.get("legenda_fala") or "").strip()
        graficos = data.get("graficos") or []
        review = caption_reviewer.review_text(legenda, graficos)
        findings = (review["data"] or {}).get("findings") or []
        cost = (anthropic_client.price(read["model"], read["usage"])
                + anthropic_client.price(review["model"], review["usage"]))
        result = {
            "repo": repo,
            "video": video,
            "frames": len(frames),
            "frames_legenda": sum(1 for f in frames if f.get("kind") == "caption"),
            "legenda_fala": legenda,
            "graficos": graficos,
            "findings": findings,
            "cost_usd": round(cost, 5),
            "tokens": {
                "read_in": read["usage"].input_tokens,
                "read_out": read["usage"].output_tokens,
                "review_in": review["usage"].input_tokens,
                "review_out": review["usage"].output_tokens,
            },
        }
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)  # mídia efêmera, como em produção


# ─── Ground truth (YAML preenchido à mão: carrega com tolerância) ─────────────

def _carrega_gt(gt_path: Path) -> dict:
    gt = yaml.safe_load(gt_path.read_text(encoding="utf-8")) or {}
    if not isinstance(gt, dict) or not str(gt.get("video") or "").strip():
        raise ValueError(f"{gt_path.name}: precisa de um mapa YAML com o campo 'video'")
    erros = gt.get("erros") or []  # null/ausente = lista vazia (vídeo limpo)
    if not isinstance(erros, list):
        raise ValueError(f"{gt_path.name}: 'erros' tem que ser lista (use [] pra vídeo limpo)")
    limpos = []
    for i, e in enumerate(erros, 1):
        if not isinstance(e, dict) or not str(e.get("trecho") or "").strip():
            raise ValueError(f"{gt_path.name}: erro #{i} precisa ao menos de 'trecho'")
        limpos.append({
            "trecho": str(e["trecho"]).strip(),
            "correto": str(e.get("correto") or "").strip(),
            "tipo": str(e.get("tipo") or "").strip().lower() or None,
        })
    gt["erros"] = limpos
    return gt


# ─── Métricas ─────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().casefold())


def _casa(erro: dict, finding: dict) -> bool:
    """Erro conhecido casa com um finding se o trecho de um contém o do outro
    (case-insensitive). Acentos CONTAM: 'inteligencia' != 'inteligência'."""
    et, ft = _norm(erro.get("trecho", "")), _norm(finding.get("trecho", ""))
    return bool(et) and bool(ft) and (et in ft or ft in et)


def _avalia_findings(findings: list, erros: list) -> dict:
    pegos, perdidos = [], []
    casados: set[int] = set()
    for erro in erros:
        match = None
        for i, f in enumerate(findings):
            if _casa(erro, f):
                match = (i, f)
                break
        if match is None:
            perdidos.append(erro)
        else:
            casados.add(match[0])
            f = match[1]
            pegos.append({
                "erro": erro,
                "finding": f,
                "correcao_exata": _norm(f.get("sugestao", "")) == _norm(erro.get("correto", "")),
                "tipo_divergente": bool(erro.get("tipo"))
                and _norm(f.get("tipo", "")) != _norm(erro["tipo"]),
            })
    falsos_positivos = [f for i, f in enumerate(findings) if i not in casados]
    return {
        "conhecidos": len(erros),
        "pegos": pegos,
        "perdidos": perdidos,
        "falsos_positivos": falsos_positivos,
        "recall": (len(pegos) / len(erros)) if erros else None,
    }


def _palavras(s: str) -> set[str]:
    return set(re.findall(r"\w+", (s or "").casefold(), flags=re.UNICODE))


def _cobertura(texto_gt: str | None, legenda_lida: str) -> float | None:
    if not texto_gt:
        return None
    gt = _palavras(texto_gt)
    if not gt:
        return None
    return len(gt & _palavras(legenda_lida)) / len(gt)


# ─── Relatório antigo do DB (vídeo original) ──────────────────────────────────

def _resultado_do_db(review_id: str) -> dict | None:
    db_path = REPO / "opussb.db"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM caption_reviews WHERE id = ?", (review_id,)).fetchone()
    con.close()
    if row is None:
        return None
    return {
        "repo": "(relatório antigo guardado no DB)",
        "frames": row["frames_count"],
        "legenda_fala": row["legenda_fala"] or "",
        "findings": json.loads(row["report_json"] or "[]"),
        "cost_usd": row["cost_usd"] or 0.0,
    }


# ─── Orquestração ─────────────────────────────────────────────────────────────

def _roda_versao(rotulo: str, repo: Path, video: Path, out_dir: Path) -> dict:
    out_json = out_dir / f"{video.stem}__{rotulo}.json"
    print(f"    -> rodando versão '{rotulo}' ({repo}) ...", flush=True)
    env = {**os.environ, "DB_PATH": str(REPO / "opussb.db")}
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker",
         "--repo", str(repo), "--video", str(video), "--out", str(out_json)],
        cwd=str(REPO), env=env,
    )
    if r.returncode != 0:
        raise RuntimeError(f"worker '{rotulo}' falhou (rc={r.returncode}) pra {video.name}")
    return json.loads(out_json.read_text(encoding="utf-8"))


def _fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:5.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gts", nargs="*", help="arquivos *.gt.yaml de ground truth")
    ap.add_argument("--repo-antigo", help="checkout do código antigo (default: worktree "
                                          "temporário da master, criado e removido aqui)")
    ap.add_argument("--so-versao", choices=["ambas", "antigo", "novo"], default="ambas")
    ap.add_argument("--out-dir", default=str(REPO / "tools" / "eval_out"))
    ap.add_argument("--teto-usd", type=float, default=None,
                    help="trava de gasto: não INICIA rodada nova se o total real passou disso")
    # modo interno (subprocess); não usar à mão
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--repo", help=argparse.SUPPRESS)
    ap.add_argument("--video", help=argparse.SUPPRESS)
    ap.add_argument("--out", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        _worker(args.repo, args.video, args.out)
        return 0

    if not args.gts:
        ap.error("informe ao menos um *.gt.yaml (modelo em tools/eval_data/exemplo.gt.yaml)")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    versoes: list[tuple[str, Path]] = []
    worktree_tmp: str | None = None
    try:
        if args.so_versao in ("ambas", "antigo"):
            if args.repo_antigo:
                versoes.append(("antigo", Path(args.repo_antigo).resolve()))
            else:
                worktree_tmp = tempfile.mkdtemp(prefix="eval_revisor_master_")
                subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach",
                                worktree_tmp, "master"], check=True, capture_output=True)
                versoes.append(("antigo", Path(worktree_tmp)))
        if args.so_versao in ("ambas", "novo"):
            versoes.append(("novo", REPO))

        custo_total = 0.0
        agreg: dict[str, dict] = {r: {"conhecidos": 0, "pegos": 0, "fp": 0,
                                      "coberturas": [], "custo": 0.0}
                                  for r, _ in versoes}
        linhas_relatorio: list[str] = []

        for gt_arg in args.gts:
            gt_path = Path(gt_arg).resolve()
            gt = _carrega_gt(gt_path)
            video = (gt_path.parent / gt["video"]).resolve()
            if not video.exists():
                print(f"!! {gt_path.name}: vídeo não encontrado: {video}", file=sys.stderr)
                return 2
            erros = gt["erros"]
            print(f"\n== {video.name} ({gt.get('descricao', 'sem descrição')}) — "
                  f"{len(erros)} erro(s) conhecido(s) ==")

            resultados: list[tuple[str, dict]] = []
            if gt.get("original_review_id"):
                antigo_db = _resultado_do_db(str(gt["original_review_id"]))
                if antigo_db is None:
                    print(f"    (aviso: review {gt['original_review_id']} não está no DB)")
                else:
                    resultados.append(("db-antigo", antigo_db))

            for rotulo, repo in versoes:
                if args.teto_usd is not None and custo_total >= args.teto_usd:
                    print(f"\n!! TETO DE GASTO atingido (US$ {custo_total:.2f} >= "
                          f"{args.teto_usd:.2f}): abortando antes de nova rodada.")
                    break
                res = _roda_versao(rotulo, repo, video, out_dir)
                custo_total += res.get("cost_usd", 0.0)
                agreg[rotulo]["custo"] += res.get("cost_usd", 0.0)
                resultados.append((rotulo, res))

            linhas_relatorio.append(f"\n{video.name} ({len(erros)} erro(s) conhecido(s)):")
            linhas_relatorio.append(
                f"  {'versão':<10} {'recall':>7} {'FP':>4} {'cobertura':>10} "
                f"{'findings':>9} {'imgs':>5} {'custo US$':>10}")
            for rotulo, res in resultados:
                m = _avalia_findings(res.get("findings", []), erros)
                cob = _cobertura(gt.get("texto_legenda"), res.get("legenda_fala", ""))
                if rotulo in agreg:
                    agreg[rotulo]["conhecidos"] += m["conhecidos"]
                    agreg[rotulo]["pegos"] += len(m["pegos"])
                    agreg[rotulo]["fp"] += len(m["falsos_positivos"])
                    if cob is not None:
                        agreg[rotulo]["coberturas"].append(cob)
                linhas_relatorio.append(
                    f"  {rotulo:<10} {_fmt_pct(m['recall']):>7} "
                    f"{len(m['falsos_positivos']):>4} {_fmt_pct(cob):>10} "
                    f"{len(res.get('findings', [])):>9} {res.get('frames', '-'):>5} "
                    f"{res.get('cost_usd', 0.0):>10.4f}")
                for p in m["pegos"]:
                    if p["tipo_divergente"]:
                        linhas_relatorio.append(
                            f"      pego, tipo divergente ({rotulo}): "
                            f"'{p['erro']['trecho']}' esperava tipo "
                            f"'{p['erro']['tipo']}', veio '{p['finding'].get('tipo')}'")
                for p in m["perdidos"]:
                    linhas_relatorio.append(
                        f"      PERDIDO ({rotulo}): '{p['trecho']}' -> '{p['correto']}'")
                for f in m["falsos_positivos"]:
                    linhas_relatorio.append(
                        f"      FP ({rotulo}): [{f.get('tipo')}] '{f.get('trecho')}' "
                        f"-> '{f.get('sugestao')}'")

        print("\n" + "=" * 72)
        print("RELATÓRIO")
        print("=" * 72)
        for linha in linhas_relatorio:
            print(linha)
        print("\nAGREGADO (a métrica que manda é o recall; FP é primeira classe):")
        for rotulo, a in agreg.items():
            recall = (a["pegos"] / a["conhecidos"]) if a["conhecidos"] else None
            cob = (sum(a["coberturas"]) / len(a["coberturas"])) if a["coberturas"] else None
            print(f"  {rotulo:<10} recall {_fmt_pct(recall)} "
                  f"({a['pegos']}/{a['conhecidos']}) | FP {a['fp']} | "
                  f"cobertura média {_fmt_pct(cob)} | custo US$ {a['custo']:.4f}")
        print(f"\nCUSTO TOTAL REAL DESTA AVALIAÇÃO: US$ {custo_total:.4f}")
        print(f"Resultados brutos por rodada em: {out_dir}")
        return 0
    finally:
        if worktree_tmp:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force",
                            worktree_tmp], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
