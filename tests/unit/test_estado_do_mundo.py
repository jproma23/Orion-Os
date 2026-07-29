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
    """CORRIGIDO em 2026-07-29 - este teste protegia um bug.

    A versão anterior publicava `vision.person_detected` com `{"nome": ...}`
    dentro, e passava. Só que o `VisionCore` NUNCA emitiu `nome` nesse evento:
    o payload real é `{classe, confianca, caixa, timestamp}`. O dublê cumpria
    um contrato que o produtor não cumpria, e por isso ninguém percebia que
    `pessoa_conhecida` era falso para todo mundo em produção, inclusive para
    os donos da casa.

    Agora cada evento responde só o que ele de fato responde: `person_detected`
    diz que há ALGUÉM, `person_recognized` diz QUEM.
    """
    await mundo._ao_ver_pessoa(
        Evento("vision.person_detected", {"classe": "person", "confianca": 0.9})
    )
    r = mundo.retrato()
    assert r.pessoa_presente is True
    assert r.pessoa_nome is None  # detecção não identifica
    assert r.pessoa_conhecida is False

    await mundo._ao_reconhecer_pessoa(
        Evento("vision.person_recognized", {"pessoa_id": 1, "nome": "João Paulo"})
    )
    r = mundo.retrato()
    assert r.pessoa_nome == "João Paulo"
    assert r.pessoa_conhecida is True


@pytest.mark.asyncio
async def test_pessoa_perdida_some_na_hora(mundo: EstadoDoMundo) -> None:
    await mundo._ao_reconhecer_pessoa(Evento("vision.person_recognized", {"nome": "Ana"}))
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


# ---------- sem eco e "nao sei", nunca "esta livre" (2026-07-29) ----------


@pytest.mark.asyncio
async def test_sem_eco_nao_vira_distancia_no_retrato():
    """O retrato existe justamente para nao mentir sobre o mundo. Um 517cm
    com `sem_eco: true` nao e uma parede a 5 metros - e a ausencia de
    medida, e tem que sair como None ("nao sei")."""
    bus = EventBus()
    mundo = EstadoDoMundo(bus)

    await mundo._ao_receber_telemetria(
        Evento(
            topico="comm.mensagem.telemetry",
            dados={
                "payload": {
                    "distancia_frontal_cm": 517.24,
                    "distancia_frontal_valida": True,
                    "frontal_sem_eco": True,
                    "distancia_traseira_cm": 517.24,
                    "distancia_traseira_valida": True,
                    "traseiro_sem_eco": True,
                }
            },
        )
    )

    retrato = mundo.retrato()
    assert retrato.obstaculo_frente_cm is None
    assert retrato.obstaculo_tras_cm is None
    assert retrato.telemetria_viva  # o corpo esta vivo; so a medida que nao existe


@pytest.mark.asyncio
async def test_medida_real_aparece_no_retrato():
    """Contraprova do teste acima - sonda que nao sabe dizer sim nao e sonda."""
    bus = EventBus()
    mundo = EstadoDoMundo(bus)

    await mundo._ao_receber_telemetria(
        Evento(
            topico="comm.mensagem.telemetry",
            dados={
                "payload": {
                    "distancia_frontal_cm": 34.0,
                    "distancia_frontal_valida": True,
                    "frontal_sem_eco": False,
                }
            },
        )
    )

    assert mundo.retrato().obstaculo_frente_cm == 34.0


# ----- quem esta na frente do robo, e nao so que ha alguem (2026-07-29) -----


@pytest.mark.asyncio
async def test_pessoa_detectada_sem_identidade_nao_vira_conhecida():
    """`vision.person_detected` nao carrega `nome` - o payload real e
    `{classe, confianca, caixa, timestamp}`. Ate 2026-07-29 o EstadoDoMundo
    lia `nome` dali, entao `pessoa_conhecida` era falso para TODO MUNDO,
    inclusive para os donos da casa."""
    bus = EventBus()
    mundo = EstadoDoMundo(bus)

    await mundo._ao_ver_pessoa(
        Evento(
            topico="vision.person_detected",
            dados={"classe": "person", "confianca": 0.91, "caixa": (1, 2, 3, 4)},
        )
    )

    retrato = mundo.retrato()
    assert retrato.pessoa_presente is True
    assert retrato.pessoa_nome is None
    assert retrato.pessoa_conhecida is False


@pytest.mark.asyncio
async def test_pessoa_reconhecida_traz_o_nome():
    bus = EventBus()
    mundo = EstadoDoMundo(bus)

    await mundo._ao_reconhecer_pessoa(
        Evento(
            topico="vision.person_recognized",
            dados={"pessoa_id": 1, "nome": "João Paulo", "centro_x": 0.5},
        )
    )

    retrato = mundo.retrato()
    assert retrato.pessoa_nome == "João Paulo"
    assert retrato.pessoa_conhecida is True


@pytest.mark.asyncio
async def test_deteccao_nao_apaga_a_identidade_ja_conhecida():
    """A visao publica `person_detected` a cada quadro e `person_recognized` so
    quando identifica. Se a deteccao sobrescrevesse o nome, o robo esqueceria
    quem esta na frente dele ate 15 vezes por segundo."""
    bus = EventBus()
    mundo = EstadoDoMundo(bus)

    await mundo._ao_reconhecer_pessoa(
        Evento(topico="vision.person_recognized", dados={"nome": "Marah"})
    )
    await mundo._ao_ver_pessoa(
        Evento(topico="vision.person_detected", dados={"classe": "person"})
    )

    assert mundo.retrato().pessoa_nome == "Marah"
