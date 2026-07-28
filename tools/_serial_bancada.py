"""Porta e baud da serial do Mega, lidos do config/orion.yaml.

Existe por causa de um bug que custou caro: as ferramentas de bancada abriam
a serial com o baud FIXO no codigo (115200), e o baud real virou 250000 em
2026-07-27. O Mega continuava falando normalmente, mas na velocidade errada
so chega lixo binario - o WHO_ARE_YOU falhava em todas as portas e o sintoma
era identico ao de um Arduino morto ou desconectado. Todo diagnostico feito
com essas ferramentas desde 27/07 deu falso-negativo.

A licao nao foi "trocar o numero": foi parar de ter numero. Qualquer
ferramenta nova importa daqui em vez de declarar a sua constante, senao a
proxima mudanca de baud repete a historia inteira.

Uso:

    from _serial_bancada import BAUD, PORTA

(Import de irmao, sem pacote: funciona porque `python3 tools/foo.py` poe a
pasta do script no sys.path. Rode sempre pela raiz do projeto, com o .venv.)
"""
from __future__ import annotations

import glob
from pathlib import Path

from orion.kernel.config import ConfigurationManager

RAIZ = Path(__file__).resolve().parent.parent

_conf = (
    ConfigurationManager(RAIZ / "config" / "orion.yaml").carregar().secao("communication")["arduino"]
)

#: Mesma ordem que o `PORTAS_SERIAIS_CANDIDATAS` do motion_core/__main__.py.
CANDIDATAS = ("/dev/ttyUSB*", "/dev/ttyACM*")


def _descobrir_porta() -> str:
    """Primeira porta serial que EXISTE agora, em vez de um caminho chutado.

    `port: "auto"` na config significa descoberta por WHO_ARE_YOU, que quem
    faz e o Motion Core. Aqui ele esta parado (a bancada exige a serial
    livre), entao o criterio possivel e mais simples: pegar a que existe.

    Isso tambem cobre a migracao do CH340 entre ttyUSB0 e ttyUSB1, que ja
    aconteceu em 26/07 e deixava a ferramenta apontando para um device
    inexistente.
    """
    for padrao in CANDIDATAS:
        achadas = sorted(glob.glob(padrao))
        if achadas:
            return achadas[0]
    # Nenhuma porta presente: devolve o palpite historico para a mensagem de
    # erro sair com um caminho de verdade em vez de vazio.
    return "/dev/ttyUSB0"


_porta_conf = _conf.get("port", "auto")

#: Porta serial do Mega. Config manda; "auto" cai na descoberta acima.
PORTA: str = _descobrir_porta() if _porta_conf == "auto" else _porta_conf

#: Baud do enlace com o Mega. TEM que bater com o Serial.begin() do firmware.
BAUD: int = _conf["baud_rate"]

__all__ = ["BAUD", "PORTA", "RAIZ"]
