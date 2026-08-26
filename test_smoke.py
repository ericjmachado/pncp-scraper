"""Teste de fumaça sem rede: python test_smoke.py"""

import db, perfil

REGISTRO = {
    "numeroControlePNCP": "00000000000000-1-000001/2026",
    "anoCompra": 2026,
    "sequencialCompra": 1,
    "objetoCompra": "Contratação de desenvolvimento de software de gestão",
    "orgaoEntidade": {
        "cnpj": "00000000000000",
        "razaoSocial": "ÓRGÃO TESTE",
        "esferaId": "M",
        "poderId": "E",
    },
    "unidadeOrgao": {
        "nomeUnidade": "UNIDADE",
        "municipioNome": "Goiânia",
        "ufSigla": "GO",
    },
    "modalidadeId": 6,
    "modalidadeNome": "Pregão - Eletrônico",
    "situacaoCompraNome": "Divulgada no PNCP",
    "srp": False,
    "valorTotalEstimado": 100000.0,
    "valorTotalHomologado": None,
    "dataPublicacaoPncp": "2026-01-01T00:00:00",
    "dataAberturaProposta": "2026-01-02T00:00:00",
    "dataEncerramentoProposta": "2099-01-01T00:00:00",
    "dataAtualizacaoGlobal": "2026-01-01T00:00:00",
    "linkSistemaOrigem": None,
}

db.DB_PATH = db.Path(":memory:")
con = db.init_db()
db.upsert_edital(con, REGISTRO)
db.upsert_edital(con, REGISTRO)  # idempotente
assert con.execute("SELECT COUNT(*) FROM editais").fetchone()[0] == 1

# busca do perfil acha (sem acento, via FTS)
hits = con.execute(
    "SELECT COUNT(*) FROM editais_fts WHERE editais_fts MATCH ?",
    (perfil.fts_query_perfil(),),
).fetchone()[0]
assert hits == 1, hits

# busca do usuário com prefixo e caracteres perigosos não explode
for q in ("desenv gestao", 'aspas "duplas" OR', "goiania"):
    con.execute(
        "SELECT COUNT(*) FROM editais_fts WHERE editais_fts MATCH ?",
        (perfil.fts_query_usuario(q),),
    ).fetchone()

db.meta_set(con, "x", "1")
assert db.meta_get(con, "x") == "1"
print("ok")
