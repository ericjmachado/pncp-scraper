#!/usr/bin/env python3
"""Scraper da API de consulta do PNCP (https://pncp.gov.br/api/consulta).

Uso:
  python scraper.py abertas [--janela 90] [--uf GO]   # editais abertos: varre os últimos N dias, do mais recente pro mais antigo
  python scraper.py backfill --desde 2026-01-01 [--ate 2026-08-26] [--uf GO]
  python scraper.py sync                    # incremental desde a última execução
  python scraper.py itens [--limite 100]      # baixa itens/arquivos dos editais do perfil ainda abertos
  python scraper.py documentos [--limite 50]  # baixa os PDFs dos editais do perfil para ./documentos/

Retomável: interrompa com Ctrl+C e rode de novo que continua de onde parou.
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from loguru import logger

import perfil
from db import MODALIDADES_NOMES, init_db, meta_get, meta_set, upsert_edital

# console mostra INFO+; o arquivo guarda tudo (DEBUG inclui cada requisição HTTP)
logger.remove()
logger.add(sys.stderr, level="INFO")
logger.add(Path(__file__).parent / "logs" / "scraper.log",
           rotation="20 MB", retention=10, encoding="utf-8", level="DEBUG")

CONSULTA = "https://pncp.gov.br/api/consulta"
PNCP_API = "https://pncp.gov.br/api/pncp"
# tabela de modalidades da Lei 14.133 (1..14), na ordem mais útil para fornecedor de software
MODALIDADES = (6, 8, 4, 9, 12, 5, 7, 1, 3, 10, 11, 13, 2, 14)
PAGINA_TAM = 50  # máximo aceito pela API
THROTTLE = 1.0  # a API devolve 429 com facilidade; não abaixe muito

session = requests.Session()
session.headers["User-Agent"] = "pncp-analyzer/1.0"


def get_json(url, params=None):
    for tentativa in range(9):
        time.sleep(THROTTLE)
        t0 = time.monotonic()
        try:
            resp = session.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            logger.warning(f"erro de rede ({e}) em {url} "
                           f"(tentativa {tentativa + 1}/9), tentando de novo...")
            time.sleep(5 * (tentativa + 1))
            continue
        logger.debug(f"GET {resp.url} -> {resp.status_code} "
                     f"({len(resp.content) // 1024} KB em {time.monotonic() - t0:.2f}s)")
        if resp.status_code in (204, 404):
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            espera = min(5 * 2**tentativa, 120)
            logger.warning(f"HTTP {resp.status_code} (tentativa {tentativa + 1}/9), "
                           f"aguardando {espera}s...")
            time.sleep(espera)
            continue
        resp.raise_for_status()
        # strict=False: o PNCP devolve caracteres de controle não escapados (ex.: Dispensa)
        return json.loads(resp.text, strict=False)
    raise RuntimeError(f"desisti após 9 tentativas: {url}")


def total_no_banco(con):
    return con.execute("SELECT COUNT(*) FROM editais").fetchone()[0]


def _paginar(con, url, params, rotulo):
    pagina = params["pagina"]
    while True:
        params["pagina"] = pagina
        d = get_json(url, params)
        if not d or not d.get("data"):
            logger.debug(f"{rotulo}: página {pagina} vazia, fim")
            break
        ncs = [r["numeroControlePNCP"] for r in d["data"]]
        existiam = con.execute(
            f"SELECT COUNT(*) FROM editais WHERE numero_controle IN ({','.join('?' * len(ncs))})",
            ncs,
        ).fetchone()[0]
        for r in d["data"]:
            upsert_edital(con, r)
        con.commit()
        logger.info(f"{rotulo}: página {pagina}/{d['totalPaginas']} — "
                    f"{len(ncs)} registros ({len(ncs) - existiam} novos, {existiam} atualizados) "
                    f"| {d['totalRegistros']} no período")
        yield pagina
        if pagina >= d["totalPaginas"]:
            break
        pagina += 1


def backfill(desde, ate, uf=None):
    con = init_db()
    logger.info(f"backfill de {desde} a {ate} (uf={uf or 'BR'}) — "
                f"{total_no_banco(con)} editais no banco")
    d1, d2 = desde.replace("-", ""), ate.replace("-", "")
    for m in MODALIDADES:
        nome = MODALIDADES_NOMES[m]
        rotulo = f"{nome} ({m})"
        chave = f"backfill:{d1}:{d2}:{uf or 'BR'}:{m}"
        estado = meta_get(con, chave)
        if estado == "fim":
            logger.info(f"{rotulo}: já concluída, pulando")
            continue
        if estado:
            logger.info(f"{rotulo}: retomando da página {estado}")
        t0 = time.monotonic()
        params = dict(dataInicial=d1, dataFinal=d2, codigoModalidadeContratacao=m,
                      pagina=int(estado or 1), tamanhoPagina=PAGINA_TAM)
        if uf:
            params["uf"] = uf
        for pag in _paginar(con, f"{CONSULTA}/v1/contratacoes/publicacao", params, rotulo):
            meta_set(con, chave, pag + 1)
            con.commit()
        meta_set(con, chave, "fim")
        con.commit()
        logger.info(f"{rotulo}: concluída em {time.monotonic() - t0:.0f}s")


def abertas(janela, uf=None):
    """Varre os últimos `janela` dias em blocos de 7, do mais recente pro mais antigo.

    O endpoint /contratacoes/proposta (só abertos) responde 504 no PNCP; varrer a
    publicação recente cobre a mesma coisa — a interface filtra pelo prazo.
    """
    con = init_db()
    t0 = time.monotonic()
    logger.info(f"varredura dos últimos {janela} dias (uf={uf or 'BR'}), "
                f"do mais recente pro mais antigo")
    fim = date.today()
    while janela > 0:
        ini = fim - timedelta(days=min(7, janela) - 1)
        logger.info(f"== bloco {ini} a {fim} ==")
        backfill(ini.isoformat(), fim.isoformat(), uf)
        janela -= 7
        fim = ini - timedelta(days=1)
    logger.info(f"varredura de abertos concluída em {(time.monotonic() - t0) / 60:.1f} min — "
                f"{total_no_banco(con)} editais no banco")


def sync():
    con = init_db()
    hoje = date.today()
    ultima = meta_get(con, "ultima_sync")
    desde = date.fromisoformat(ultima) - timedelta(days=1) if ultima else hoje - timedelta(days=7)
    d1, d2 = desde.strftime("%Y%m%d"), hoje.strftime("%Y%m%d")
    logger.info(f"sync de {desde} até {hoje} (novos + atualizados)")
    for m in MODALIDADES:
        params = dict(dataInicial=d1, dataFinal=d2, codigoModalidadeContratacao=m,
                      pagina=1, tamanhoPagina=PAGINA_TAM)
        for _ in _paginar(con, f"{CONSULTA}/v1/contratacoes/atualizacao", params, f"modalidade {m}"):
            pass
    meta_set(con, "ultima_sync", hoje.isoformat())
    con.commit()
    logger.info("sync concluído.")


def fetch_detalhe(con, edital_id, cnpj, ano, sequencial):
    """Baixa itens + arquivos de um edital e grava no banco. Retorna (itens, arquivos)."""
    def todas_paginas(recurso):
        acc, pag = [], 1
        while True:
            lote = get_json(f"{PNCP_API}/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/{recurso}",
                            dict(pagina=pag, tamanhoPagina=50))
            if not lote:
                break
            acc.extend(lote)
            if len(lote) < 50:
                break
            pag += 1
        return acc

    itens, arquivos = todas_paginas("itens"), todas_paginas("arquivos")
    con.execute(
        "UPDATE editais SET itens=?, arquivos=?, itens_atualizado_em=? WHERE id=?",
        (json.dumps(itens, ensure_ascii=False), json.dumps(arquivos, ensure_ascii=False),
         datetime.now().isoformat(), edital_id),
    )
    con.commit()
    return itens, arquivos


def baixar_itens(limite):
    """Enriquece com itens/arquivos os editais do perfil ainda com propostas abertas."""
    con = init_db()
    agora = datetime.now().isoformat()
    rows = con.execute(
        """SELECT e.id, e.cnpj, e.ano, e.sequencial, e.numero_controle FROM editais e
           JOIN editais_fts f ON f.rowid = e.id
           WHERE editais_fts MATCH ? AND e.itens IS NULL AND e.data_encerramento >= ?
           ORDER BY e.data_encerramento LIMIT ?""",
        (perfil.fts_query_perfil(), agora, limite),
    ).fetchall()
    logger.info(f"{len(rows)} editais do perfil sem itens")
    for i, r in enumerate(rows, 1):
        fetch_detalhe(con, r["id"], r["cnpj"], r["ano"], r["sequencial"])
        logger.info(f"[{i}/{len(rows)}] {r['numero_controle']}")


DOCS_DIR = Path(__file__).parent / "documentos"


def pasta_docs(numero_controle):
    return DOCS_DIR / numero_controle.replace("/", "-")


def baixar_documentos_de(con, row):
    """Baixa todos os arquivos de um edital para documentos/<numero_controle>/."""
    itens_json = row["arquivos"]
    if itens_json is None:
        _, arquivos = fetch_detalhe(con, row["id"], row["cnpj"], row["ano"], row["sequencial"])
    else:
        arquivos = json.loads(itens_json)
    pasta = pasta_docs(row["numero_controle"])
    pasta.mkdir(parents=True, exist_ok=True)
    for a in arquivos:
        if not a.get("statusAtivo"):
            continue
        seq = a["sequencialDocumento"]
        if any(pasta.glob(f"{seq:03d}_*")):
            continue  # já baixado
        time.sleep(THROTTLE)
        resp = session.get(a["url"], timeout=120)
        if resp.status_code != 200:
            logger.warning(f"arquivo {seq}: HTTP {resp.status_code}, pulando")
            continue
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)', cd)
        nome_orig = m.group(1) if m else (a.get("titulo") or f"doc{seq}")
        nome = re.sub(r'[^\w.\-]+', "_", nome_orig)[:120]
        (pasta / f"{seq:03d}_{nome}").write_bytes(resp.content)
        logger.info(f"{seq:03d}_{nome} ({len(resp.content) // 1024} KB)")


def baixar_documentos(limite):
    """Baixa os documentos dos editais do perfil com propostas abertas."""
    con = init_db()
    agora = datetime.now().isoformat()
    rows = con.execute(
        """SELECT e.* FROM editais e JOIN editais_fts f ON f.rowid = e.id
           WHERE editais_fts MATCH ? AND e.data_encerramento >= ?
           ORDER BY e.data_encerramento LIMIT ?""",
        (perfil.fts_query_perfil(), agora, limite),
    ).fetchall()
    logger.info(f"{len(rows)} editais do perfil abertos")
    for i, r in enumerate(rows, 1):
        logger.info(f"[{i}/{len(rows)}] {r['numero_controle']}")
        baixar_documentos_de(con, r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ab = sub.add_parser("abertas", help="varre os últimos N dias (mais recente primeiro)")
    ab.add_argument("--janela", type=int, default=90, help="dias para trás (padrão 90)")
    ab.add_argument("--uf", help="filtrar por UF; omita para Brasil inteiro")
    b = sub.add_parser("backfill", help="carga completa por período de publicação")
    b.add_argument("--desde", required=True, help="YYYY-MM-DD")
    b.add_argument("--ate", default=date.today().isoformat(), help="YYYY-MM-DD (padrão: hoje)")
    b.add_argument("--uf", help="filtrar por UF (ex.: GO); omita para Brasil inteiro")
    sub.add_parser("sync", help="incremental: novos e atualizados desde a última execução")
    i = sub.add_parser("itens", help="baixa itens/arquivos dos editais do perfil abertos")
    i.add_argument("--limite", type=int, default=100)
    d = sub.add_parser("documentos", help="baixa os PDFs dos editais do perfil abertos")
    d.add_argument("--limite", type=int, default=50)
    args = ap.parse_args()
    try:
        if args.cmd == "abertas":
            abertas(args.janela, args.uf)
        elif args.cmd == "backfill":
            backfill(args.desde, args.ate, args.uf)
            logger.info("backfill concluído.")
        elif args.cmd == "sync":
            sync()
        elif args.cmd == "itens":
            baixar_itens(args.limite)
        else:
            baixar_documentos(args.limite)
    except KeyboardInterrupt:
        logger.info("interrompido — rode de novo para continuar de onde parou.")
