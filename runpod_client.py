"""Cliente do endpoint RunPod SERVERLESS de reframe (2º endpoint, separado do Whisper, que roda via
N8N). Submete um clipe pra reenquadrar na GPU e consulta o resultado, via REST direto: POST /run
(assíncrono) + poll GET /status. Ver BACKEND_CONTEXT §20.

Não levanta sozinho a decisão de fallback: quem chama (processor.run_thread2) cai no reframe de CPU
se isto levantar."""
import logging
import time

import requests

import config

log = logging.getLogger("opussb.runpod")

_BASE = "https://api.runpod.ai/v2"
_POLL_INTERVAL = 2.0
# Timeout de REDE de cada chamada HTTP (connect, read). NÃO é o tempo do job (esse é o
# executionTimeout, passado no submit e controlado pelo RunPod).
_HTTP_TIMEOUT = (10, 30)
# Quantos erros de rede consecutivos no poll antes de desistir (blip transitório não derruba o job).
_MAX_POLL_ERRORS = 5


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.RUNPOD_API_KEY}", "Content-Type": "application/json"}


def submit_reframe(input_url: str, output_put_url: str, output_content_type: str,
                   params: dict, exec_timeout: int) -> str:
    """Dispara um job de reframe (assíncrono) e devolve o job_id do RunPod. `params` são os de
    estabilidade da câmera (deadzone_frac/max_pan_frac/min_conf) repassados pro worker. `exec_timeout`
    (s) vira o executionTimeout do job (em ms), acima do default de 600s do RunPod."""
    endpoint = config.RUNPOD_REFRAME_ENDPOINT_ID
    if not endpoint:
        raise RuntimeError("RUNPOD_REFRAME_ENDPOINT_ID não configurado")
    payload = {
        "input": {
            "input_url": input_url,
            "output_put_url": output_put_url,
            "output_content_type": output_content_type,
            **params,
        },
        "policy": {"executionTimeout": int(exec_timeout) * 1000},
    }
    r = requests.post(f"{_BASE}/{endpoint}/run", json=payload, headers=_headers(), timeout=_HTTP_TIMEOUT)
    r.raise_for_status()
    job_id = r.json()["id"]
    log.info("[reframe-gpu] submetido job %s", job_id)
    return job_id


def poll(job_id: str, timeout: float) -> dict:
    """Faz poll do /status até COMPLETED (devolve o `output`, ex.: {"duration": <s>}) ou levanta em
    FAILED/CANCELLED/TIMED_OUT, output com "error", ou estouro do `timeout` LOCAL. Tolera alguns
    erros de rede transitórios antes de desistir."""
    endpoint = config.RUNPOD_REFRAME_ENDPOINT_ID
    deadline = time.monotonic() + timeout
    errors = 0
    while True:
        try:
            r = requests.get(f"{_BASE}/{endpoint}/status/{job_id}", headers=_headers(),
                             timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            body = r.json()
            errors = 0
        except requests.RequestException as e:
            errors += 1
            if errors >= _MAX_POLL_ERRORS:
                raise RuntimeError(f"reframe job {job_id}: {errors} erros de rede seguidos no poll ({e})")
            time.sleep(_POLL_INTERVAL)
            continue

        status = body.get("status")
        if status == "COMPLETED":
            out = body.get("output") or {}
            if isinstance(out, dict) and out.get("error"):
                raise RuntimeError(f"reframe job {job_id} retornou erro: {out['error']}")
            return out if isinstance(out, dict) else {}
        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RuntimeError(f"reframe job {job_id} {status}: {body.get('output') or body.get('error')}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"reframe job {job_id} timeout local após {timeout}s (status={status})")
        time.sleep(_POLL_INTERVAL)
