"""O robo pergunta UMA VEZ por situacao, aprende, e nao pergunta de novo.

Pedido do dono em 2026-08-07, depois de a cota do Gemini acabar as 10h da
manha: *"ela tem q aprender a so perguntar uma unica vez e aprender dai nao
pergunta mais ja gravou a resposta"*.

O NUMERO QUE MOTIVOU: naquele dia o robo fez **505 chamadas** ao modelo e
tomou **4 decisoes**, todas `observar_ambiente`, e nao saiu do lugar
nenhuma vez. Com os sensores mudos (os dois ultrassons sem eco, sem
bussola), a situacao dele era a MESMA o dia inteiro - e ele repergunta a
cada intervalo como se fosse nova.

## A ideia, e por que ela e honesta

Mesma entrada, mesma resposta. A assinatura sai do PROPRIO TEXTO que iria
para o modelo - nao de uma reconstrucao paralela do estado, que envelheceria
separada dele. Se o texto que descreve a situacao nao mudou, a pergunta e a
mesma e a resposta ja esta guardada.

## O que entra na assinatura, e o que NAO entra

Entra o que muda a decisao:
  - o bloco "O QUE EU SINTO AGORA" (distancias, bateria, corpo)
  - se ha alguem na frente dele agora
  - a lista de opcoes oferecidas (mudou o cardapio, muda a escolha)

NAO entra o diario do dia ("as 20:11 eu vi..."), que cresce a cada evento e
faria toda situacao parecer nova - a assinatura nunca repetiria e o cache
nunca serviria para nada.

## Por que ha validade, se a ideia e nao reperguntar

Uma resposta guardada para sempre e uma decisao congelada: se o mundo mudar
de um jeito que a assinatura nao captura, o robo fica preso nela sem nunca
reconsiderar. A validade e generosa de proposito (24 h por padrao) - o
objetivo e nao gastar cota, nao e nunca mais perguntar.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger("motion_core.behavior.memoria_decisoes")

CAMINHO_PADRAO = Path("/mnt/ssd/orion/decisoes_aprendidas.json")

#: Um dia. Ver o cabecalho: existe para a decisao nao congelar, nao para
#: forcar repergunta.
VALIDADE_PADRAO_S = 24 * 3600.0

#: Teto de situacoes guardadas. Com sensores funcionando as combinacoes
#: crescem, e um arquivo sem teto viraria lixo acumulado. As mais RECENTES
#: vencem.
MAXIMO_PADRAO = 200

_CABECALHO_SENTIDOS = "O QUE EU SINTO AGORA:"
#: As secoes do contexto sao cabecalhos em CAIXA ALTA terminados em ":".
_PROXIMO_CABECALHO = re.compile(r"^[A-ZÀ-Ú][A-ZÀ-Ú \-,]{4,}:", re.MULTILINE)
_LINHA_VISAO = re.compile(r"^- (neste exato momento.*)$", re.MULTILINE)


def _bloco_dos_sentidos(contexto: str) -> str:
    """So o trecho que descreve o corpo agora."""
    inicio = contexto.find(_CABECALHO_SENTIDOS)
    if inicio == -1:
        # Formato desconhecido: melhor assinar o texto inteiro (o cache
        # simplesmente nao acerta) do que assinar vazio e devolver a mesma
        # decisao para situacoes diferentes.
        return contexto
    resto = contexto[inicio + len(_CABECALHO_SENTIDOS) :]
    proximo = _PROXIMO_CABECALHO.search(resto)
    return resto[: proximo.start()] if proximo else resto


def _visao_agora(contexto: str) -> str:
    achado = _LINHA_VISAO.search(contexto)
    return achado.group(1).strip() if achado else ""


def assinatura(contexto: str, opcoes: list[str]) -> str:
    """Impressao digital da SITUACAO, estavel entre consultas iguais."""
    partes = [
        " ".join(_bloco_dos_sentidos(contexto).split()),
        _visao_agora(contexto),
        ",".join(sorted(opcoes)),
    ]
    bruto = "|".join(partes)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


class MemoriaDeDecisoes:
    """Guarda o que ja foi decidido para cada situacao, e sobrevive a reinicio."""

    def __init__(
        self,
        caminho: Path | None = None,
        validade_s: float = VALIDADE_PADRAO_S,
        maximo: int = MAXIMO_PADRAO,
        agora=time.time,
    ) -> None:
        self._caminho = caminho or CAMINHO_PADRAO
        self._validade_s = validade_s
        self._maximo = maximo
        self._agora = agora
        self._decisoes: dict[str, dict] = {}
        #: Contadores para medir se isto esta servindo para alguma coisa.
        self.reaproveitadas = 0
        self.perguntadas = 0
        self._carregar()

    # ------------------------------------------------------------ disco

    def _carregar(self) -> None:
        """Nunca levanta: sem o arquivo o robo so volta a perguntar."""
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            logger.warning("nao consegui ler as decisoes aprendidas", exc_info=True)
            return
        if isinstance(dados, dict):
            self._decisoes = {k: v for k, v in dados.items() if isinstance(v, dict)}
            logger.info("%d decisoes aprendidas carregadas", len(self._decisoes))

    def _salvar(self) -> None:
        try:
            self._caminho.parent.mkdir(parents=True, exist_ok=True)
            self._caminho.write_text(json.dumps(self._decisoes), encoding="utf-8")
        except Exception:
            # Aprender e otimizacao de cota - falhar ao gravar nao pode
            # atrapalhar o robo decidir agora.
            logger.warning("nao consegui gravar as decisoes aprendidas", exc_info=True)

    # ----------------------------------------------------------- uso

    def lembrar(self, chave: str) -> dict | None:
        """A decisao ja tomada para esta situacao, se ainda vale."""
        registro = self._decisoes.get(chave)
        if registro is None:
            return None
        if self._agora() - registro.get("quando", 0) > self._validade_s:
            # Vencida: sai daqui para nao ocupar espaco e para a proxima
            # consulta contar como pergunta de verdade.
            self._decisoes.pop(chave, None)
            return None
        self.reaproveitadas += 1
        return {
            "comportamento": registro.get("comportamento"),
            "motivo": registro.get("motivo", ""),
        }

    def aprender(self, chave: str, comportamento: str, motivo: str = "") -> None:
        self.perguntadas += 1
        self._decisoes[chave] = {
            "comportamento": comportamento,
            "motivo": motivo,
            "quando": self._agora(),
        }
        if len(self._decisoes) > self._maximo:
            # As mais recentes vencem: situacao velha tem mais chance de
            # nao voltar a acontecer.
            ordenadas = sorted(
                self._decisoes.items(), key=lambda par: par[1].get("quando", 0)
            )
            self._decisoes = dict(ordenadas[-self._maximo :])
        self._salvar()

    def esquecer(self, chave: str) -> None:
        """Tira uma decisao da memoria - para quando ela se mostrar ruim."""
        if self._decisoes.pop(chave, None) is not None:
            self._salvar()

    @property
    def quantas(self) -> int:
        return len(self._decisoes)

    def resumo(self) -> str:
        total = self.reaproveitadas + self.perguntadas
        if not total:
            return "nenhuma consulta ainda"
        economia = self.reaproveitadas / total
        return (
            f"{self.quantas} situacoes conhecidas; "
            f"{self.reaproveitadas} reaproveitadas de {total} "
            f"({economia:.0%} de chamadas economizadas)"
        )
