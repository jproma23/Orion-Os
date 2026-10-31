"""Testes do Boot Manager (Cap 6 secao 4).

Fase 1 - pronto quando: boot em modo --sim chega a system.ready.
"""
import asyncio
import logging

import pytest

from orion.kernel.boot import BootManager
from orion.kernel.event_bus import Prioridade
from orion.kernel.registry import EstadoModulo


@pytest.mark.asyncio
async def test_boot_sim_chega_a_system_ready(tmp_path):
    recebidos = []

    boot_manager = BootManager(
        caminho_config="config/orion.yaml",
        caminho_log=tmp_path / "orion.log",
        simulado=True,
    )
    sistema = await boot_manager.iniciar()
    sistema.event_bus.subscribe("system.ready", lambda evento: recebidos.append(evento.dados))

    # o system.ready real ja foi publicado e consumido durante o boot; aqui
    # verificamos que o proprio boot concluiu com o registry no estado certo
    # e publicamos um segundo evento so para confirmar que o bus continua
    # funcionando apos o boot.
    await sistema.event_bus.publish("system.ready", {"pos_boot": True}, prioridade=Prioridade.CRITICA)
    await sistema.event_bus.aguardar_fila_vazia()

    assert recebidos == [{"pos_boot": True}]
    assert sistema.registry.obter("mission_core").estado is EstadoModulo.RUNNING

    await sistema.encerrar()


@pytest.mark.asyncio
async def test_boot_encerrar_para_mission_core(tmp_path):
    boot_manager = BootManager(caminho_log=tmp_path / "orion.log", simulado=True)
    sistema = await boot_manager.iniciar()

    await sistema.encerrar()

    assert sistema.registry.obter("mission_core").estado is EstadoModulo.STOPPED



@pytest.mark.asyncio
async def test_boot_nao_aborta_com_modulos_de_fases_futuras_ausentes(tmp_path):
    """Raspberry/Arduino/DB/IA/Vision/Motion Core ainda nao existem neste
    ponto do projeto - o boot deve tolerar isso e nao registra-los, em vez
    de abortar (Cap 6 secao 8)."""
    boot_manager = BootManager(caminho_log=tmp_path / "orion.log", simulado=True)
    sistema = await boot_manager.iniciar()

    for nome in ("raspberry", "hardware_core", "database", "ai", "vision", "motion_hardware"):
        assert not sistema.registry.existe(nome)

    await sistema.encerrar()


# --- O link com o Motion Core no boot pelo systemd (2026-09-20) -------------
#
# Enquanto o Mission Core era subido na mao, o Motion Core ja estava sempre de
# pe e UMA tentativa bastava. Como servico systemd os dois sobem juntos, e o
# discador chega na porta antes de ela existir:
#
#     16:46:59  Raspberry inalcancavel em 127.0.0.1:5757: [Errno 111]
#     ... e 98 "Sem rota conhecida para o destino 'motion_core'" depois
#
# `After=` na unit nao cobre isso: o systemd espera o processo nascer, nao o
# socket abrir.


class _ConfigFalsa:
    def secao(self, nome):
        assert nome == "communication"
        return {"raspberry": {"host": "127.0.0.1", "tcp_port": 5757}}


class _CommFalso:
    def __init__(self):
        self.links = {}
        self.alcancaveis = {}

    def adicionar_link(self, nome, transporte, alcancaveis_via=None):
        self.links[nome] = transporte
        self.alcancaveis[nome] = list(alcancaveis_via or [])


class _BusFalso:
    def __init__(self):
        self.publicados = []

    async def publish(self, topico, dados, prioridade=None):
        self.publicados.append((topico, dados))


