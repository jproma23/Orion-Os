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

# ## MEMORIA COGNITIVA (2026-08-09) - o que o ORION SABE e usa antes da IA.

# A IA da conversa nunca consultava o proprio acervo: as aulas so respondiam
# quando pediam "me da uma aula de", e a tabela `conhecimento` so tinha fatos
# triviais sobre a pessoa. Pergunta normal ("o que e um BME280?") caia no
# modelo, que inventava. Nascem 5 categorias que espelham como um aprendiz
# organiza o que aprendeu + um FTS5 (`saber_fts`) sobre o texto pesquisavel.

# - conhecimentos  : o que e, como funciona, como usar (conceito)
# - experiencias   : o que aconteceu, o que funcionou, o que falhou
# - procedimentos  : como executar uma tarefa, passo a passo
# - dispositivos   : hardware - quais existem, onde estao, como acessar
# - relacoes       : sensor -> funcao -> skill -> decisao (grafo)

# `tipo_fonte`/`ref_id` no FTS apontam para a linha exata da tabela certa;
# `bm25` ordena e `saber.buscar` confere a cobertura antes de responder.
_MIGRACAO_003 = """
CREATE TABLE IF NOT EXISTS conhecimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL DEFAULT 'conceito',
    titulo TEXT NOT NULL,
    corpo TEXT NOT NULL DEFAULT '',
    fonte TEXT NOT NULL DEFAULT '',
    confianca REAL NOT NULL DEFAULT 0.7,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiencias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    situacao TEXT NOT NULL,
    o_que_aconteceu TEXT NOT NULL DEFAULT '',
    o_que_funcionou TEXT NOT NULL DEFAULT '',
    o_que_falhou TEXT NOT NULL DEFAULT '',
    resultado TEXT NOT NULL DEFAULT '',
    contexto TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    passos TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispositivos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT '',
    localizacao TEXT NOT NULL DEFAULT '',
    acesso TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origem_tipo TEXT NOT NULL,
    origem_id INTEGER NOT NULL,
    relacao TEXT NOT NULL,
    destino_tipo TEXT NOT NULL,
    destino_id INTEGER NOT NULL,
    criado_em TEXT NOT NULL
);

-- Indice de busca do "Eu ja sei?" (mesmo FTS5 do saber.db: barato, sem
-- modelo de embedding). Removendo diacriticos nos dois sentidos como o
-- banco de aulas, para fala sem acento achar conteudo com acento.
CREATE VIRTUAL TABLE IF NOT EXISTS saber_fts USING fts5(
    tipo_fonte UNINDEXED, ref_id UNINDEXED, titulo, corpo,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE INDEX IF NOT EXISTS idx_conhecimentos_titulo ON conhecimentos (titulo);
CREATE INDEX IF NOT EXISTS idx_experiencias_situacao ON experiencias (situacao);
CREATE INDEX IF NOT EXISTS idx_procedimentos_nome ON procedimentos (nome);
CREATE INDEX IF NOT EXISTS idx_dispositivos_nome ON dispositivos (nome);
CREATE INDEX IF NOT EXISTS idx_relacoes_origem ON relacoes (origem_tipo, origem_id);
"""

#: RECADOS (2026-08-03). Nasceu de uma falha real, e vale registrar qual:
#: o dono pediu *"avise a Mara quando a vir que eu quero falar com ela"*, o
#: robo respondeu que sim, e quando a Mara chegou ele a cumprimentou e nao deu
#: o recado. A promessa era do MODELO, nao do sistema - nao havia nada em lugar
#: nenhum que guardasse aquilo. E a familia de falha que este projeto mais
#: repete: alguem publica no vazio e nada denuncia.
#:
#: POR QUE `para_nome` E NAO `pessoa_id`. Quem deixa o recado diz um NOME
#: ("a Mara"), e quem chega e reconhecido por NOME (`vision.person_recognized`
#: carrega `nome`). Exigir o id obrigaria a resolver a pessoa no instante do
#: pedido - e um recado para alguem ainda nao cadastrado simplesmente nao
#: poderia ser deixado, o que e pior que um recado guardado por nome.
#:
#: `entregue_em` NULL e o que significa "pendente". Apagar o registro na
#: entrega perderia a unica prova de que o robo cumpriu o combinado.
_MIGRACAO_002 = """
CREATE TABLE IF NOT EXISTS recados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    para_nome TEXT NOT NULL,
    de_nome TEXT,
    texto TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    entregue_em TEXT
);

-- O unico acesso quente: "ha recado pendente para quem acabou de chegar?".
-- Roda a cada chegada de pessoa, entao merece indice proprio.
CREATE INDEX IF NOT EXISTS idx_recados_pendentes ON recados (para_nome, entregue_em);
"""

#: VOZ como identidade (2026-08-19). O rosto (`embedding_face`) ja existe;
#: a coluna aqui da a MESMA identidade pela VOZ - o dono pediu o
#: reconhecimento de locutor ("quem esta falando") no nivel do reconhecimento
#: facial. O embedding vem do SpeechBrain ECAPA (192 floats32) e e
#: comparado por cosseno, espelhando o padrao do rosto (128 floats64 por
#: distancia euclidiana). Vazio = pessoa sem voiceprint cadastrada, e a
#: conversa dela volta a ser "da casa" (mesma regra do rosto ausente).
_MIGRACAO_004 = """
ALTER TABLE pessoas ADD COLUMN embedding_voz BLOB;
"""

MIGRACOES: tuple[Migracao, ...] = (
    (1, _MIGRACAO_001),
    (2, _MIGRACAO_002),
    (3, _MIGRACAO_003),
    (4, _MIGRACAO_004),
)

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
    "recados": "recados",
    "conhecimentos": "conhecimentos",
    "experiencias": "experiencias",
    "procedimentos": "procedimentos",
    "dispositivos": "dispositivos",
    "relacoes": "relacoes",
}
