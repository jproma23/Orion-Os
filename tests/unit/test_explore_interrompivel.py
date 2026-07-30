"""Teste do achado da revisao de 2026-07-30: o trecho do explore e interrompivel."""
import asyncio

import pytest

from motion_core.navigation import navigation_core as nav


@pytest.mark.asyncio
async def test_explore_encerra_o_trecho_quando_chega_comando():
    """O PARAR nao pode esperar o trecho inteiro.

    `_executar_pendentes` faz await no `executar_explore` inteiro e o handler do
    bus nao cria tarefa nova enquanto a atual roda - so guarda em
    `_comando_pendente`. Com 4 passos por decisao, um STOP do operador esperaria
    ate 1,2 m. O laco olha a vaga entre os passos: quem chegou executa no
    proximo retorno, e o robo anda no maximo UM segmento a mais.
    """
    chamadas = {"scan": 0, "segmento": 0}

    class NavFalsa:
        _comando_pendente = None
        _distancia_frontal_atual_cm = 200.0
        _modo = None

        async def _mudar_modo(self, modo):
            self._modo = modo

        async def _escanear_a_frente(self):
            chamadas["scan"] += 1
            return [{"angulo": 90, "distancia_cm": 200.0, "valida": True}]

        def frente_desconhecida(self):
            return False

        def _obstaculo_na_varredura(self, leituras):
            return False

        def _obstaculo_a_frente(self):
            return False

        async def _publicar_obstaculo(self, contexto):
            pass

        async def _executar_segmento(self, graus, distancia_cm, velocidade_percent):
            chamadas["segmento"] += 1
            # o operador aperta PARAR durante o primeiro segmento
            if chamadas["segmento"] == 1:
                self._comando_pendente = {"acao": "MANUAL", "comando": "STOP"}
            return True

        executar_explore = nav.NavigationCore.executar_explore

    await NavFalsa().executar_explore()

    assert chamadas["segmento"] == 1, (
        f"andou {chamadas['segmento']} segmentos com um STOP na vaga - o PARAR "
        "esperou o trecho inteiro"
    )