def _preparar(monkeypatch, falhas_antes_de_conectar):
    """Instala um TcpTransport que so conecta depois de N recusas."""
    import orion.communication.discovery as discovery
    import orion.communication.transport as transport
    from orion.kernel import boot as modulo_boot

    tentativas = []

    class TcpTransportFalso:
        def __init__(self, host, porta):
            self.host, self.porta = host, porta

        async def conectar(self):
            tentativas.append((self.host, self.porta))
            if len(tentativas) <= falhas_antes_de_conectar:
                # o mesmo erro que o Errno 111 vira no projeto
                raise transport.ErroTransporte(f"Falha ao conectar em {self.host}:{self.porta}")

    class _Info:
        nome, versao_modulo = "motion_core", "0.1.0"

    async def descobrir_falso(*args, **kwargs):
        return _Info()

    monkeypatch.setattr(transport, "TcpTransport", TcpTransportFalso)
    monkeypatch.setattr(discovery, "descobrir", descobrir_falso)
    # sem isto o teste esperaria 2s de verdade entre as tentativas
    monkeypatch.setattr(modulo_boot, "INTERVALO_RETENTATIVA_RASPBERRY_S", 0.01, raising=False)
    return tentativas


@pytest.mark.asyncio
async def test_boot_insiste_ate_o_motion_core_abrir_a_porta(monkeypatch):
    from orion.kernel.boot import _conectar_raspberry

    tentativas = _preparar(monkeypatch, falhas_antes_de_conectar=3)
    comm, bus = _CommFalso(), _BusFalso()

    ok = await _conectar_raspberry(comm, _ConfigFalsa(), bus, logging.getLogger("teste"))

    assert ok is True
    assert len(tentativas) == 4, "deveria ter insistido ate a porta abrir"
    assert "motion_core" in comm.links, "sem o link, toda chamada de memoria morre"
    # O corpo mora atras do Motion Core: sem esta rota, "anda para frente"
    # morria em "Sem rota conhecida para o destino 'hardware_core'".
    assert comm.alcancaveis["motion_core"] == ["hardware_core"]
    assert bus.publicados == [], "nao e erro: o Motion Core so estava subindo"


@pytest.mark.asyncio
async def test_boot_desiste_e_tolera_ausencia_quando_a_paciencia_acaba(monkeypatch):
    """Insistir nao pode virar travar: Motion Core realmente ausente ainda
    deixa o robo subir (Cap 6 s.8), so que sem memoria - e dizendo isso."""
    from orion.kernel import boot as modulo_boot
    from orion.kernel.boot import _conectar_raspberry

    tentativas = _preparar(monkeypatch, falhas_antes_de_conectar=10_000)
    monkeypatch.setattr(modulo_boot, "ESPERA_TOTAL_RASPBERRY_S", 0.05, raising=False)
    comm, bus = _CommFalso(), _BusFalso()

    ok = await _conectar_raspberry(comm, _ConfigFalsa(), bus, logging.getLogger("teste"))

    assert ok is False
    assert tentativas, "tem que ter tentado pelo menos uma vez"
    assert "motion_core" not in comm.links
    assert bus.publicados[0][0] == "diagnostic.error"
    assert bus.publicados[0][1]["motivo"] == "inalcancavel"


@pytest.mark.asyncio
async def test_boot_simulado_continua_sem_abrir_soquete(monkeypatch):
    """--sim nao pode discar: rodar o boot para testar ROUBAVA o enlace do
    processo de producao (EDR-0023 s.5). Agora que ele insiste, quebrar isto
    custaria caro - entao fica travado por teste."""
    from orion.kernel.boot import _conectar_raspberry

    tentativas = _preparar(monkeypatch, falhas_antes_de_conectar=0)
    comm, bus = _CommFalso(), _BusFalso()

    ok = await _conectar_raspberry(
        comm, _ConfigFalsa(), bus, logging.getLogger("teste"), simulado=True
    )

    assert ok is False
    assert tentativas == []


# --- Reconexao do link com o Motion Core EM OPERACAO (2026-09-20) ----------
#
# O boot que insiste (acima) cobre so a corrida de subida. Se o link cair
# DEPOIS - Motion Core reiniciado, processo morto, socket derrubado - ele nao
# voltava: 98 "Sem rota conhecida para o destino 'motion_core'" num dia, com o
# robo falando normalmente e sem memoria nenhuma.


