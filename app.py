#!/usr/bin/env python3
"""Interface web do pncp-analyzer. Rode: python app.py (http://localhost:5000)"""
import json
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_from_directory
from loguru import logger

import db
import perfil
import scraper

app = Flask(__name__)
db.init_db().close()

MODALIDADES = db.MODALIDADES_NOMES
# tipoBeneficio dos itens: 1 = exclusivo ME/EPP, 3 = cota reservada ME/EPP
BENEFICIOS_ME_EPP = {1, 3}


@app.route("/")
def index():
    return render_template("index.html", modalidades=MODALIDADES,
                           perfil_termos=perfil.TERMOS)


@app.get("/api/busca")
def busca():
    con = db.get_db()
    a = request.args
    where, params = [], []

    fts = []
    if a.get("perfil") == "1":
        fts.append(f"({perfil.fts_query_perfil()})")
    if a.get("q", "").strip():
        fts.append(f"({perfil.fts_query_usuario(a['q'].strip())})")
    if fts:
        where.append("e.id IN (SELECT rowid FROM editais_fts WHERE editais_fts MATCH ?)")
        params.append(" AND ".join(fts))
    if a.get("uf"):
        where.append("e.uf = ?")
        params.append(a["uf"])
    if a.get("modalidade"):
        where.append("e.modalidade_id = ?")
        params.append(int(a["modalidade"]))
    agora = datetime.now().isoformat()
    if a.get("status") == "abertas":
        where.append("e.data_encerramento >= ?")
        params.append(agora)
    elif a.get("status") == "encerradas":
        where.append("(e.data_encerramento < ? OR e.data_encerramento IS NULL)")
        params.append(agora)
    if a.get("valor_min"):
        where.append("e.valor_estimado >= ?")
        params.append(float(a["valor_min"]))
    if a.get("valor_max"):
        where.append("e.valor_estimado <= ?")
        params.append(float(a["valor_max"]))
    if a.get("srp") == "1":
        where.append("e.srp = 1")

    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    ordem = {
        "encerramento": "e.data_encerramento ASC",
        "valor": "e.valor_estimado DESC",
        "publicacao": "e.data_publicacao DESC",
    }.get(a.get("ordem", "publicacao"), "e.data_publicacao DESC")

    total = con.execute(f"SELECT COUNT(*) c FROM editais e {sql_where}", params).fetchone()["c"]
    pagina = max(1, int(a.get("pagina", 1)))
    rows = con.execute(
        f"""SELECT e.numero_controle, e.cnpj, e.ano, e.sequencial, e.objeto, e.orgao,
                   e.municipio, e.uf, e.modalidade, e.situacao, e.srp, e.valor_estimado,
                   e.data_publicacao, e.data_encerramento
            FROM editais e {sql_where} ORDER BY {ordem} NULLS LAST
            LIMIT 50 OFFSET ?""",
        params + [(pagina - 1) * 50],
    ).fetchall()
    return jsonify(total=total, pagina=pagina, paginas=(total + 49) // 50,
                   resultados=[dict(r) for r in rows])


@app.get("/api/edital")
def edital():
    nc = request.args.get("nc", "")
    con = db.get_db()
    row = con.execute("SELECT * FROM editais WHERE numero_controle = ?", (nc,)).fetchone()
    if not row:
        return jsonify(erro="não encontrado"), 404
    d = dict(row)
    d["raw"] = json.loads(d["raw"], strict=False)
    if d["itens"] is None:
        try:
            itens, arquivos = scraper.fetch_detalhe(con, row["id"], row["cnpj"],
                                                    row["ano"], row["sequencial"])
            d["itens"], d["arquivos"] = itens, arquivos
        except Exception as e:
            logger.exception(f"falha ao buscar itens/arquivos de {nc} no PNCP")
            d["itens"], d["arquivos"] = [], []
            d["erro_detalhe"] = f"falha ao buscar itens no PNCP: {e}"
    else:
        d["itens"] = json.loads(d["itens"], strict=False)
        d["arquivos"] = json.loads(d["arquivos"] or "[]", strict=False)
    d["me_epp"] = any(i.get("tipoBeneficio") in BENEFICIOS_ME_EPP for i in d["itens"])
    pasta = scraper.pasta_docs(nc)
    locais = {f.name.split("_", 1)[0].lstrip("0"): f.name for f in pasta.glob("*")} if pasta.is_dir() else {}
    for arq in d["arquivos"]:
        nome = locais.get(str(arq.get("sequencialDocumento", "")))
        arq["local"] = f"/docs/{pasta.name}/{nome}" if nome else None
    return jsonify(d)


@app.get("/docs/<pasta>/<nome>")
def docs(pasta, nome):
    return send_from_directory(scraper.DOCS_DIR / pasta, nome)


@app.get("/api/stats")
def stats():
    con = db.get_db()
    agora = datetime.now().isoformat()
    return jsonify(
        total=con.execute("SELECT COUNT(*) c FROM editais").fetchone()["c"],
        abertas=con.execute("SELECT COUNT(*) c FROM editais WHERE data_encerramento >= ?",
                            (agora,)).fetchone()["c"],
        ultima_sync=db.meta_get(con, "ultima_sync"),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
