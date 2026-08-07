#!/usr/bin/env python3
"""Constroi e consulta o banco de aulas - o conteudo que o robo ensina.

    .venv/bin/python tools/aulas.py construir
    .venv/bin/python tools/aulas.py listar
    .venv/bin/python tools/aulas.py buscar "equacao do segundo grau"
    .venv/bin/python tools/aulas.py ver ingles.verbo-to-be.basico

O `construir` le `content/aulas/*.yaml` e reconstroi o indice do zero. Ele
VALIDA todo bloco de quadro no caminho: aula com tipo escrito errado derruba
a construcao, com o arquivo e o numero do passo. E de proposito - o outro
lugar onde esse erro apareceria e no meio da aula, na frente de quem esta
aprendendo.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from orion.ensino.autoconhecimento import (  # noqa: E402
    Autoconhecimento,
    FatoInvalido,
)
from orion.ensino.autoconhecimento import carregar_arquivo as carregar_fatos  # noqa: E402
from orion.ensino.banco_aulas import (  # noqa: E402
    AulaInvalida,
    BancoAulas,
    caminho_padrao,
    carregar_pasta,
    normalizar_nivel,
)
from orion.ensino.ementa import (  # noqa: E402
    BancoEmentas,
    EmentaInvalida,
    contar_cobertura,
)
from orion.ensino.ementa import carregar_pasta as carregar_ementas  # noqa: E402

PASTA_CONTEUDO = RAIZ / "content" / "aulas"
PASTA_EMENTAS = RAIZ / "content" / "ementas"
ARQUIVO_SOBRE_MIM = RAIZ / "content" / "sobre_mim.yaml"


def _banco(argumentos) -> BancoAulas:
    # O MESMO caminho que o robo abre - `caminho_padrao` e de `banco_aulas`
    # justamente para nao haver dois palpites. Construir num arquivo e ler de
    # outro daria um robo que diz nao saber a materia que acabou de indexar.
    return BancoAulas(Path(argumentos.banco) if argumentos.banco else caminho_padrao(RAIZ))


def comando_construir(argumentos) -> int:
    try:
        aulas = carregar_pasta(PASTA_CONTEUDO)
    except AulaInvalida as erro:
        print(f"CONTEUDO INVALIDO: {erro}", file=sys.stderr)
        return 1
    if not aulas:
        print(f"nenhuma aula em {PASTA_CONTEUDO}", file=sys.stderr)
        return 1
    banco = _banco(argumentos)
    quantas = banco.construir(aulas)
    passos = sum(a.quantos_passos for a in aulas)
    print(f"{quantas} aulas, {passos} passos indexados")
    for aula in aulas:
        print(f"  {aula.nivel:14} {aula.materia:12} {aula.titulo}")

    # AS EMENTAS - o mapa. A cobertura e contada contra as aulas que existem
    # DE VERDADE, e nao contra o campo `aula` do arquivo: um id escrito
    # errado contaria como aula pronta e o numero mentiria.
    if PASTA_EMENTAS.is_dir():
        try:
            ementas = carregar_ementas(PASTA_EMENTAS)
        except EmentaInvalida as erro:
            print(f"EMENTA INVALIDA: {erro}", file=sys.stderr)
            return 1
        if ementas:
            banco_ementas = BancoEmentas(banco._caminho)
            banco_ementas.construir(ementas)
            prontos, total = contar_cobertura(ementas, {a.id for a in aulas})
            print(f"{len(ementas)} ementas, {total} topicos - {prontos} com aula escrita")
            for ementa in ementas:
                faltam = ementa.quantos_topicos - sum(
                    1 for lista in ementa.topicos.values()
                    for t in lista if t.aula in {a.id for a in aulas}
                )
                print(f"  {ementa.nome:16} {ementa.quantos_topicos:3} topicos, faltam {faltam}")
            banco_ementas.fechar()

    # O que ele sabe SOBRE SI MESMO vai para o mesmo arquivo: e a mesma
    # ideia (conteudo em texto, indice reconstruivel) e um comando so para
    # nao existir um banco atualizado e outro esquecido.
    if ARQUIVO_SOBRE_MIM.exists():
        try:
            fatos = carregar_fatos(ARQUIVO_SOBRE_MIM)
        except FatoInvalido as erro:
            print(f"SOBRE MIM INVALIDO: {erro}", file=sys.stderr)
            return 1
        auto = Autoconhecimento(banco._caminho)
        print(f"{auto.construir(fatos)} fatos sobre ele mesmo")
        auto.fechar()
    return 0


def comando_listar(argumentos) -> int:
    for aula in _banco(argumentos).listar(argumentos.materia, normalizar_nivel(argumentos.nivel)):
        print(f"{aula.id:46} {aula.nivel:14} {aula.titulo}")
    return 0


def comando_buscar(argumentos) -> int:
    pedido = " ".join(argumentos.termos)
    achados = _banco(argumentos).buscar(pedido, nivel=normalizar_nivel(pedido))
    if not achados:
        print("nao achei nada - e esta e a resposta honesta, nao uma falha")
        return 0
    for aula in achados:
        print(f"{aula.id:46} {aula.nivel:14} {aula.titulo}")
        if aula.resumo:
            print(f"    {aula.resumo}")
    return 0


def comando_ver(argumentos) -> int:
    aula = _banco(argumentos).carregar(argumentos.id)
    if aula is None:
        print(f"nao existe: {argumentos.id}", file=sys.stderr)
        return 1
    print(f"{aula.titulo}  [{aula.materia} / {aula.nivel}]")
    print(aula.resumo)
    for numero, passo in enumerate(aula.passos, start=1):
        tipo = passo.bloco["tipo"] if passo.bloco else "(so fala)"
        print(f"\n{numero:2}. [{tipo}] {passo.fala}")
    return 0


def principal() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--banco", help="caminho do saber.db")
    sub = analisador.add_subparsers(dest="comando", required=True)

    sub.add_parser("construir", help="reconstroi o indice a partir de content/aulas")

    listar = sub.add_parser("listar", help="lista as aulas do banco")
    listar.add_argument("--materia")
    listar.add_argument("--nivel")

    buscar = sub.add_parser("buscar", help="busca como o robo busca")
    buscar.add_argument("termos", nargs="+")

    ver = sub.add_parser("ver", help="mostra a aula inteira, passo a passo")
    ver.add_argument("id")

    argumentos = analisador.parse_args()
    return {
        "construir": comando_construir,
        "listar": comando_listar,
        "buscar": comando_buscar,
        "ver": comando_ver,
    }[argumentos.comando](argumentos)


if __name__ == "__main__":
    raise SystemExit(principal())
