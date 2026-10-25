"""Mission Memory: a memoria do PROJETO sobre si mesmo.

Responde "ja vimos esse erro antes?", "qual commit resolveu isso?", "que
modulo costuma causar esse tipo de problema?" - lendo o que o projeto ja
escreve a cada correcao: o journal, os corpos de commit e os EDRs.

NAO e a memoria do robo sobre as pessoas (essa e o `conhecimento`/`conversas`
no orion.db). Esta aqui e ferramenta de bancada.

POR QUE FTS5 E NAO BANCO VETORIAL (decidido 2026-08-06, com numero): todo o
corpus curado do projeto da menos de 1 MB - 94 sessoes de journal, ~310
commits, 11 EDRs. Qdrant/Milvus/FAISS existem para milhoes de documentos;
aqui somariam um servidor e um modelo de embedding na RAM de um Notebook com
3 GB livres, que ja roda YOLO, Whisper e reconhecimento facial. O FTS5 e
busca de texto embutida no SQLite: zero processo novo, zero RAM nova. Se um
dia nao bastar, `sqlite-vec` entra no MESMO arquivo, sem servidor.

POR QUE UM ARQUIVO SEPARADO, e nao o `orion.db`: este indice e ARTEFATO
DERIVADO - da para joga-lo fora e reconstruir do repositorio em segundos. O
`orion.db` guarda o que nao se recupera: rostos, conversas, recados. Misturar
as duas coisas poria um cache no caminho do backup e faria a Mission Memory
escrever no banco de producao com o robo rodando. Se este arquivo sumir, o
conserto e `construir` de novo.

USO:

    python tools/mission_memory.py construir
    python tools/mission_memory.py buscar "reconexao serial"
    python tools/mission_memory.py estado
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Expoe motion_core/ quando este arquivo roda como SCRIPT: ai o sys.path
# tem tools/, nao a raiz. Mesmo padrao de tools/conversar_jarvis.py. Sob
# pytest isso ja vem do `pythonpath` do pyproject, e a linha e inofensiva.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from motion_core.memory import projeto  # noqa: E402
from motion_core.memory.projeto import buscar, estado  # noqa: E402

#: Fica no SSD junto do resto, mas em arquivo proprio - ver o cabecalho.
#: A LEITURA mora em motion_core/memory/projeto.py - o robo consulta
#: este indice em operacao, e runtime nao pode depender de tools/.
#: Aqui fica so a CONSTRUCAO, que e de bancada.
CAMINHO_PADRAO = projeto.CAMINHO_PADRAO

TOKENIZADOR = projeto.TOKENIZADOR

PESOS_BM25 = projeto.PESOS_BM25

_CABECALHO_SESSAO = re.compile(r"^## +(.*)$")
_DATA_NO_TEXTO = re.compile(r"(\d{4}-\d{2}-\d{2})")

#: Separadores para fatiar a saida do git sem depender de quebra de linha -
#: corpo de commit tem linha em branco, titulo tem acento, e mensagem deste
#: projeto tem ate tabela ASCII dentro.
#:
#: NAO usar `\x00` aqui, por mais natural que pareca: o formato vai como
#: ARGUMENTO de processo, e argumento e terminado em nulo - o subprocess
#: recusa com `ValueError: embedded null byte` antes de o git rodar.
#: Mordeu na primeira execucao dos testes (06/08). Os controles 0x1e/0x1f
#: (separador de registro e de campo do ASCII) fazem o mesmo servico e
#: passam por argv.
_FIM_REGISTRO = "\x1e"
_FIM_CAMPO = "\x1f"


@dataclass
class Documento:
    tipo: str  # 'journal' | 'commit' | 'edr'
    ref: str  # hash curto, data da sessao, ou nome do arquivo
    data: str  # ISO ou vazio
    titulo: str
    texto: str


def _abrir(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(caminho)


def _criar_esquema(con: sqlite3.Connection) -> None:
    """Uma tabela so, FTS5 pura.

    Nao se usa `content=` (tabela externa) de proposito: ela economiza
    espaco a troco de gatilhos de sincronia, e aqui o indice e reconstruido
    inteiro a cada vez. Um cache de 1 MB nao precisa de economia que custa
    complexidade - e sincronia mal feita e como este projeto ja perdeu
    tempo antes.
    """
    con.execute("DROP TABLE IF EXISTS documentos")
    con.execute(
        f"""
        CREATE VIRTUAL TABLE documentos USING fts5(
            tipo UNINDEXED,
            ref UNINDEXED,
            data UNINDEXED,
            titulo,
            texto,
            tokenize='{TOKENIZADOR}'
        )
        """
    )


def _data_de(texto: str) -> str:
    achado = _DATA_NO_TEXTO.search(texto)
    return achado.group(1) if achado else ""


def ler_journal(raiz: Path) -> list[Documento]:
    """Fatia o journal por sessao (`## `).

    A sessao e a unidade certa: e o que tem sintoma, causa, correcao e
    medicao juntos. Fatiar por paragrafo devolveria o sintoma sem a
    correcao, que e pior que nao achar nada.
    """
    caminho = raiz / "docs/journal.md"
    if not caminho.exists():
        return []

    docs: list[Documento] = []
    titulo_atual: str | None = None
    linhas_atuais: list[str] = []

    def _fechar() -> None:
        if titulo_atual is None:
            return
        corpo = "\n".join(linhas_atuais).strip()
        if not corpo:
            return
        docs.append(
            Documento(
                tipo="journal",
                ref=_data_de(titulo_atual) or titulo_atual[:40],
                data=_data_de(titulo_atual),
                titulo=titulo_atual,
                texto=corpo,
            )
        )

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        cabecalho = _CABECALHO_SESSAO.match(linha)
        if cabecalho:
            _fechar()
            titulo_atual = cabecalho.group(1).strip()
            linhas_atuais = []
            continue
        if titulo_atual is not None:
            linhas_atuais.append(linha)
    _fechar()
    return docs


def ler_commits(raiz: Path) -> list[Documento]:
    """Cada commit e um documento: titulo + corpo.

    O corpo importa mais que o diff. Neste projeto a mensagem de commit
    carrega o PORQUE (o que foi medido, o que se decidiu nao fazer, o que
    ficou aberto) - e e disso que a pergunta "qual foi a solucao que
    funcionou" precisa. Diff ninguem le procurando causa.
    """
    formato = _FIM_CAMPO.join(["%h", "%ad", "%s", "%b"]) + _FIM_REGISTRO
    try:
        bruto = subprocess.run(
            ["git", "log", f"--pretty=format:{formato}", "--date=short"],
            cwd=raiz,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    docs: list[Documento] = []
    for registro in bruto.split(_FIM_REGISTRO):
        registro = registro.strip("\n")
        if not registro:
            continue
        partes = registro.split(_FIM_CAMPO)
        if len(partes) < 4:
            continue
        curto, data, assunto, corpo = partes[0], partes[1], partes[2], partes[3]
        docs.append(
            Documento(
                tipo="commit",
                ref=curto,
                data=data,
                titulo=assunto,
                texto=corpo.strip(),
            )
        )
    return docs


def ler_edrs(raiz: Path) -> list[Documento]:
    """Os EDRs sao as decisoes com nome e numero - o 'por que e assim'."""
    pasta = raiz / "docs/edr"
    if not pasta.is_dir():
        return []

    docs: list[Documento] = []
    for caminho in sorted(pasta.glob("*.md")):
        texto = caminho.read_text(encoding="utf-8")
        primeira = next(
            (linha.lstrip("# ").strip() for linha in texto.splitlines() if linha.strip()),
            caminho.stem,
        )
        docs.append(
            Documento(
                tipo="edr",
                ref=caminho.stem,
                data=_data_de(texto),
                titulo=primeira,
                texto=texto,
            )
        )
    return docs


#: Pastas de codigo que entram no indice. `tests/` fica de fora de
#: proposito: teste descreve o que o codigo faz, e quem pergunta "onde isso
#: e tratado" quer a implementacao, nao o dobro de resultados repetidos.
PASTAS_DE_CODIGO = ("motion_core", "src/orion", "tools")

#: Firmware entra por ARQUIVO inteiro (sao pequenos e o `ast` e de Python).
EXTENSOES_FIRMWARE = (".h", ".cpp", ".ino")


def _trechos_python(caminho: Path, raiz: Path) -> list[Documento]:
    """Uma funcao/classe de topo por documento, com o comentario que a
    antecede - e ali que este projeto escreve o porque."""
    import ast

    try:
        fonte = caminho.read_text(encoding="utf-8")
        arvore = ast.parse(fonte)
    except (SyntaxError, UnicodeDecodeError):
        return []

    linhas = fonte.splitlines()
    relativo = str(caminho.relative_to(raiz))
    docs: list[Documento] = []

    for no in arvore.body:
        if not isinstance(no, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        # `decorator_list` desloca lineno para baixo; pegar o topo real
        inicio = min([no.lineno] + [d.lineno for d in no.decorator_list]) - 1
        # sobe enquanto for comentario colado - o "porque" mora ali
        while inicio > 0 and linhas[inicio - 1].lstrip().startswith("#"):
            inicio -= 1
        fim = no.end_lineno or no.lineno
        corpo = "\n".join(linhas[inicio:fim])
        if len(corpo) < 40:
            continue
        docs.append(
            Documento(
                tipo="codigo",
                ref=f"{relativo}:{no.lineno}",
                data="",
                titulo=f"{relativo} :: {no.name}",
                texto=corpo,
            )
        )
    return docs


def ler_codigo(raiz: Path) -> list[Documento]:
    docs: list[Documento] = []
    for pasta in PASTAS_DE_CODIGO:
        base = raiz / pasta
        if not base.is_dir():
            continue
        for caminho in sorted(base.rglob("*.py")):
            if "__pycache__" in caminho.parts or ".venv" in caminho.parts:
                continue
            docs.extend(_trechos_python(caminho, raiz))

    firmware = raiz / "firmware"
    if firmware.is_dir():
        for caminho in sorted(firmware.rglob("*")):
            if caminho.suffix not in EXTENSOES_FIRMWARE or not caminho.is_file():
                continue
            if ".pio" in caminho.parts:
                continue
            try:
                texto = caminho.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            docs.append(
                Documento(
                    tipo="codigo",
                    ref=str(caminho.relative_to(raiz)),
                    data="",
                    titulo=str(caminho.relative_to(raiz)),
                    texto=texto,
                )
            )
    return docs


def construir(caminho_db: Path, raiz: Path) -> dict[str, int]:
    """Reconstroi o indice do zero. Idempotente por definicao."""
    docs = ler_journal(raiz) + ler_commits(raiz) + ler_edrs(raiz) + ler_codigo(raiz)
    con = _abrir(caminho_db)
    try:
        _criar_esquema(con)
        con.executemany(
            "INSERT INTO documentos(tipo, ref, data, titulo, texto) VALUES (?,?,?,?,?)",
            [(d.tipo, d.ref, d.data, d.titulo, d.texto) for d in docs],
        )
        con.commit()
    finally:
        con.close()

    contagem: dict[str, int] = {}
    for d in docs:
        contagem[d.tipo] = contagem.get(d.tipo, 0) + 1
    return contagem


def _raiz_do_repo() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=CAMINHO_PADRAO)
    sub = parser.add_subparsers(dest="acao", required=True)

    sub.add_parser("construir", help="reconstroi o indice a partir do repositorio")
    sub.add_parser("estado", help="quantos documentos por tipo")
    p_buscar = sub.add_parser("buscar", help="procura e mostra os trechos")
    p_buscar.add_argument("termos", nargs="+")
    p_buscar.add_argument("-n", "--limite", type=int, default=5)
    p_buscar.add_argument("--tipo", choices=("journal", "commit", "edr", "codigo"))

    args = parser.parse_args(argv)

    if args.acao == "construir":
        contagem = construir(args.db, _raiz_do_repo())
        total = sum(contagem.values())
        print(f"indice em {args.db}")
        for tipo, quantos in sorted(contagem.items()):
            print(f"  {tipo:8s} {quantos}")
        print(f"  {'total':8s} {total}")
        tamanho = args.db.stat().st_size if args.db.exists() else 0
        print(f"  {tamanho / 1024:.0f} KB")
        return 0

    if args.acao == "estado":
        linhas = estado(args.db)
        if not linhas:
            print(f"sem indice em {args.db} - rode `construir`")
            return 1
        for tipo, quantos in linhas:
            print(f"  {tipo:8s} {quantos}")
        return 0

    pergunta = " ".join(args.termos)
    try:
        achados = buscar(args.db, pergunta, limite=args.limite, tipo=args.tipo)
    except (FileNotFoundError, ValueError) as erro:
        print(erro, file=sys.stderr)
        return 1

    if not achados:
        print(f"nada encontrado para {pergunta!r}")
        return 0

    for achado in achados:
        etiqueta = f"[{achado.tipo}]"
        referencia = f"{achado.ref}" + (f"  {achado.data}" if achado.data else "")
        print(f"\n{etiqueta:10s} {referencia}")
        print(f"  {achado.titulo}")
        print(f"  {achado.trecho}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
