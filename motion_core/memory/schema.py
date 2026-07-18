"""Schema SQL do banco (Cap 15 secoes 3-4; Cap 11 secao 5).

Cada migracao e uma versao numerada, aplicada uma unica vez e registrada em
`schema_version`. Uma migracao ja aplicada em producao nunca deve ser
editada - mudancas de schema viram uma NOVA migracao no final da lista.
"""
from __future__ import annotations

Migracao = tuple[int, str]

_MIGRACAO_001 = """
CREATE TABLE IF NOT EXISTS schema_version (
    versao INTEGER PRIMARY KEY,
    aplicado_em TEXT NOT NULL
);

-- Identidade e memoria (Cap 11)
CREATE TABLE IF NOT EXISTS pessoas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    autorizacao TEXT NOT NULL DEFAULT 'nenhuma',
    embedding_face BLOB,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ambientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    descricao TEXT,
    assinatura_visual BLOB,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS objetos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    classe TEXT NOT NULL,
    descricao TEXT,
    ambiente_id INTEGER REFERENCES ambientes(id),
    ultima_posicao TEXT,
    visto_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conhecimento (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL,
    valor TEXT NOT NULL,
    fonte TEXT,
    confianca REAL NOT NULL DEFAULT 1.0,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pessoa_id INTEGER REFERENCES pessoas(id),
    papel TEXT NOT NULL,
    texto TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracao_memoria (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- Operacao (Cap 15)
CREATE TABLE IF NOT EXISTS missoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    parametros_json TEXT,
    estado TEXT NOT NULL,
    resultado TEXT,
    origem TEXT,
    inicio TEXT NOT NULL,
    fim TEXT
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origem TEXT NOT NULL,
    tipo TEXT NOT NULL,
    payload_json TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origem TEXT NOT NULL,
    metrica TEXT NOT NULL,
    valor REAL NOT NULL,
    timestamp TEXT NOT NULL
);

-- Sistema (Cap 15)
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nivel TEXT NOT NULL,
    origem TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    contexto_json TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnosticos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    modulo TEXT NOT NULL,
    teste TEXT NOT NULL,
    resultado TEXT NOT NULL,
    detalhes TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracao (
    chave TEXT NOT NULL,
    valor TEXT NOT NULL,
    perfil TEXT NOT NULL DEFAULT 'HOME',
    atualizado_em TEXT NOT NULL,
    PRIMARY KEY (chave, perfil)
);

-- Indices minimos obrigatorios (Cap 15 s.4)
CREATE INDEX IF NOT EXISTS idx_eventos_timestamp ON eventos (timestamp);
CREATE INDEX IF NOT EXISTS idx_eventos_origem_tipo ON eventos (origem, tipo);
CREATE INDEX IF NOT EXISTS idx_telemetria_origem_metrica_timestamp
    ON telemetria (origem, metrica, timestamp);
CREATE INDEX IF NOT EXISTS idx_missoes_estado ON missoes (estado);
CREATE INDEX IF NOT EXISTS idx_missoes_inicio ON missoes (inicio);
CREATE INDEX IF NOT EXISTS idx_conversas_pessoa_timestamp ON conversas (pessoa_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_nivel_timestamp ON logs (nivel, timestamp);
CREATE INDEX IF NOT EXISTS idx_objetos_ambiente ON objetos (ambiente_id);
"""

MIGRACOES: tuple[Migracao, ...] = ((1, _MIGRACAO_001),)

#: Tabelas mapeadas por categoria da API de memoria (Cap 11 s.6) - usadas
#: por MemoryAPI para traduzir `categoria` em nome de tabela sem expor SQL
#: cru aos chamadores.
TABELAS_POR_CATEGORIA: dict[str, str] = {
    "pessoas": "pessoas",
    "ambientes": "ambientes",
    "objetos": "objetos",
    "missoes": "missoes",
    "eventos": "eventos",
    "conversas": "conversas",
    "conhecimento": "conhecimento",
    "configuracao_memoria": "configuracao_memoria",
}
