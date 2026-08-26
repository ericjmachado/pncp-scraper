import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pncp.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS editais (
    id INTEGER PRIMARY KEY,
    numero_controle TEXT UNIQUE NOT NULL,
    cnpj TEXT, ano INTEGER, sequencial INTEGER,
    objeto TEXT, orgao TEXT, unidade TEXT, municipio TEXT, uf TEXT,
    esfera TEXT, poder TEXT,
    modalidade_id INTEGER, modalidade TEXT,
    situacao TEXT, srp INTEGER,
    valor_estimado REAL, valor_homologado REAL,
    data_publicacao TEXT, data_abertura TEXT, data_encerramento TEXT,
    data_atualizacao TEXT,
    link_origem TEXT,
    raw TEXT NOT NULL,
    itens TEXT, arquivos TEXT, itens_atualizado_em TEXT
);
CREATE INDEX IF NOT EXISTS idx_editais_uf ON editais(uf);
CREATE INDEX IF NOT EXISTS idx_editais_modalidade ON editais(modalidade_id);
CREATE INDEX IF NOT EXISTS idx_editais_encerramento ON editais(data_encerramento);
CREATE INDEX IF NOT EXISTS idx_editais_publicacao ON editais(data_publicacao);

CREATE VIRTUAL TABLE IF NOT EXISTS editais_fts USING fts5(
    objeto, orgao, unidade, municipio,
    content='editais', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS editais_ai AFTER INSERT ON editais BEGIN
    INSERT INTO editais_fts(rowid, objeto, orgao, unidade, municipio)
    VALUES (new.id, new.objeto, new.orgao, new.unidade, new.municipio);
END;
CREATE TRIGGER IF NOT EXISTS editais_ad AFTER DELETE ON editais BEGIN
    INSERT INTO editais_fts(editais_fts, rowid, objeto, orgao, unidade, municipio)
    VALUES ('delete', old.id, old.objeto, old.orgao, old.unidade, old.municipio);
END;
CREATE TRIGGER IF NOT EXISTS editais_au AFTER UPDATE OF objeto, orgao, unidade, municipio ON editais BEGIN
    INSERT INTO editais_fts(editais_fts, rowid, objeto, orgao, unidade, municipio)
    VALUES ('delete', old.id, old.objeto, old.orgao, old.unidade, old.municipio);
    INSERT INTO editais_fts(rowid, objeto, orgao, unidade, municipio)
    VALUES (new.id, new.objeto, new.orgao, new.unidade, new.municipio);
END;

CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
"""


def get_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db():
    con = get_db()
    con.executescript(SCHEMA)
    con.commit()
    return con


def upsert_edital(con, r):
    """r = dict retornado pela API de consulta (RecuperarCompraPublicacaoDTO)."""
    orgao = r.get("orgaoEntidade") or {}
    unidade = r.get("unidadeOrgao") or {}
    con.execute(
        """
        INSERT INTO editais (numero_controle, cnpj, ano, sequencial, objeto, orgao,
            unidade, municipio, uf, esfera, poder, modalidade_id, modalidade, situacao,
            srp, valor_estimado, valor_homologado, data_publicacao, data_abertura,
            data_encerramento, data_atualizacao, link_origem, raw)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(numero_controle) DO UPDATE SET
            objeto=excluded.objeto, orgao=excluded.orgao, unidade=excluded.unidade,
            municipio=excluded.municipio, uf=excluded.uf, esfera=excluded.esfera,
            poder=excluded.poder, modalidade_id=excluded.modalidade_id,
            modalidade=excluded.modalidade, situacao=excluded.situacao, srp=excluded.srp,
            valor_estimado=excluded.valor_estimado, valor_homologado=excluded.valor_homologado,
            data_publicacao=excluded.data_publicacao, data_abertura=excluded.data_abertura,
            data_encerramento=excluded.data_encerramento, data_atualizacao=excluded.data_atualizacao,
            link_origem=excluded.link_origem, raw=excluded.raw
        """,
        (
            r["numeroControlePNCP"],
            orgao.get("cnpj"),
            r.get("anoCompra"),
            r.get("sequencialCompra"),
            r.get("objetoCompra"),
            orgao.get("razaoSocial"),
            unidade.get("nomeUnidade"),
            unidade.get("municipioNome"),
            unidade.get("ufSigla"),
            orgao.get("esferaId"),
            orgao.get("poderId"),
            r.get("modalidadeId"),
            r.get("modalidadeNome"),
            r.get("situacaoCompraNome"),
            1 if r.get("srp") else 0,
            r.get("valorTotalEstimado"),
            r.get("valorTotalHomologado"),
            r.get("dataPublicacaoPncp"),
            r.get("dataAberturaProposta"),
            r.get("dataEncerramentoProposta"),
            r.get("dataAtualizacaoGlobal"),
            r.get("linkSistemaOrigem"),
            json.dumps(r, ensure_ascii=False),
        ),
    )


def meta_get(con, chave, default=None):
    row = con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
    return row["valor"] if row else default


def meta_set(con, chave, valor):
    con.execute(
        "INSERT INTO meta (chave, valor) VALUES (?,?) "
        "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
        (chave, str(valor)),
    )
