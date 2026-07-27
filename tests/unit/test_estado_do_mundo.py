"""Testes do modelo de mundo unificado (motion_core/behavior/estado_do_mundo.py).

O foco é o comportamento que dá segurança ao maestro: dado vencido tem que
virar None ("não sei"), nunca continuar sendo servido como se fosse atual.
"""
from __future__ import annotations

import time

import pytest

from motion_core.behavior.estado_do_mundo import (
    VALIDADE_TELEMETRIA_S,
    EstadoDoMundo,
    _Campo,
)
from orion.kernel.event_bus import EventBus, Evento


@pytest.fixture
def mundo() -> EstadoDoMundo:
    return EstadoDoMundo(EventBus())


async def _telemetria(mundo: EstadoDoMundo, payload: dict) -> None:
    await mundo._ao_receber_telemetria(Evento("comm.mensagem.telemetry", {"payload": payload}))


# ----- campo com prazo de validade -----


def test_campo_nunca_escrito_devolve_none() -> None:
    assert _Campo().ler(validade_s=1.0) is None


def test_campo_recem_escrito_devolve_valor() -> None:
    campo = _Campo()
    campo.escrever(42)
    assert campo.ler(validade_s=1.0) == 42


def test_campo_vencido_devolve_none() -> None:
    """O coração da classe: valor velho vira 'não sei', não valor antigo."""
    campo = _Campo()
    campo.escrever(42)
    futuro = time.monotonic() + 10.0
    assert campo.ler(validade_s=1.0, agora=futuro) is None


# ----- retrato do mundo -----


@pytest.mark.asyncio
async def test_retrato_vazio_e_todo_desconhecido(mundo: EstadoDoMundo) -> None:
    r = mundo.retrato()
    assert r.obstaculo_frente_cm is None
    assert r.bateria_nivel is None
    assert r.telemetria_viva is False
    assert r.ouvindo is False


@pytest.mark.asyncio
async def test_telemetria_preenche_o_corpo(mundo: EstadoDoMundo) -> None:
    await _telemetria(
        mundo,
        {
            "distancia_frontal_cm": 34.0,
            "distancia_frontal_valida": True,
            "inclinacao_graus": 0.4,
            "impacto_detectado": False,
        },
    )
    r = mundo.retrato()
    assert r.obstaculo_frente_cm == 34.0
    assert r.inclinacao_graus == 0.4
    assert r.telemetria_viva is True


@pytest.mark.asyncio
async def test_flag_de_invalido_do_firmware_e_respeitado(mundo: EstadoDoMundo) -> None:
    """Ultrassom sem eco manda valor sujo com o flag em False - tem que
    virar None, senão o maestro decidiria em cima de lixo."""
    await _telemetria(
        mundo,
        {"distancia_frontal_cm": 999.0, "distancia_frontal_valida": False},
    )
    assert mundo.retrato().obstaculo_frente_cm is None


@pytest.mark.asyncio
async def test_telemetria_que_parou_de_chegar_marca_corpo_sem_sinal(
    mundo: EstadoDoMundo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cabo solto / Mega resetando: o quadro não pode continuar dizendo que
    está tudo bem com dado velho."""
    await _telemetria(
        mundo, {"distancia_frontal_cm": 34.0, "distancia_frontal_valida": True}
    )

    # Avança o relógio para além do prazo de validade da telemetria.
    real = time.monotonic
    monkeypatch.setattr(
        time, "monotonic", lambda: real() + VALIDADE_TELEMETRIA_S + 1.0
    )

    r = mundo.retrato()
    assert r.telemetria_viva is False
    assert r.obstaculo_frente_cm is None


@pytest.mark.asyncio
async def test_bateria_so_aparece_quando_lida(mundo: EstadoDoMundo) -> None:
    """Enquanto o divisor não estiver montado o firmware manda
    bateria_lida=False - não pode virar tensão inventada no quadro."""
    await _telemetria(mundo, {"bateria_lida": False, "bateria_tensao_v": 0.0})
    assert mundo.retrato().bateria_v is None

    await _telemetria(
        mundo,
        {"bateria_lida": True, "bateria_tensao_v": 17.2, "bateria_nivel": "ok"},
    )
    r = mundo.retrato()
    assert r.bateria_v == 17.2
    assert r.bateria_nivel == "ok"


@pytest.mark.asyncio
async def test_pessoa_conhecida_e_desconhecida(mundo: EstadoDoMundo) -> None:
    await mundo._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "João Paulo"}))
    r = mundo.retrato()
    assert r.pessoa_presente is True
    assert r.pessoa_nome == "João Paulo"
    assert r.pessoa_conhecida is True

    # Rosto sem nome = não reconhecido.
    await mundo._ao_ver_pessoa(Evento("vision.person_detected", {}))
    assert mundo.retrato().pessoa_conhecida is False


@pytest.mark.asyncio
async def test_pessoa_perdida_some_na_hora(mundo: EstadoDoMundo) -> None:
    await mundo._ao_ver_pessoa(Evento("vision.person_detected", {"nome": "Ana"}))
    await mundo._ao_perder_pessoa(Evento("vision.person_lost", {}))
    assert mundo.retrato().pessoa_presente is None


@pytest.mark.asyncio
async def test_voz_liga_e_desliga_o_ouvindo(mundo: EstadoDoMundo) -> None:
    await mundo._ao_acordar_voz(Evento("voice.wake_detected", {}))
    assert mundo.retrato().ouvindo is True
    await mundo._ao_terminar_voz(Evento("voice.response_finished", {}))
    assert mundo.retrato().ouvindo is False


@pytest.mark.asyncio
async def test_resumo_marca_sem_sinal_e_interrogacao(mundo: EstadoDoMundo) -> None:
    texto = mundo.retrato().resumo()
    assert "SEM SINAL" in texto
    assert "?" in texto
