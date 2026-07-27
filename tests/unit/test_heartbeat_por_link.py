"""Tolerância de heartbeat POR ENLACE.

Motivação real (2026-07-19): com um limite único de 3 perdidos (3s), o Pi
declarava o link com o Notebook morto toda vez que a inferência de IA local
saturava a CPU de lá - o socket estava vivo, só os heartbeats atrasavam.
Mas simplesmente afrouxar o limite global seria pior: o mesmo parâmetro
governa o enlace com o Arduino, onde detectar falha rápido é segurança.

Daí o limite por módulo: paciência com o Notebook, rigor com o Mega.
"""
from __future__ import annotations

import pytest

from orion.kernel.watchdog import HealthMonitor


def _monitor() -> HealthMonitor:
    return HealthMonitor(intervalo_heartbeat_s=1.0, heartbeats_perdidos_limite=3)


def test_modulo_sem_limite_proprio_usa_o_global() -> None:
    m = _monitor()
    m.registrar_modulo("hardware_core", agora=0.0)
    assert m.timeout_de("hardware_core") == 3.0


def test_limite_proprio_sobrescreve_o_global() -> None:
    m = _monitor()
    m.registrar_modulo("mission_core", agora=0.0, heartbeats_perdidos_limite=45)
    assert m.timeout_de("mission_core") == 45.0


def test_modulo_desconhecido_cai_no_timeout_padrao() -> None:
    """Não pode explodir nem devolver algo absurdo para nome não registrado."""
    assert _monitor().timeout_de("nao_existe") == 3.0


def test_tolerante_sobrevive_a_pausa_que_mataria_o_rigido() -> None:
    """O cenário exato do bug: 20s de CPU travada pela inferência.

    O Arduino (rígido) seria dado como perdido - e deve ser, se sumir de
    verdade. O Notebook (tolerante) não pode ser.
    """
    m = _monitor()
    m.registrar_modulo("hardware_core", agora=0.0)
    m.registrar_modulo("mission_core", agora=0.0, heartbeats_perdidos_limite=45)

    perdidos = m.modulos_com_heartbeat_perdido(agora=20.0)

    assert "hardware_core" in perdidos
    assert "mission_core" not in perdidos


def test_tolerante_tambem_e_dado_como_perdido_se_sumir_de_vez() -> None:
    """Tolerância não é imortalidade: passado o prazo dele, cai também."""
    m = _monitor()
    m.registrar_modulo("mission_core", agora=0.0, heartbeats_perdidos_limite=45)
    assert "mission_core" in m.modulos_com_heartbeat_perdido(agora=46.0)


def test_heartbeat_recebido_reinicia_a_contagem_do_tolerante() -> None:
    m = _monitor()
    m.registrar_modulo("mission_core", agora=0.0, heartbeats_perdidos_limite=45)
    m.receber_heartbeat("mission_core", agora=40.0)
    # 50s no relógio, mas só 10s desde o último heartbeat.
    assert "mission_core" not in m.modulos_com_heartbeat_perdido(agora=50.0)


def test_limite_proprio_invalido_e_recusado() -> None:
    m = _monitor()
    with pytest.raises(ValueError):
        m.registrar_modulo("x", heartbeats_perdidos_limite=0)
    with pytest.raises(ValueError):
        m.registrar_modulo("x", heartbeats_perdidos_limite=-5)


def test_timeout_padrao_do_monitor_nao_muda_por_causa_de_um_modulo() -> None:
    """Dar tolerância a um enlace não pode afrouxar os outros."""
    m = _monitor()
    m.registrar_modulo("mission_core", agora=0.0, heartbeats_perdidos_limite=45)
    m.registrar_modulo("hardware_core", agora=0.0)
    assert m.timeout_s == 3.0
    assert m.timeout_de("hardware_core") == 3.0
