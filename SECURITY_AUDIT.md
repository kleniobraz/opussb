# OpusSB (SB Clips) — Auditoria de Seguranca e Clean Code

**Data:** 2026-07-03 · **Escopo:** backend Flask (`/opt/opussb/*.py`), frontend Next.js (`frontend/`),
config do Nginx e infra da VPS Hostinger (producao, `https://sbclips.space`). **Modo:** read-only (nada
corrigido, nada deployado, nenhuma config alterada). A correcao vem depois, priorizada pelo dono.

## Metodologia
Ferramentas automaticas rodadas num venv ISOLADO (o venv de producao NAO foi tocado):
`pip-audit` (sobre o `pip freeze` real da producao), `bandit`, `ruff`, e `npm audit` no frontend.
Revisao manual das areas de risco por dimensao (auth, autorizacao, injecao, segredos, superficie,
upload, clean code), e cada achado passou por VERIFICACAO ADVERSARIAL (tentativa de refutar antes de
reportar), pra nao classificar falso-positivo como risco real.

## Veredito
**E seguro operar? Sim, sem achado Critico.** Zero CVE em dependencia (pip-audit limpo), nenhuma injecao
realmente exploravel, base de auth solida (scrypt, sessao server-side validada por request, state anti-CSRF
no Zoho, rota interna com whitelist). O risco real concentra-se em DISPONIBILIDADE (DoS por exaustao de
disco no caminho de upload) e em alguns pontos de hardening. Tudo corrigivel com esforco baixo/medio.

## Placar por gravidade

### Critico: nenhum.

### Alto (2): ambos DoS por disco no upload
| Achado | Local | Por que importa | Esforco |
|---|---|---|---|
| Corpo do upload e gravado em disco (spool do multipart) ANTES de checar espaco/vaga; teto de 16 GiB | `app.py:57`, `app.py:2586` vs `app.py:2616` | Usuario logado enche o FS raiz (o mesmo do SQLite) e derruba app + banco | baixo |
| `/api/end_screens` grava o arquivo inteiro antes de checar tamanho; sem gate de concorrencia | `app.py:2294`, `app.py:2304` | Mesmo vetor de exaustao de disco por um arquivo grande com extensao valida | baixo |

### Medio (4)
| Achado | Local | Por que importa | Esforco |
|---|---|---|---|
| DELETE de tela final sem gate de dono/admin | `app.py:2345` | Qualquer usuario ativo destroi recurso COMPARTILHADO (DB + asset S3); contrasta com `delete_project` que usa `_can_manage` | baixo |
| Throttle de login colapsa para "por e-mail" atras do proxy (todo IP vira 127.0.0.1) | `app.py:164`, `app.py:402` | Habilita password spraying e lockout-DoS de contas | medio |
| Confianca das rotas internas = toda a bridge Docker (`172.17.0.0/16`), so por IP, sem segredo | `app.py:2743`, `/api/callback`, `/api/internal` | Fail-open: depende de UMA linha do Nginx (404); qualquer container comprometido na bridge forja status/clips de job | medio |
| Jobs reapados por timeout e falha de save nao limpam o disco | `db.py:824`, `app.py:2631` | Arquivos grandes vazam no FS raiz (mesmo tema dos Alto) | baixo |

### Baixo (9)
| Achado | Local | Nota |
|---|---|---|
| Erro cru do job devolvido ao cliente vaza infra (endpoint S3, bucket, URL do N8N, caminhos) | `app.py:890` via `processor.py:766` | mensagem generica resolve |
| Enumeracao de usuario por timing no `/api/login` | `app.py:414` | mensagem ja e generica; diferenca de tempo |
| `must_change_password` so barra no upload | `app.py:2583` | conta com senha padrao navega antes de trocar |
| Sessao sem tempo de vida absoluto (janela deslizante de 7 dias) | `app.py:54`, `db.py:312` | |
| DDL por f-string em `_ensure_column` (latente, NAO exploravel hoje) | `db.py:35` | nomes fixos internos, nao vem do usuario |
| CSP com `unsafe-inline` no `script-src` | nginx `sbclips.space` | defesa-em-profundidade; so importa se houver XSS (nao encontrado) |
| `/api/upload` valida so a extensao (sem ffprobe/magic) | `app.py:2587` | end_screens e music validam conteudo |
| SPA legada em `static/` ainda servida pelo Nginx | nginx + `static/js/views.js` | XSS legado; parar de servir ou blindar |
| `opussb.db` legivel por todos no host (`-rw-r--r--`) | `/opt/opussb/opussb.db` | hashes + sessoes; `chmod 600` |

### Refutado (1)
| Achado alegado | Veredito | Por que |
|---|---|---|
| Validacao do id_token do Zoho sem assinatura/aud/exp | REFUTADO (nao exploravel) | o token vem por troca back-channel server-to-server sobre TLS direto do endpoint Zoho, e `iss`/dominio SAO checados |

## Base solida confirmada (o que ESTA certo)
- **Dependencias:** `pip-audit` sem CVE conhecido; `ultralytics` em 8.4.84 (versao nao comprometida, input do modelo e confiavel); `npm audit` so 2 moderate transitivos (postcss via Next, sem exposicao runtime).
- **Injecao:** nenhum `shell=True` nos subprocess; SQL parametrizado; a unica query dinamica (`update_job`) recebe chaves so de um WHITELIST (`runpod_job_id, status, progress, error`) na unica rota que passa chaves dinamicas, e essa rota e interna e gated. O `B608` do bandit e falso-positivo na pratica.
- **Auth:** scrypt (Werkzeug); sessao server-side validada a cada request (da pra revogar); state anti-CSRF no OAuth; break-glass so para conta `active`.
- **Rotas internas:** defesa em tres camadas (bind so em loopback/bridge + Nginx 404 + gate de subnet); reforcado pelo `ufw` (2026-07-03) que so abre 22/80/443 + `docker0:5001`.
- **Segredos:** `.env` fora do git; `.gitignore` cobre `.env`, `opussb.db`, cookies.

## Clean code (secundario, por impacto)
- **Alto:** `app.py` monolitico (2790 linhas, 55 rotas, sem blueprints); duas suites de teste divergentes e nenhuma CI rodando.
- **Medio:** boilerplate repetido nas rotas de midia (thumb/waveform/filmstrip); `run_thread2` com 164 linhas/7 estagios; `static/` como dead code versionado.
- **Baixo:** invocacao de ffmpeg/ffprobe repetida em ~13 pontos sem helper; 23 itens triviais de lint (ruff).

## Priorizacao de correcao (proposta)
1. **Onda 1 (disponibilidade):** os 2 Alto de DoS de disco + o Medio do reaper/save que nao limpam disco. Tema coeso: gestao de disco. NAO baixar o teto de upload (podcasts de 1-2h/11GB+ sao legitimos); checar espaco livre e concorrencia ANTES de spoolar, e `rmtree` na limpeza.
2. **Onda 2 (exposicao barata):** DELETE de end_screen sem gate; erro cru vazando infra.
3. **Onda 3 (hardening):** throttle atras do proxy; segredo nas rotas internas; `chmod 600` no db; remover `static/` legado; CSP com nonce.
4. **Clean code:** CI minima rodando os testes; quebrar `app.py` em blueprints.
