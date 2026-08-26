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
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import perfil
from db import init_db, meta_get, meta_set, upsert_edital

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
        try:
            resp = session.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            print(f"    erro de rede ({e}), tentando de novo...")
            time.sleep(5 * (tentativa + 1))
            continue
        if resp.status_code in (204, 404):
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            espera = min(5 * 2**tentativa, 120)
            print(f"    HTTP {resp.status_code}, aguardando {espera}s...")
            time.sleep(espera)
            continue
        resp.raise_for_status()
        # strict=False: o PNCP devolve caracteres de controle não escapados (ex.: Dispensa)
        return json.loads(resp.text, strict=False)
    raise RuntimeError(f"desisti após 9 tentativas: {url}")


def _paginar(con, url, params, rotulo):
    pagina = params["pagina"]
    while True:
        params["pagina"] = pagina
        d = get_json(url, params)
        if not d or not d.get("data"):
            break
        for r in d["data"]:
            upsert_edital(con, r)
        con.commit()
        print(f"{rotulo}: página {pagina}/{d['totalPaginas']} ({d['totalRegistros']} registros)")
        yield pagina
        if pagina >= d["totalPaginas"]:
            break
        pagina += 1


def backfill(desde, ate, uf=None):
    con = init_db()
    d1, d2 = desde.replace("-", ""), ate.replace("-", "")
    for m in MODALIDADES:
        chave = f"backfill:{d1}:{d2}:{uf or 'BR'}:{m}"
        estado = meta_get(con, chave)
        if estado == "fim":
            print(f"modalidade {m}: já concluída, pulando")
            continue
        params = dict(dataInicial=d1, dataFinal=d2, codigoModalidadeContratacao=m,
                      pagina=int(estado or 1), tamanhoPagina=PAGINA_TAM)
        if uf:
            params["uf"] = uf
        for pag in _paginar(con, f"{CONSULTA}/v1/contratacoes/publicacao", params, f"modalidade {m}"):
            meta_set(con, chave, pag + 1)
            con.commit()
        meta_set(con, chave, "fim")
        con.commit()
        print(f"modalidade {m}: concluída")


def abertas(janela, uf=None):
    """Varre os últimos `janela` dias em blocos de 7, do mais recente pro mais antigo.

    O endpoint /contratacoes/proposta (só abertos) responde 504 no PNCP; varrer a
    publicação recente cobre a mesma coisa — a interface filtra pelo prazo.
    """
    fim = date.today()
    while janela > 0:
        ini = fim - timedelta(days=min(7, janela) - 1)
        print(f"== bloco {ini} a {fim} ==")
        backfill(ini.isoformat(), fim.isoformat(), uf)
        janela -= 7
        fim = ini - timedelta(days=1)
    print("varredura de abertos concluída.")


def sync():
    con = init_db()
    hoje = date.today()
    ultima = meta_get(con, "ultima_sync")
    desde = date.fromisoformat(ultima) - timedelta(days=1) if ultima else hoje - timedelta(days=7)
    d1, d2 = desde.strftime("%Y%m%d"), hoje.strftime("%Y%m%d")
    print(f"sync de {desde} até {hoje} (novos + atualizados)")
    for m in MODALIDADES:
        params = dict(dataInicial=d1, dataFinal=d2, codigoModalidadeContratacao=m,
                      pagina=1, tamanhoPagina=PAGINA_TAM)
        for _ in _paginar(con, f"{CONSULTA}/v1/contratacoes/atualizacao", params, f"modalidade {m}"):
            pass
    meta_set(con, "ultima_sync", hoje.isoformat())
    con.commit()
    print("sync concluído.")


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
    print(f"{len(rows)} editais do perfil sem itens")
    for i, r in enumerate(rows, 1):
        fetch_detalhe(con, r["id"], r["cnpj"], r["ano"], r["sequencial"])
        print(f"[{i}/{len(rows)}] {r['numero_controle']}")


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
            print(f"    arquivo {seq}: HTTP {resp.status_code}, pulando")
            continue
        cd = resp.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)', cd)
        nome_orig = m.group(1) if m else (a.get("titulo") or f"doc{seq}")
        nome = re.sub(r'[^\w.\-]+', "_", nome_orig)[:120]
        (pasta / f"{seq:03d}_{nome}").write_bytes(resp.content)
        print(f"    {seq:03d}_{nome} ({len(resp.content) // 1024} KB)")


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
    print(f"{len(rows)} editais do perfil abertos")
    for i, r in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {r['numero_controle']}")
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
            print("backfill concluído.")
        elif args.cmd == "sync":
            sync()
        elif args.cmd == "itens":
            baixar_itens(args.limite)
        else:
            baixar_documentos(args.limite)
    except KeyboardInterrupt:
        print("\ninterrompido — rode de novo para continuar de onde parou.")
