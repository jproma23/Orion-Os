"""Testes dos modulos de display e missao (EDR-0023).

Tudo com dublês: nao sobe HTTP de verdade, nao chama IA, nao precisa de
chave de API. O que se testa aqui e o CICLO DE VIDA que o Boot Manager
depende - subir, responder se esta saudavel, desligar sem explodir.
"""
import asyncio

import pytest

from orion.display.modulo import ModuloDisplay
from orion.kernel.event_bus import EventBus
from orion.kernel.modulo import ModuloOrion
from orion.mission.modulo import ModuloMissao

CONFIG_VISAO = {"pan_limits_degrees": [-80, 80], "tilt_limits_degrees": [-30, 45]}
CONFIG_IA = {
    "ollama_model": "gemma3:4b",
    "temperature": 0.7,
    "system_prompt_file": "config/prompt_sistema.txt",
    "resposta_max_tokens": 200,
    "keep_alive_minutes": 5,
    "provider": "ollama",
}


class ServidorFalso:
    def __init__(self, falhar_ao_encerrar: bool = False) -> None:
        self.iniciou = False
        self.encerrou = False
        self.falhar_ao_encerrar = falhar_ao_encerrar

    async def iniciar(self) -> None:
        self.iniciou = True

    async def encerrar(self) -> None:
        if self.falhar_ao_encerrar:
            raise RuntimeError("porta travada")
        self.encerrou = True


class IaFalsa:
    async def responder(self, texto: str, contexto=None) -> str:
        return "oi"


# --------------------------------------------------------------- display


def test_display_cumpre_o_contrato():
    modulo = ModuloDisplay(EventBus(), CONFIG_VISAO, servidor=ServidorFalso())
    assert isinstance(modulo, ModuloOrion)
    assert modulo.nome == "display"


@pytest.mark.asyncio
async def test_display_sobe_e_desce():
    servidor = ServidorFalso()
    modulo = ModuloDisplay(EventBus(), CONFIG_VISAO, servidor=servidor)

    assert not modulo.esta_saudavel()
    await modulo.iniciar()
    assert servidor.iniciou
    assert modulo.esta_saudavel()

    await modulo.encerrar()
    assert servidor.encerrou
    assert not modulo.esta_saudavel()


@pytest.mark.asyncio
async def test_display_encerrar_engole_falha():
    """Contrato do EDR-0023: encerrar() nunca levanta.

    Se o avatar falhar ao fechar, os modulos seguintes ainda precisam soltar
    camera e microfone - uma excecao aqui deixaria hardware preso.
    """
    modulo = ModuloDisplay(EventBus(), CONFIG_VISAO, servidor=ServidorFalso(falhar_ao_encerrar=True))
    await modulo.iniciar()
    await modulo.encerrar()  # nao pode levantar
    assert not modulo.esta_saudavel()


@pytest.mark.asyncio
async def test_display_encerrar_sem_iniciar_e_seguro():
    # roda quando o boot falha no meio: o modulo nunca subiu, mas encerrar()
    # e chamado do mesmo jeito no caminho de limpeza
    await ModuloDisplay(EventBus(), CONFIG_VISAO).encerrar()


# --------------------------------------------------------------- missao


class CommFalso:
    def __init__(self) -> None:
        self.enviados: list[tuple[str, dict]] = []

    async def send(self, destino: str, dados: dict) -> None:
        self.enviados.append((destino, dados))

    async def request(self, *args, **kwargs):
        return {}


def test_missao_cumpre_o_contrato():
    modulo = ModuloMissao(EventBus(), CommFalso(), CONFIG_IA, ia=IaFalsa())
    assert isinstance(modulo, ModuloOrion)
    assert modulo.nome == "mission"


@pytest.mark.asyncio
async def test_missao_sobe_e_fica_saudavel():
    modulo = ModuloMissao(EventBus(), CommFalso(), CONFIG_IA, ia=IaFalsa())

    assert not modulo.esta_saudavel()
    await modulo.iniciar()
    assert modulo.esta_saudavel()

    await modulo.encerrar()
    assert not modulo.esta_saudavel()


@pytest.mark.asyncio
async def test_missao_manda_comando_pela_cadeia_e_nao_direto_ao_arduino():
    """Regra 2 do ARQUITETURA.txt: o Notebook nunca fala com o Arduino.

    O comando tem que sair endereçado a "hardware_core" - quem entrega pela
    serial e o Raspberry.
    """
    comm = CommFalso()
    modulo = ModuloMissao(EventBus(), comm, CONFIG_IA, ia=IaFalsa())
    await modulo.iniciar()

    await modulo._enviar_comando_hardware("STOP")

    assert comm.enviados == [("hardware_core", {"comando": "STOP"})]
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_missao_processar_antes_de_iniciar_falha_claro():
    modulo = ModuloMissao(EventBus(), CommFalso(), CONFIG_IA, ia=IaFalsa())
    with pytest.raises(RuntimeError):
        await modulo.processar("oi")
