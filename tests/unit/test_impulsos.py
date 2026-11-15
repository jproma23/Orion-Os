"""Impulsos (ligados em 2026-11-15): curiosidade e busca de companhia.

O modulo existia desde 05/08 sem chamador e sem teste. Dono, 15/11: "o robo
tem q ter curiosidade e buscar companhia".
"""

from __future__ import annotations

from types import SimpleNamespace

from motion_core.behavior import goap
from motion_core.behavior.impulsos import (
    SEGUNDOS_ATE_SOLIDAO_MAXIMA,
    SEGUNDOS_ATE_TEDIO_MAXIMO,
    Impulsos,
)

ACOES = ("explorar", "seguir_pessoa", "olhar_em_volta", "parar", "fotografar")


class _Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _retrato(frente=120.0, pessoa=False, estado_hw="IDLE", viva=True):
    return SimpleNamespace(
        obstaculo_frente_cm=frente,
        pessoa_presente=pessoa,
        pessoa_conhecida=pessoa,
        telemetria_viva=viva,
        estado_hardware=estado_hw,
        bateria_nivel=None,
        alerta_sentinela=None,
    )


def _impulsos(hora=15):
    relogio = _Relogio()
    return Impulsos(agora=relogio, hora_local=lambda: hora), relogio


def _passar(imp, relogio, retrato, segundos):
    """Avalia no comeco e depois de `segundos`: os relogios contam desde a 1a vez."""
    estado = goap.estado_de(retrato)
    imp.avaliar(retrato, estado)
    relogio.t += segundos
    return {m.nome: m for m in imp.avaliar(retrato, estado)}, estado


def test_sozinho_por_15_min_quer_companhia_e_sai_explorando():
    imp, relogio = _impulsos()
    r = _retrato()
    medidos, estado = _passar(imp, relogio, r, SEGUNDOS_ATE_SOLIDAO_MAXIMA)

    assert medidos["solidao"].urgencia == 1.0
    acao, motivo = imp.acao_interna(r, estado, ACOES)
    assert acao == "explorar"
    assert "faz tempo que nao vejo ninguem" in motivo


def test_com_gente_a_vista_a_solidao_some():
    imp, relogio = _impulsos()
    medidos, _ = _passar(imp, relogio, _retrato(pessoa=True), SEGUNDOS_ATE_SOLIDAO_MAXIMA)
    assert medidos["solidao"].urgencia == 0.0


def test_parado_muito_tempo_da_tedio():
    imp, relogio = _impulsos()
    medidos, _ = _passar(imp, relogio, _retrato(pessoa=True), SEGUNDOS_ATE_TEDIO_MAXIMO)
    assert medidos["tedio"].urgencia == 1.0
    assert medidos["tedio"].familia == "curiosidade"


def test_andando_nao_tem_tedio():
    imp, relogio = _impulsos()
    medidos, _ = _passar(imp, relogio, _retrato(estado_hw="EXECUTING_MISSION"), SEGUNDOS_ATE_TEDIO_MAXIMO)
    assert medidos["tedio"].urgencia == 0.0


def test_de_madrugada_nao_sai_procurando_ninguem():
    imp, relogio = _impulsos(hora=3)
    r = _retrato()
    medidos, estado = _passar(imp, relogio, r, SEGUNDOS_ATE_TEDIO_MAXIMO)

    assert medidos["solidao"].urgencia == 0.0
    assert medidos["tedio"].urgencia == 0.0
    assert imp.acao_interna(r, estado, ACOES) is None


def test_silencio_que_passa_da_meia_noite():
    assert Impulsos(hora_local=lambda: 23).em_silencio()
    assert Impulsos(hora_local=lambda: 6).em_silencio()
    assert not Impulsos(hora_local=lambda: 7).em_silencio()
    assert not Impulsos(hora_local=lambda: 15).em_silencio()


def test_frente_fechada_olha_em_volta_em_vez_de_explorar():
    imp, relogio = _impulsos()
    r = _retrato(frente=20.0)
    _, estado = _passar(imp, relogio, r, SEGUNDOS_ATE_SOLIDAO_MAXIMA)
    acao, _ = imp.acao_interna(r, estado, ACOES)
    assert acao == "olhar_em_volta"


def test_acao_fora_das_permitidas_nao_sai():
    imp, relogio = _impulsos()
    r = _retrato()
    _, estado = _passar(imp, relogio, r, SEGUNDOS_ATE_SOLIDAO_MAXIMA)
    assert imp.acao_interna(r, estado, ("parar",)) is None


def test_bloco_do_prompt_diz_o_que_sente():
    imp, relogio = _impulsos()
    r = _retrato()
    medidos, _ = _passar(imp, relogio, r, SEGUNDOS_ATE_SOLIDAO_MAXIMA)
    bloco = imp.bloco_para_prompt(list(medidos.values()))
    assert "faz tempo que nao vejo ninguem" in bloco
    assert "forte" in bloco


# ------------------------------------------------- ligado na IA estrategica
import pytest

from motion_core.behavior.comportamentos import IaEstrategica
from orion.kernel.event_bus import EventBus


class _Resposta:
    def __init__(self, payload):
        self.payload = payload


class _Comm:
    def __init__(self, falha=False, acao="parar"):
        self.falha, self.acao, self.pedidos = falha, acao, []

    async def request(self, destino, payload, timeout_s=None):
        self.pedidos.append(payload)
        if self.falha:
            raise TimeoutError("sem Notebook (simulado)")
        return _Resposta({"ok": True, "comportamento": self.acao, "motivo": "a IA quis"})


def _ia(comm, imp, retrato):
    return IaEstrategica(
        EventBus(), comm, acoes_validas=ACOES, impulsos=imp, ler_retrato=lambda: retrato
    )


@pytest.mark.asyncio
async def test_ia_calada_o_impulso_de_companhia_decide():
    imp, relogio = _impulsos()
    r = _retrato()
    imp.avaliar(r, goap.estado_de(r))
    relogio.t += SEGUNDOS_ATE_SOLIDAO_MAXIMA
    ia = _ia(_Comm(falha=True), imp, r)

    await ia._consultar()

    assert ia._acao_atual == "explorar"
    assert "impulso proprio" in ia._motivo_atual


@pytest.mark.asyncio
async def test_ia_respondendo_ela_decide_e_recebe_o_que_ele_sente():
    imp, relogio = _impulsos()
    r = _retrato()
    imp.avaliar(r, goap.estado_de(r))
    relogio.t += SEGUNDOS_ATE_SOLIDAO_MAXIMA
    comm = _Comm(acao="parar")
    ia = _ia(comm, imp, r)

    await ia._consultar()

    assert ia._acao_atual == "parar"  # a abstencao/escolha dela continua dela
    assert "faz tempo que nao vejo ninguem" in comm.pedidos[0]["contexto"]
