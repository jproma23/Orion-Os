"""O QUADRO: a tela como lousa do professor (Cap 13 s.3).

Pedido do dono em 2026-08-07: *"jamboard como utilitario e controle total da
tela para mostrar graficos e ensinar"*, *"interativa onde ele pode ensinar
escrevendo por ali"* - materias do ensino medio, ingles, espanhol, italiano,
programacao e tecnologia.

UM AVISO SOBRE O NOME: o Google Jamboard foi DESLIGADO pelo Google no fim de
2024 - o servico nao existe mais para ninguem. "Tipo Jamboard" aqui e a
forma, nao o produto: o quadro e nosso, roda sem internet, sem conta e sem
depender de terceiro que possa desligar de novo.

## As regras que este arquivo aplica

1. **Vocabulario FECHADO, conferido no import.** Mesma trava do catalogo de
   acoes faladas (`mission/acoes_faladas.py`): o modelo escolhe um NOME de
   uma lista, e o codigo decide o que ele desenha. Tipo fora da lista nao
   vira chute - vira nada.
2. **Nenhum transporte novo.** O quadro viaja no `display.quadro` do Event
   Bus e sai pelo SSE que o `avatar_server.py` ja mantinha aberto para a
   legenda da fala.
3. **Sem biblioteca de formula.** O kiosk e Firefox: MathML e SVG sao
   nativos. Vendorizar KaTeX/MathJax seria megabytes por algo que o
   navegador ja faz.
4. **Via unica, servidor -> tela.** A tela nao tem teclado nem mouse e nao e
   touch. Quem escreve no quadro e o ROBO; quem responde e a pessoa,
   FALANDO, que e o canal que ele ja tem nos dois sentidos. Por isso nao ha
   evento vindo do navegador - e por isso existe o `apontar`, o dedo que
   mostra do que ele esta falando agora. Sem o dedo, a tela seria um slide.

## O que NAO esta aqui

Video. E o unico tipo que precisa de rede e de uma decisao de fonte (de onde
vem o video, quem paga a banda, o que fazer quando nao carrega), e mistura-lo
com texto e formula atrasaria os dois. Fica para depois, de proposito.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("orion.display.quadro")

#: Topico unico do quadro no Event Bus. Um so, e nao um por tipo de bloco: o
#: tipo viaja DENTRO do evento, entao acrescentar um tipo novo nao exige
#: mexer na lista de topicos repassados do avatar_server (que e exatamente o
#: lugar onde este projeto ja esqueceu de registrar um topico quatro vezes -
#: a saudacao que nunca aparecia na tela).
TOPICO = "display.quadro"

#: Topico do liga/desliga da tela, para o robo poder mostrar o quadro mesmo
#: quando a tela esta descansando. Ver `tela.py`.
TOPICO_TELA = "display.tela"


class BlocoInvalido(ValueError):
    """Tipo fora do catalogo, ou campo obrigatorio faltando."""


@dataclass(frozen=True)
class TipoBloco:
    """Uma coisa que da para escrever no quadro.

    `descricao` NAO e documentacao: e o texto que o MODELO le para escolher.
    Vale escrever como quem explica a uma pessoa - mesma regra do catalogo de
    acoes faladas.
    """

    nome: str
    campos: tuple[str, ...]
    descricao: str
    #: Campos que podem vir, mas nao precisam.
    campos_opcionais: tuple[str, ...] = ()


_CATALOGO: tuple[TipoBloco, ...] = (
    TipoBloco(
        nome="titulo",
        campos=("texto",),
        descricao="o titulo da aula ou de uma parte dela. Abre o assunto",
    ),
    TipoBloco(
        nome="texto",
        campos=("texto",),
        descricao=(
            "um paragrafo curto de explicacao. NAO escreva no quadro tudo o "
            "que voce vai falar - o quadro e o que a pessoa precisa VER: a "
            "grafia de uma palavra, a definicao, o passo do calculo"
        ),
    ),
    TipoBloco(
        nome="topicos",
        campos=("itens",),
        campos_opcionais=("texto",),
        descricao=(
            "uma lista de pontos (campo 'itens'), com um titulo opcional em "
            "'texto'. Use para os passos de um metodo ou as regras de uma "
            "conjugacao"
        ),
    ),
    TipoBloco(
        nome="formula",
        campos=("texto",),
        descricao=(
            "uma formula ou equacao escrita em texto comum, onde ^ eleva e _ "
            "abaixa: 'x^2 + 2x - 3 = 0', 'H_2O', 'v = d/t'. E o jeito normal "
            "de escrever formula no quadro"
        ),
    ),
    TipoBloco(
        # A saida de emergencia da `formula`: fracao empilhada, raiz com
        # indice, matriz. Existe porque o Firefox do kiosk renderiza MathML
        # nativo - nao ha biblioteca nenhuma envolvida.
        nome="mathml",
        campos=("texto",),
        descricao=(
            "uma formula em MathML, para o que texto comum nao da conta "
            "(fracao empilhada, raiz quadrada de verdade, matriz). Comece "
            "sempre com <math>. So use quando a formula simples nao servir"
        ),
    ),
    TipoBloco(
        nome="codigo",
        campos=("texto",),
        campos_opcionais=("linguagem",),
        descricao=(
            "um trecho de programa, com a linguagem em 'linguagem' (python, "
            "javascript, c). Preserva a indentacao - e o que faz o codigo ser "
            "lido como codigo"
        ),
    ),
    TipoBloco(
        nome="tabela",
        campos=("linhas",),
        campos_opcionais=("cabecalho", "texto"),
        descricao=(
            "uma tabela: 'cabecalho' e a lista de titulos das colunas e "
            "'linhas' e uma lista de listas. E o formato certo para "
            "conjugacao de verbo e para vocabulario nos dois idiomas lado a "
            "lado"
        ),
    ),
    TipoBloco(
        nome="grafico",
        campos=("valores",),
        campos_opcionais=("texto", "rotulos"),
        descricao=(
            "um grafico de barras: 'valores' e a lista de numeros e "
            "'rotulos' o nome de cada barra. Para comparar quantidades"
        ),
    ),
    TipoBloco(
        nome="limpar",
        campos=(),
        descricao=(
            "apaga o quadro inteiro. Use ao trocar de assunto - quadro cheio "
            "de aula velha atrapalha a nova"
        ),
    ),
    TipoBloco(
        nome="apontar",
        campos=("alvo",),
        descricao=(
            "aponta o dedo para um bloco que voce ja escreveu, pelo numero "
            "que ele recebeu. Use ao falar sobre algo que ja esta no quadro"
        ),
    ),
)

TIPOS: dict[str, TipoBloco] = {tipo.nome: tipo for tipo in _CATALOGO}


def _conferir_catalogo() -> None:
    """Confere o catalogo no import - e nao no primeiro uso.

    Um erro aqui (nome repetido, campo escrito de dois jeitos) so apareceria
    no meio de uma aula, na frente de quem esta aprendendo. Melhor derrubar a
    subida do robo, que e quando alguem esta olhando.
    """
    if len(TIPOS) != len(_CATALOGO):
        raise RuntimeError("nomes repetidos no catalogo do quadro")
    for tipo in _CATALOGO:
        if not tipo.nome.isidentifier() or tipo.nome != tipo.nome.lower():
            raise RuntimeError(f"nome invalido no catalogo do quadro: {tipo.nome!r}")
        if not tipo.descricao.strip():
            raise RuntimeError(f"{tipo.nome} sem descricao - o modelo nao teria como escolher")
        repetidos = set(tipo.campos) & set(tipo.campos_opcionais)
        if repetidos:
            raise RuntimeError(
                f"{tipo.nome}: campo obrigatorio e opcional ao mesmo tempo: {repetidos}"
            )


_conferir_catalogo()


def montar(tipo: str, **campos) -> dict:
    """Valida e devolve o dicionario que vai no evento.

    Levanta `BlocoInvalido` em vez de consertar por conta propria: um bloco
    remendado aqui viraria um quadro que mostra o que ninguem pediu, e
    descobrir isso na frente de um aluno e tarde demais. Quem chama a partir
    da IA trata a excecao e simplesmente nao escreve nada.
    """
    definicao = TIPOS.get(tipo)
    if definicao is None:
        raise BlocoInvalido(f"tipo de bloco desconhecido: {tipo!r}")
    faltando = [campo for campo in definicao.campos if not campos.get(campo)]
    if faltando:
        raise BlocoInvalido(f"bloco {tipo!r} sem os campos {faltando}")
    permitidos = set(definicao.campos) | set(definicao.campos_opcionais)
    sobrando = set(campos) - permitidos
    if sobrando:
        # Campo a mais e quase sempre nome errado ("titulo" em vez de
        # "texto"), e o silencio faria o bloco aparecer sem aquele conteudo.
        raise BlocoInvalido(f"bloco {tipo!r} com campos que nao existem: {sorted(sobrando)}")
    return {"tipo": tipo, **{k: v for k, v in campos.items() if v is not None}}


def bloco_para_prompt() -> str:
    """O texto que ensina o modelo a escrever no quadro.

    Gerado do catalogo, e nao escrito a mao num arquivo de prompt: prompt
    escrito a mao envelhece calado - acrescentar um tipo e esquecer de
    descreve-lo daria um tipo que existe e que o modelo nunca usa.
    """
    linhas = [f"- {tipo.nome}: {tipo.descricao}" for tipo in _CATALOGO]
    return "\n".join(linhas)


class Quadro:
    """Escreve no quadro publicando no Event Bus.

    Guarda o numero do ultimo bloco para o `apontar` ter em que se apoiar - e
    o unico estado aqui. A tela e quem guarda o conteudo; duplicar isso na
    memoria do Python criaria duas versoes do quadro que divergem no primeiro
    reload do navegador.
    """

    def __init__(self, event_bus) -> None:
        self._bus = event_bus
        self._contador = 0

    async def escrever(self, tipo: str, **campos) -> str:
        """Escreve um bloco e devolve o id dele (para poder apontar depois)."""
        dados = montar(tipo, **campos)
        self._contador += 1
        dados["id"] = f"b{self._contador}"
        # Acender a tela ANTES do bloco, e nao depois: o `display.quadro` ja
        # acorda a tela sozinho (ver tela.py), mas a ordem importa - um
        # monitor que acende com o conteudo ja escrito perde a primeira
        # linha para quem esta olhando.
        await self._bus.publish(TOPICO_TELA, {"ligada": True, "motivo": "quadro"})
        await self._bus.publish(TOPICO, dados)
        logger.info("quadro: %s %s", tipo, dados["id"])
        return dados["id"]

    async def limpar(self) -> None:
        self._contador = 0
        await self._bus.publish(TOPICO, {"tipo": "limpar"})

    async def apontar(self, alvo: str) -> None:
        await self._bus.publish(TOPICO, montar("apontar", alvo=alvo))