class _HeartbeatFalso:
    """So guarda o que foi registrado - a maquina de verdade tem testes
    proprios em test_heartbeat.py; aqui o que se prova e o CALLBACK."""

    def __init__(self):
        self.registros = []

    def monitorar(self, nome, reconectar=None, heartbeats_perdidos_limite=None):
        self.registros.append((nome, reconectar, heartbeats_perdidos_limite))


@pytest.mark.asyncio
async def test_link_caido_reconecta_sozinho(monkeypatch):
    from orion.kernel import boot as modulo_boot

    tentativas = []

    async def conectar_falso(comm, config, event_bus, logger, simulado=False, espera_total_s=None):
        tentativas.append(espera_total_s)
        # falha nas duas primeiras, conecta na terceira
        return len(tentativas) >= 3

    monkeypatch.setattr(modulo_boot, "_conectar_raspberry", conectar_falso)
    monkeypatch.setattr(modulo_boot, "RECONEXAO_ESPERA_INICIAL_S", 0.01)
    monkeypatch.setattr(modulo_boot, "RECONEXAO_ESPERA_MAXIMA_S", 0.01)

    heartbeat, bus = _HeartbeatFalso(), _BusFalso()
    ao_perder = modulo_boot._criar_reconexao_motion_core(
        _CommFalso(), _ConfigFalsa(), bus, logging.getLogger("teste"), heartbeat, 45
    )

    await ao_perder()
    # o callback delega para uma tarefa; espera ela terminar
    for _ in range(200):
        await asyncio.sleep(0.01)
        if heartbeat.registros:
            break

    assert len(tentativas) == 3, "deveria ter insistido ate reconectar"
    assert all(e == 0.0 for e in tentativas), (
        "cada ciclo pede UMA discagem: o backoff e do callback, nao do _conectar_raspberry"
    )
    # Re-armou o monitoramento - sem isto a PROXIMA queda nao seria detectada.
    assert heartbeat.registros[0][0] == "motion_core"
    assert heartbeat.registros[0][1] is ao_perder, "o callback tem que continuar armado"
    assert heartbeat.registros[0][2] == 45, "a tolerancia propria do link tem que sobreviver"
    assert ("comm.module_recovered", {"modulo": "motion_core"}) in bus.publicados


@pytest.mark.asyncio
async def test_duas_perdas_nao_abrem_dois_lacos_de_discagem(monkeypatch):
    """O Motion Core so aceita UM enlace "mission_core" por vez (EDR-0023 s.5):
    dois lacos discando em duplicata brigariam pelo mesmo nome."""
    from orion.kernel import boot as modulo_boot

    chamadas = []
    liberar = asyncio.Event()

    async def conectar_falso(comm, config, event_bus, logger, simulado=False, espera_total_s=None):
        chamadas.append(1)
        await liberar.wait()  # segura a primeira tentativa em curso
        return True

    monkeypatch.setattr(modulo_boot, "_conectar_raspberry", conectar_falso)
    heartbeat = _HeartbeatFalso()
    ao_perder = modulo_boot._criar_reconexao_motion_core(
        _CommFalso(), _ConfigFalsa(), _BusFalso(), logging.getLogger("teste"), heartbeat, None
    )

    await ao_perder()
    await asyncio.sleep(0.02)
    await ao_perder()  # segunda perda enquanto a primeira ainda insiste
    await asyncio.sleep(0.02)

    assert len(chamadas) == 1, "a segunda perda nao pode abrir um segundo laco"

    liberar.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_boot_simulado_nao_monta_heartbeat(tmp_path):
    """--sim nao abre enlace, entao nao ha o que monitorar - e um monitor
    ligado ali ficaria tentando discar para o Pi de producao."""
    from orion.kernel.boot import BootManager

    sistema = await BootManager(caminho_log=tmp_path / "orion.log", simulado=True).iniciar()
    assert sistema.heartbeat is None
    await sistema.encerrar()
