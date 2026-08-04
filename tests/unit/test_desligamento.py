"""O botao de desligar faz as duas maquinas cairem, na ordem certa.

O que estes testes protegem e sobretudo a ORDEM e a TEIMOSIA: o Notebook antes
do Pi (porque e o Pi quem alcanca o Notebook), e o Pi cai mesmo se o Notebook
falhar (porque deixar as duas ligadas e pior).
"""
from __future__ import annotations

import pytest

from motion_core.desligamento import Desligador


class RodarEspiao:
    def __init__(self, respostas=None) -> None:
        self.chamadas: list[tuple] = []
        self._respostas = respostas or {}

    async def __call__(self, *argumentos):
        self.chamadas.append(argumentos)
        for chave, resposta in self._respostas.items():
            if chave in argumentos:
                return resposta
        return (0, "")


def _desligador(rodar, host="10.20.20.195"):
    return Desligador(host, rodar=rodar, segundos_ate_desligar=0.0)


@pytest.mark.asyncio
async def test_desliga_o_notebook_antes_do_raspberry():
    """O Pi e quem alcanca o Notebook. Invertendo a ordem, o Notebook ficaria
    ligado sozinho e sem ninguem para desliga-lo."""
    espiao = RodarEspiao()
    await _desligador(espiao).desligar_tudo()

    assert len(espiao.chamadas) == 2
    assert "ssh" in espiao.chamadas[0], "a primeira chamada tem que ser o Notebook"
    assert "ssh" not in espiao.chamadas[1], "a segunda e o proprio Pi"


@pytest.mark.asyncio
async def test_o_raspberry_cai_mesmo_se_o_notebook_falhar():
    """Perder o desligamento gracioso de uma maquina e melhor que deixar as
    duas ligadas."""
    espiao = RodarEspiao({"ssh": (255, "Connection refused")})
    resultado = await _desligador(espiao).desligar_tudo()

    assert resultado["notebook"]["ok"] is False
    assert resultado["raspberry"]["ok"] is True
    assert len(espiao.chamadas) == 2, "o Pi tem que ter sido chamado assim mesmo"


@pytest.mark.asyncio
async def test_ssh_nunca_pergunta_senha():
    """`BatchMode=yes` e `-n` no sudo: sem eles o comando fica PENDURADO
    esperando um teclado que nao existe - a pior falha para servico de fundo."""
    espiao = RodarEspiao()
    await _desligador(espiao).desligar_notebook()

    argumentos = espiao.chamadas[0]
    assert "BatchMode=yes" in argumentos
    assert "-n" in argumentos, "sudo tem que ser -n"
    assert "ConnectTimeout=5" in argumentos, "Notebook fora do ar nao pode travar o Pi"


@pytest.mark.asyncio
async def test_notebook_sem_permissao_e_reportado_e_nao_engolido():
    """Sem a regra de sudoers o Notebook recusa - e isso tem que APARECER."""
    espiao = RodarEspiao({"ssh": (1, "sudo: a password is required")})
    resultado = await _desligador(espiao).desligar_tudo()

    assert resultado["notebook"]["ok"] is False
    assert "password" in resultado["notebook"]["erro"]


@pytest.mark.asyncio
async def test_falha_de_rede_nao_levanta():
    """O botao nao pode derrubar a interface junto."""

    class Explode(RodarEspiao):
        async def __call__(self, *argumentos):
            self.chamadas.append(argumentos)
            if "ssh" in argumentos:
                raise OSError("rede fora (simulado)")
            return (0, "")

    resultado = await _desligador(Explode()).desligar_tudo()
    assert resultado["notebook"]["ok"] is False
    assert resultado["raspberry"]["ok"] is True
