"""Testes dos comportamentos concretos plugados no maestro (EDR-0020)."""
import asyncio

import pytest

from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamentos import (
    TIMEOUT_INATIVIDADE_MANUAL_S,
    Atender,
    ControleManual,
    IaEstrategica,
    Mentor,
    Repouso,
    Vigilia,
    VigilanciaObstaculo,
)
from orion.kernel.event_bus import EventBus


async def _passo(maestro):
    maestro.pedir_reavaliacao()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_repouso_assume_quando_ninguem_mais_quer():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"  # sem obstáculo -> base assume

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


class _RespostaFalsa:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _ComunicacaoFalsa:
    """Fake de ComunicacaoService so para IaEstrategica.request() (EDR-0022)
    - nao abre link nenhum de verdade."""

    def __init__(self, acao: str | None = "observar_ambiente", falha: bool = False) -> None:
        self._acao = acao
        self._falha = falha
        self.pedidos: list[dict] = []

    async def request(self, destino, payload, timeout_s=None):
        self.pedidos.append(payload)
        if self._falha:
            raise TimeoutError("Notebook inalcancavel (simulado)")
        return _RespostaFalsa({"ok": True, "acao": self._acao})


@pytest.mark.asyncio
async def test_ia_estrategica_assume_com_acao_valida():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(acao="observar_ambiente"))
    maestro.registrar(ia_estrategica)
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"  # ainda sem decisao da IA

    await ia_estrategica._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "ia_estrategica"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_ia_estrategica_descansar_nao_disputa_o_controle():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(acao="descansar"))
    maestro.registrar(ia_estrategica)
    laco = asyncio.create_task(maestro.executar())

    await ia_estrategica._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"  # "descansar" = nao assume o controle

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_ia_estrategica_sem_notebook_nao_derruba_o_maestro():
    """Sem internet/Notebook, a IA simplesmente nao tem opiniao - Repouso
    segue no controle normalmente (Cap 6 s.8)."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(falha=True))
    maestro.registrar(ia_estrategica)
    laco = asyncio.create_task(maestro.executar())

    await ia_estrategica._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"
    assert ia_estrategica.quer_rodar() is False

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


class _ComunicacaoFalsaMentor:
    """Fake de ComunicacaoService so para Mentor._consultar() - resposta no
    formato de behavior.aconselhar (comportamento/motivo), diferente do
    formato de mission.decidir (acao) usado por _ComunicacaoFalsa acima."""

    def __init__(self, comportamento: str | None = "observar_ambiente", falha: bool = False) -> None:
        self._comportamento = comportamento
        self._falha = falha
        self.pedidos: list[dict] = []

    async def request(self, destino, payload, timeout_s=None):
        self.pedidos.append(payload)
        if self._falha:
            raise TimeoutError("Notebook inalcancavel (simulado)")
        if self._comportamento is None:
            return _RespostaFalsa({"ok": False})
        return _RespostaFalsa(
            {"ok": True, "comportamento": self._comportamento, "motivo": "teste"}
        )


@pytest.mark.asyncio
async def test_mentor_assume_com_conselho_aceito():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    mentor = Mentor(bus, _ComunicacaoFalsaMentor(comportamento="observar_ambiente"))
    maestro.registrar(mentor)
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"  # ainda sem conselho

    await mentor._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "mentor"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_mentor_descansar_nao_disputa_o_controle():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    mentor = Mentor(bus, _ComunicacaoFalsaMentor(comportamento="descansar"))
    maestro.registrar(mentor)
    laco = asyncio.create_task(maestro.executar())

    await mentor._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_mentor_perde_para_ia_estrategica_quando_as_duas_opinam():
    """Prioridade do Mentor (35) e menor que a da IaEstrategica (40) de
    proposito - a resposta mais rapida/atual vence quando as duas tem
    opiniao (ver docstring de Mentor)."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    mentor = Mentor(bus, _ComunicacaoFalsaMentor(comportamento="observar_ambiente"))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(acao="observar_ambiente"))
    maestro.registrar(mentor)
    maestro.registrar(ia_estrategica)
    laco = asyncio.create_task(maestro.executar())

    await mentor._consultar()
    await ia_estrategica._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "ia_estrategica"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_mentor_sem_notebook_nao_derruba_o_maestro():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    mentor = Mentor(bus, _ComunicacaoFalsaMentor(falha=True))
    maestro.registrar(mentor)
    laco = asyncio.create_task(maestro.executar())

    await mentor._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"
    assert mentor.quer_rodar() is False

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_preempta_ia_estrategica():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(acao="observar_ambiente"))
    maestro.registrar(ia_estrategica)
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await ia_estrategica._consultar()
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "ia_estrategica"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"  # 100 > 40, sempre

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_preempta_o_repouso_e_libera_ao_sair():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    vigilancia = VigilanciaObstaculo(bus)
    maestro.registrar(vigilancia)
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # Mega reporta obstáculo -> vigilância (prio 100) assume
    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"

    # obstáculo liberado -> volta ao repouso
    await bus.publish("motion.status", {"estado": "IDLE"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_atender_preempta_repouso_e_manda_hold():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # dono chamou "Fofão" (evento vindo do Notebook)
    await bus.publish("voice.wake_detected", {"texto_janela": "fofão"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"
    assert {"acao": "HOLD"} in comandos_nav  # parou o robô ao atender

    # resposta terminou -> volta ao repouso
    await bus.publish("voice.response_finished", {"texto": "pronto"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_controle_manual_assume_e_libera_apos_inatividade():
    """Ate 2026-07-27 o /comando da webui falava com o Hardware Core direto,
    entao o maestro nunca sabia que o dashboard tinha assumido o robo. Este
    teste cobre o caminho que corrige isso: o mesmo navigation.comando que
    o NavigationCore executa tambem acorda o ControleManual."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos_nav = []
    bus.subscribe("navigation.comando", lambda e: comandos_nav.append(e.dados))

    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # simula o /comando da webui publicando o mesmo evento que o
    # NavigationCore consome (joystick empurrado para frente)
    await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    # soltou o joystick (sem novo comando) -> depois do timeout de
    # inatividade, devolve o controle e manda HOLD como rede de seguranca
    await asyncio.sleep(TIMEOUT_INATIVIDADE_MANUAL_S + 0.3)
    assert maestro.ativo_nome == "repouso"
    assert {"acao": "HOLD"} in comandos_nav

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_controle_manual_continua_ativo_enquanto_comando_se_repete():
    """O joystick reenvia o comando a cada 300ms enquanto seguro
    (dashboard.js) - isso tem que manter o controle_manual no ar, sem
    esperar o timeout de inatividade."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    # repete a cada 0.3s (como o joystick faz) por mais tempo que o
    # timeout de inatividade sozinho toleraria
    for _ in range(4):
        await asyncio.sleep(TIMEOUT_INATIVIDADE_MANUAL_S / 2)
        await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    assert maestro.ativo_nome == "controle_manual"  # nunca soltou

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_controle_manual_ignora_comando_de_navegacao_nao_manual():
    """GOTO/PATROL/HOLD publicados por outros modulos (ex.: Atender manda
    HOLD) nao podem acordar o controle manual - so acao=MANUAL conta como
    presenca do operador."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("navigation.comando", {"acao": "HOLD"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_vence_controle_manual():
    """Seguranca (100) preempta ate o controle manual (95): o operador
    dirigindo nunca pode ficar acima da seguranca reativa."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_controle_manual_vence_atender():
    """O operador dirigindo pelo dashboard (95) e mais explicito que o
    dono chamando por voz (80) - se o joystick for usado durante um
    atendimento, o controle manual assume."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("voice.wake_detected", {"texto_janela": "fofão"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"

    await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_vence_atender():
    """Segurança (100) preempta até o Atender (80): se surge obstáculo
    enquanto atende, a vigilância assume."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("voice.wake_detected", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilancia_obstaculo"  # 100 > 80

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_vigilia_investiga_e_libera_e_perde_para_atender():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    fotos = []
    bus.subscribe("sentinela.capturar_foto", lambda e: fotos.append(e.dados))

    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    maestro.registrar(Vigilia(bus, duracao_alerta_s=0.15))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    # alerta Sentinela -> vigília assume, pede foto
    await bus.publish("sentinela.alerta", {"tipo": "barulho"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilia"
    assert fotos and fotos[0]["motivo"] == "barulho"

    # dono chama "Fofão" no meio -> Atender (80) preempta a Vigília (60)
    await bus.publish("voice.wake_detected", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"

    # atendimento acaba -> vigília RETOMA (alerta não foi apagado)
    await bus.publish("voice.response_finished", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "vigilia"

    # deixa a investigação concluir -> libera para repouso
    await asyncio.sleep(0.2)
    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus
