#!/usr/bin/env python3
"""Transforma um RASCUNHO da Wikipedia em AULA, com UMA chamada de modelo.

    .venv/bin/python tools/redigir_aula.py fisica.cinematica.basico
    .venv/bin/python tools/redigir_aula.py --todos --limite 5
    .venv/bin/python tools/redigir_aula.py --secar fisica.cinematica.basico

Pedido do dono em 2026-08-07, depois de ver o rascunho gerado da Wikipedia:
o material esta certo e com fonte, mas e prosa de enciclopedia - falta a
didatica. Este comando faz a ponte.

## UMA chamada por AULA, e nunca por PERGUNTA. E o ponto inteiro.

O robo NAO chama modelo para dar aula: ele le o YAML. O modelo entra aqui,
na autoria, uma vez por aula, e o resultado fica guardado para sempre. E o
proprio principio do dono - *"o robo so pergunta uma vez e salva em
memoria, dai nunca mais pergunta ele ja sabe"* - aplicado ao conteudo.

A conta: 308 aulas a escrever, uma chamada cada = 308 chamadas UMA VEZ.
Pelo caminho oposto (modelo montando a aula na hora), a cota diaria de 500
acabaria numa tarde de uso - e ja acabou, em 07/08, as 10h da manha.

## O que este comando NAO faz

Nao publica. A saida volta para `content/rascunhos/`, sobrescrevendo o
rascunho com a versao redigida, e `gerar_aula.py --publicar` continua sendo
um ato separado e humano. Modelo redigindo conteudo de ensino sem ninguem
ler e exatamente o que produz o erro que ninguem percebe.

## As tres travas contra o modelo inventar

1. **Vocabulario FECHADO**: o prompt lista os tipos de bloco que a tela sabe
   desenhar, e a validacao recusa qualquer outro - a mesma trava do catalogo
   de acoes faladas.
2. **Validacao antes de gravar**: a saida passa por `carregar_arquivo`, o
   mesmo carregador do robo. Se nao for uma aula valida, o rascunho ORIGINAL
   fica onde estava e o comando falha dizendo por que.
3. **A fonte e preservada do rascunho**, nao pedida ao modelo: procedencia
   nao pode depender de o modelo lembrar de copia-la.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from orion.display.quadro import bloco_para_prompt  # noqa: E402
from orion.ensino.banco_aulas import AulaInvalida, carregar_arquivo  # noqa: E402
from orion.kernel.config import ConfigurationManager  # noqa: E402

PASTA_RASCUNHOS = RAIZ / "content" / "rascunhos"

#: O modelo devolve JSON e nao YAML: JSON tem um parser que nao aceita
#: ambiguidade, e YAML gerado por modelo quebra com dois-pontos no meio de
#: uma frase - erro que ja aconteceu nesta sessao, escrito a mao.
_INSTRUCAO = """Voce e um professor experiente do ensino medio brasileiro.

Recebe o material bruto de um assunto (extraido da Wikipedia) e o transforma
numa AULA para um robo dar em voz alta, escrevendo num quadro.

REGRAS DA AULA:
- Fale como quem explica a uma pessoa de 15 anos, com frases curtas.
- Comece pelo CONCRETO: um exemplo do dia a dia antes da definicao.
- Cada passo tem "fala" (o que o robo diz) e "quadro" (o que ele escreve).
- O quadro NAO repete a fala: no quadro vai o que precisa ser VISTO - a
  palavra, a formula, a tabela, os passos de um metodo.
- Use os numeros e fatos do material. NAO invente dado que nao esta la.
- Se o material nao cobrir algo do objetivo, simplesmente nao ensine isso.
- Entre 8 e 14 passos.
- Termine com uma pergunta que faca a pessoa usar o que aprendeu.

TIPOS DE BLOCO QUE A TELA SABE DESENHAR (nao invente outros):
{tipos}

Responda SO com um JSON valido, sem cercas de codigo, nesta forma:
{{"plano": {{"objetivo": "...", "pre_requisitos": ["..."], "duracao_min": 12,
  "fixacao": ["...", "..."], "fechamento": "..."}},
 "passos": [{{"fala": "...", "quadro": {{"tipo": "texto", "texto": "..."}}}}]}}
"""


def _pedir_ao_modelo(rascunho: dict, secao_ia: dict, secar: bool) -> dict | None:
    material = "\n\n".join(
        p["fala"] for p in rascunho["passos"] if p.get("fala")
    )
    pedido = (
        f"ASSUNTO: {rascunho['titulo']}\n"
        f"MATERIA: {rascunho['materia']}  NIVEL: {rascunho['nivel']}\n"
        f"OBJETIVO DA AULA: {rascunho['plano']['objetivo']}\n\n"
        f"MATERIAL BRUTO:\n{material}"
    )
    instrucao = _INSTRUCAO.format(tipos=bloco_para_prompt())

    if secar:
        print("--- INSTRUCAO ---\n" + instrucao)
        print("--- PEDIDO ---\n" + pedido[:1500] + "\n[...]")
        return None

    from openai import OpenAI

    cliente = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=secao_ia.get("openai_base_url"),
    )
    resposta = cliente.chat.completions.create(
        model=secao_ia.get("openai_model", "gemini-flash-lite-latest"),
        messages=[
            {"role": "system", "content": instrucao},
            {"role": "user", "content": pedido},
        ],
        # Temperatura BAIXA: aqui nao se quer criatividade, se quer fidelidade
        # ao material. Aula inventada com confianca e o defeito que este
        # projeto mais persegue.
        temperature=0.3,
        max_tokens=3000,
    )
    bruto = (resposta.choices[0].message.content or "").strip()
    # Modelos gostam de cercar JSON com ```json apesar do pedido.
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1]
        bruto = bruto[4:] if bruto.startswith("json") else bruto
    return json.loads(bruto)


def redigir(id_aula: str, secao_ia: dict, secar: bool) -> bool:
    caminho = PASTA_RASCUNHOS / f"{id_aula}.yaml"
    if not caminho.exists():
        print(f"nao existe rascunho: {caminho}", file=sys.stderr)
        return False

    rascunho = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    try:
        redigida = _pedir_ao_modelo(rascunho, secao_ia, secar)
    except Exception as erro:  # noqa: BLE001
        print(f"  FALHOU  {id_aula} - {type(erro).__name__}: {erro}")
        return False
    if redigida is None:
        return True  # modo --secar

    # A identificacao e a FONTE vem do rascunho, nunca do modelo: procedencia
    # nao pode depender de ele lembrar de copiar.
    nova = {
        "id": rascunho["id"],
        "materia": rascunho["materia"],
        "titulo": rascunho["titulo"],
        "nivel": rascunho["nivel"],
        "resumo": rascunho["resumo"],
        "fonte": rascunho["fonte"],
        "plano": redigida.get("plano") or rascunho["plano"],
        "passos": redigida.get("passos") or [],
    }

    # VALIDA ANTES DE GRAVAR, com o mesmo carregador que o robo usa. Um
    # rascunho bom substituido por uma saida invalida seria perder trabalho
    # e a chamada paga junto.
    temporario = caminho.with_suffix(".yaml.novo")
    temporario.write_text(
        yaml.safe_dump(nova, allow_unicode=True, sort_keys=False, width=88),
        encoding="utf-8",
    )
    try:
        aula = carregar_arquivo(temporario)
    except AulaInvalida as erro:
        temporario.unlink()
        print(f"  RECUSADA  {id_aula} - o modelo devolveu algo invalido: {erro}")
        return False

    temporario.replace(caminho)
    print(f"  redigida  {id_aula:38} {aula.quantos_passos} passos")
    return True


def principal() -> int:
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("id", nargs="?", help="id do rascunho")
    analisador.add_argument("--todos", action="store_true")
    analisador.add_argument("--limite", type=int, default=3)
    analisador.add_argument(
        "--secar", action="store_true",
        help="mostra o que seria enviado e NAO chama o modelo (nao gasta cota)",
    )
    argumentos = analisador.parse_args()

    config = ConfigurationManager(str(RAIZ / "config" / "orion.yaml")).carregar()
    secao_ia = config.secao("ai")

    if argumentos.todos:
        ids = sorted(p.stem for p in PASTA_RASCUNHOS.glob("*.yaml"))[: argumentos.limite]
    elif argumentos.id:
        ids = [argumentos.id]
    else:
        analisador.error("informe um id ou --todos")

    if not ids:
        print("nenhum rascunho em content/rascunhos")
        return 1

    print(f"{len(ids)} rascunho(s), UMA chamada de modelo cada:")
    feitos = sum(1 for i in ids if redigir(i, secao_ia, argumentos.secar))
    print(f"\n{feitos} de {len(ids)}")
    if feitos and not argumentos.secar:
        print(
            "LEIA antes de publicar. O modelo redigiu a partir do material, mas quem\n"
            "responde pelo que o robo ensina e voce.\n"
            "Depois: tools/gerar_aula.py --publicar <id>  e  tools/aulas.py construir"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
