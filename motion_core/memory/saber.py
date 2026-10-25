"""Memoria cognitiva do ORION (2026-08-09) - o que ele SABE e usa antes da IA.

## A dupla de fontes que este modulo cria

O robô sempre teve conhecimento (as aulas do saber.db, o ruído de fatos
triviais na tabela `conhecimento`) e a IA da conversa nunca o consultava -
o acervo so respondia quando pediam "me da uma aula de". Para perguntas
normais ("o que e um BME280?") o modelo era chamado e inventava.

Aqui nasce a terceira fonte: a memoria cognitiva estruturada, com 5
categorias que espelham como um aprendiz organiza o que aprendeu:

- `conhecimentos`  - o que e, como funciona, como usar (conceito enciclopedico)
- `experiencias`   - o que aconteceu, o que funcionou, o que falhou
- `procedimentos`  - como executar uma tarefa passo a passo
- `dispositivos`   - hardware: quais existem, onde estao, como acessar
- `relacoes`       - sensor -> funcao -> skill -> decisao

Mais um indice FTS5 (`saber_fts`) sobre o texto pesquisavel de tudo isso.
E FTS5 e nao vetorial pelo mesmo motivo de sempre neste projeto: sao
dezenas de entradas e o Notebook nao tem RAM para modelo de embedding.

## Contrato

Este modulo nao abre banco: recebe a conexao ja aberta pelo
DatabaseManager e a usa dentro do lock compartilhado da MemoryAPI (e o
mesmo arranjo do VaultConhecimento com o SSD). Quem orquestra, publica
eventos e conta `consultas_ia` e a MemoryAPI; aqui mora a logica pura de
gravar, buscar e conferir cobertura.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

#: Onde o FTS5 procura, por categoria de origem do registro.
#:
#: `dispositivos` e `relacoes` nao entram: o primeiro tem pouco texto
#: (nome/alvo) e o segundo e um grafo, que se consulta pelos extremos, nao
#: por palavras. Espelha a regra do projeto de nao buscar onde nao ha o que
#: achar.
CAMPOS_FTS: dict[str, tuple[str, str]] = {
    "conhecimentos": ("titulo", "corpo"),
    "experiencias": ("situacao", "o_que_aconteceu"),
    "procedimentos": ("nome", "passos"),
}

FTS_POR_CATEGORIA: dict[str, str] = {
    "conhecimentos": "conhecimento",
    "experiencias": "experiencia",
    "procedimentos": "procedimento",
}

#: bm25 em banco pequeno fica proximo de zero e um teto absoluto rejeitaria
#: tudo - a guarda de verdade aqui e a COBERTURA (>=2 palavras de conteudo da
#: fala no texto do registro). O bm25 so ORDENA; a cobertura decide.
_PESOS_BM25 = "0.0, 0.0, 3.0, 1.0"  # titulo vale 3x o corpo

#: Uma fala precisa de ao menos isto de texto pesquisavel para a busca valer
#: uma chamada. "O que e?" nao tem palavra util; "o que e um BME280?" tem UMA
#: (bme280) e vale procurar - a cobertura ainda guarda (1 termo exige 1).
_MINIMO_TERMOS = 1

#: Palavras que aparecem em QUALQUER fala e nao discriminam conteudo. Curta
#: de proposito, igual ao vault: lista grande comeca a derrubar palavra util.
#
# Verbos de ACAO/comando entram aqui de proposito (2026-08-16): "olha",
# "mexe", "liga" etc. sao pedidos para o robo FAZER algo, nao temas de
# conhecimento. Sem eles, "der uma olha" virava termo "olha", casava com
# qualquer registro que contivesse a palavra no corpo, e o "Eu ja sei?"
# despejava texto cru fora de contexto (visto: "der uma olha" respondeu
# "Pensamento sistemico do dono").
_PALAVRAS_VAZIAS = frozenset(
    {
        "para", "pelo", "pela", "como", "quando", "onde", "isso", "isto",
        "aquilo", "esse", "essa", "este", "esta", "voce", "vocie", "jarvis",
        "meu", "minha", "seu", "sua", "nosso", "nossa", "sobre", "entao",
        "mais", "menos", "muito", "pouco", "aqui", "sabe", "tem", "esta",
        "estao", "sera", "foi", "era", "sao", "com", "sem", "dos", "das",
        "nos", "nas", "num", "numa", "pra", "por", "que", "mas", "porque",
        "nao", "quero", "fazer", "existe", "existem", "acha", "acha que",
        # Verbos de acao/comando: nao sao temas de conhecimento.
        "olha", "olhar", "olhe", "ve", "ver", "viu", "vai", "anda", "andar",
        "move", "mover", "mexe", "mexer", "liga", "ligar", "desliga",
        "desligar", "acende", "apaga", "gira", "girar", "para", "parar",
        "continua", "mostra", "mostrar", "fala", "falar", "conta", "contar",
        "diz", "dizer", "faz", "checa", "checar", "verifica", "verificar",
    }
)


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def _termos(texto: str) -> list[str]:
    """A fala vira as palavras que valem procurar, sem repeticao."""
    vistos: list[str] = []
    for palavra in re.findall(r"[a-z0-9]+", _sem_acento(texto)):
        if len(palavra) < 3 or palavra in _PALAVRAS_VAZIAS:
            continue
        if palavra not in vistos:
            vistos.append(palavra)
    return vistos


def _consulta_fts(consulta: str) -> str | None:
    """Termos da fala -> sintaxe FTS5 de prefixo (`bme* OR 280*`)."""
    termos = _termos(consulta)
    if len(termos) < _MINIMO_TERMOS:
        return None
    return " OR ".join(f"{t}*" for t in termos[:6])  # teto: 6 termos cobrem uma frase


def _cobertura_ok(consulta: str, titulo: str, corpo: str) -> bool:
    """O texto do registro cobre a fala o bastante para valer responder?

    Mesma regra do autoconhecimento (2026-08-08): o fato vencedor precisa
    cobrir pelo menos 2 palavras de conteudo da frase (ou 1, se a frase so
    tiver 1), senao uma palavra generica faz qualquer coisa encostar em
    qualquer outra. Uma palavra encosta; duas apontam um assunto.
    """
    palavras = _termos(consulta)
    if not palavras:
        return False
    alvo = set(re.findall(r"[a-z0-9]+", _sem_acento(f"{titulo} {corpo}")))
    cobertas = sum(1 for p in palavras if _palavra_coberta(p, alvo))
    return cobertas >= 1 if len(palavras) == 1 else cobertas >= 2


#: Quantas letras a palavra do registro pode ter A MAIS que a da fala e ainda
#: contar como a mesma: plural e genero ("sensor"/"sensores", "lampada"/
#: "lampadas"). Tres ja e outra palavra ("dia"/"diario").
_SUFIXO_TOLERADO = 2


def _palavra_coberta(palavra: str, alvo: set[str]) -> bool:
    """A palavra da fala aparece INTEIRA no registro (com folga de plural).

    Ate 2026-10-25 isto era `palavra in texto`, que acha PEDACO de palavra -
    e a conversa comum passou a ser respondida com fichas do codigo:
    "Bom dia! Como voce esta hoje?" cobria "dia" (dentro de "diario") e
    "hoje" (dentro de "observacoesdehoje") e respondia com o diario.py;
    "qual e o seu nome" achava "nome" dentro de "mesmonome". Sem palavra de
    ativacao, TODA fala passa por aqui antes da IA.
    """
    return any(
        w == palavra or (w.startswith(palavra) and len(w) - len(palavra) <= _SUFIXO_TOLERADO)
        for w in alvo
    )


def sincronizar_fts(
    conexao: sqlite3.Connection, categoria: str, ref_id: int, dados: dict[str, Any]
) -> None:
    """(Re)indexa o texto de um registro no FTS, somem primeira para nao
    duplicar a linha do mesmo `(categoria, ref_id)` numa atualizacao."""
    campos = CAMPOS_FTS.get(categoria)
    if campos is None:
        return  # categoria sem FTS (dispositivos, relacoes)
    titulo_col, corpo_col = campos
    conexao.execute(
        "DELETE FROM saber_fts WHERE tipo_fonte = ? AND ref_id = ?",
        (FTS_POR_CATEGORIA[categoria], ref_id),
    )
    conexao.execute(
        "INSERT INTO saber_fts (tipo_fonte, ref_id, titulo, corpo) VALUES (?, ?, ?, ?)",
        (
            FTS_POR_CATEGORIA[categoria],
            ref_id,
            str(dados.get(titulo_col, "") or ""),
            str(dados.get(corpo_col, "") or ""),
        ),
    )


def remover_fts(conexao: sqlite3.Connection, categoria: str, ref_id: int) -> None:
    if categoria not in CAMPOS_FTS:
        return
    conexao.execute(
        "DELETE FROM saber_fts WHERE tipo_fonte = ? AND ref_id = ?",
        (FTS_POR_CATEGORIA[categoria], ref_id),
    )


def buscar(
    conexao: sqlite3.Connection, consulta: str, limite: int = 3
) -> list[dict[str, Any]]:
    """Acha registros de conhecimento que cobrem a fala. Vazio = nao ha.

    Devolve o enunciado indexado (titulo, corpo, tipo_fonte, ref_id) com a
    nota bm25. A cobertura e conferida AQUI, no melhor candidato - o FTS
    acha, a cobertura decide se vale responder.
    """
    fts = _consulta_fts(consulta)
    if fts is None:
        return []
    try:
        linhas = conexao.execute(
            "SELECT tipo_fonte, ref_id, titulo, corpo, "
            f"bm25(saber_fts, {_PESOS_BM25}) AS nota "
            "FROM saber_fts WHERE saber_fts MATCH ? ORDER BY nota LIMIT ?",
            (fts, limite),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 rejeita operador solto que a fala produziu - mesmo tratamento
        # do banco de aulas: melhor nada do que falhar a conversa.
        return []

    adequados = [
        dict(linha)
        for linha in linhas
        if _cobertura_ok(consulta, str(linha["titulo"] or ""), str(linha["corpo"] or ""))
    ]
    return adequados  # a cobertura ja e a guarda; a nota so ordenou


#: Colunas de texto de cada categoria, para `carregar` normalizar o achado em
#: `titulo`/`corpo` - o consumidor (o "Eu ja sei?" da conversa) nao pode
#: precisar saber que procedimento se chama `nome` e nao `titulo`.
COLUNAS_TEXTO: dict[str, tuple[str, str]] = {
    "conhecimentos": ("titulo", "corpo"),
    "experiencias": ("situacao", "o_que_aconteceu"),
    "procedimentos": ("nome", "passos"),
    "dispositivos": ("nome", "tipo"),
    "relacoes": ("relacao", ""),
}


def carregar(
    conexao: sqlite3.Connection, tipo_fonte: str, ref_id: int
) -> dict[str, Any] | None:
    """Pega o registro COMPLETO da tabela certa a partir do achado do FTS,
    normalizado em `titulo`/`corpo` para quem consumir."""
    por_tipo = {v: k for k, v in FTS_POR_CATEGORIA.items()}
    categoria = por_tipo.get(tipo_fonte)
    if categoria is None:
        return None
    linha = conexao.execute(
        f"SELECT * FROM {categoria} WHERE id = ?", (ref_id,)
    ).fetchone()
    if linha is None:
        return None
    registro = dict(linha)
    col_titulo, col_corpo = COLUNAS_TEXTO.get(categoria, ("titulo", "corpo"))
    return {
        "id": registro["id"],
        "categoria": categoria,
        "titulo": registro.get(col_titulo) or registro.get("titulo") or "",
        "corpo": registro.get(col_corpo) or registro.get("corpo") or "",
        "fonte": registro.get("fonte", ""),
        "confianca": registro.get("confianca", 0.7),
        "criado_em": registro.get("criado_em"),
    }


def contar(conexao: sqlite3.Connection) -> dict[str, int]:
    """Contagem por categoria - o alimentador do indicador CONHECIMENTO ORION."""
    contagens: dict[str, int] = {}
    for tabela in (
        "conhecimentos",
        "experiencias",
        "procedimentos",
        "dispositivos",
        "relacoes",
    ):
        try:
            contagens[tabela] = conexao.execute(
                f"SELECT COUNT(*) FROM {tabela}"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            contagens[tabela] = 0
    return contagens