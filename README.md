# pncp-analyzer

Raspa os editais do [PNCP](https://pncp.gov.br) (API pública de consulta) para um SQLite local
e oferece uma interface web de busca voltada para fornecedor de software (pequena empresa).

## Como rodar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. carga inicial: todos os editais publicados nos últimos 90 dias,
#    do mais recente pro mais antigo (é isso que cobre tudo que está ABERTO)
python scraper.py abertas

# 2. interface web
python app.py          # abra http://localhost:5000
```

A interface já abre filtrada em **"Recebendo propostas"** + **"Meu perfil (TI/software)"**.
Clicar num edital busca na hora os itens e os documentos no PNCP (e guarda no banco).

## Rotina diária

```bash
python scraper.py sync   # novos + atualizados desde a última execução (prazos adiados, retificações etc.)
```

Agende no cron, por exemplo: `0 7 * * * cd /caminho/pncp-analyzer && .venv/bin/python scraper.py sync`

## Comandos do scraper

| comando | o que faz |
|---|---|
| `abertas [`--janela 90`] [--uf GO]` | varre os últimos N dias em blocos de 7, mais recente primeiro |
| `sync` | incremental por data de atualização (pega novos **e** alterados) |
| `backfill --desde 2024-01-01 [--ate ...] [--uf GO]` | carga histórica por período de publicação |
| `itens [--limite 100]` | baixa itens + lista de arquivos dos editais do perfil ainda abertos |
| `documentos [--limite 50]` | baixa os PDFs dos editais do perfil abertos para `documentos/` |

Tudo é **retomável**: Ctrl+C e rode de novo que continua da página onde parou (estado na tabela `meta`).
Todo registro é upsert pela chave `numeroControlePNCP` — rodar duas vezes não duplica nada.

## Seu perfil

Os termos de busca do perfil (checkbox "Meu perfil") estão em **`perfil.py`** — edite a lista `TERMOS`.
A busca ignora acentos (FTS5 `unicode61 remove_diacritics 2`).

No detalhe do edital aparece a tag **"benefício ME/EPP"** quando algum item tem participação
exclusiva ou cota reservada para micro/pequena empresa (campo `tipoBeneficio` dos itens).

## O que aprendi da API (pra você não sofrer)

- Base de consulta: `https://pncp.gov.br/api/consulta` (aberta, sem autenticação).
- `GET /v1/contratacoes/publicacao` — por data de publicação. `codigoModalidadeContratacao` é
  **obrigatório** (códigos 1–14), datas `yyyyMMdd`, `tamanhoPagina` **máx. 50**.
- `GET /v1/contratacoes/atualizacao` — mesma coisa por data de atualização global (usado no `sync`).
- `GET /v1/contratacoes/proposta` — seria "só propostas abertas", mas responde **504** (sobrecarga
  do lado deles). Por isso o comando `abertas` varre a publicação recente e o filtro de
  "recebendo propostas" é aplicado pelo `dataEncerramentoProposta` no banco.
- Itens e arquivos: `https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens`
  e `/arquivos` (URL de download direto em cada arquivo).
- Página do edital no portal: `https://pncp.gov.br/app/editais/{cnpj}/{ano}/{sequencial}`.
- **Rate limit agressivo**: 429 com poucas chamadas seguidas → throttle de 1 s + backoff exponencial.
- **JSON inválido**: Dispensa e Credenciamento devolvem caracteres de controle não escapados →
  `json.loads(..., strict=False)` em tudo.
- Volume: ~6 mil editais/dia no Brasil inteiro; a varredura de 90 dias leva algumas horas
  (limite deles, não nosso). Use `--uf` se quiser algo rápido e regional.

## Arquivos

- `scraper.py` — coleta (CLI)
- `app.py` — Flask: busca (`/api/busca`), detalhe (`/api/edital`), stats
- `db.py` — schema SQLite + FTS5 + upsert
- `perfil.py` — termos do seu perfil
- `templates/index.html` — interface (vanilla JS)
- `pncp.db` — o banco (criado na primeira execução)
