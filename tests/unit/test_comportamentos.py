"""Testes dos comportamentos concretos plugados no maestro (EDR-0020)."""
import asyncio

import pytest

from motion_core.behavior import comportamentos
from motion_core.behavior.behavior_core import BehaviorCore
from motion_core.behavior.comportamentos import (
    TIMEOUT_INATIVIDADE_MANUAL_S,
    Atender,
    AtenderGesto,
    ControleManual,
    IaEstrategica,
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
        # Protocolo do conselheiro (`behavior.aconselhar`), que a IA passou a
        # usar na fusao de 2026-07-28: resposta com enum + motivo.
        return _RespostaFalsa({"ok": True, "comportamento": self._acao, "motivo": "teste"})


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
async def test_ia_estrategica_fora_do_contrato_nao_disputa_o_controle():
    """Regra 6 do contrato: resposta fora do catalogo vira abstencao, nunca
    chute. "descansar" saiu do vocabulario em 2026-07-28 (decisao do usuario:
    nao existe descansar no diretorio do maestro), entao serve de amostra de
    resposta invalida - e o robo tem que seguir como se a IA nao tivesse
    falado nada."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia_estrategica = IaEstrategica(bus, _ComunicacaoFalsa(acao="descansar"))
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


@pytest.mark.asyncio
async def test_gesto_vem_liga_os_motores():
    """O teste que faltava.

    A primeira versao deste comportamento mandava FOLLOW, e FOLLOW so GIRA
    para centralizar quem o robo RECONHECE - nunca anda ate a pessoa. Relato
    do usuario, com o robo na bancada: "chamei com a mao e ele nao ligou os
    motores". Este teste falha na versao antiga e passa na nova.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(AtenderGesto(bus, duracao_s=0.05))
    laco = asyncio.create_task(maestro.executar())

    # chamado vindo da DIREITA do quadro (centro_x > 0.5)
    await bus.publish("gesto.detectado", {"tipo": "vem", "centro_x": 0.9})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    await asyncio.sleep(1.2)
    await bus.aguardar_fila_vazia()

    enviados = [c.get("comando") for c in comandos if c.get("acao") == "MANUAL"]
    assert "TURN_RIGHT" in enviados, f"nao girou para a pessoa: {comandos}"
    assert "MOVE_FORWARD" in enviados, f"nao andou ate a pessoa: {comandos}"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_gesto_vem_da_esquerda_gira_para_a_esquerda():
    """O sentido do giro segue a mesma convencao do vision.person_recognized
    (0..1 no quadro, 0.5 no centro) - se os dois caminhos discordassem, o
    robo viraria para o lado errado num deles."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(AtenderGesto(bus, duracao_s=0.05))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("gesto.detectado", {"tipo": "vem", "centro_x": 0.1})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    await asyncio.sleep(1.2)
    await bus.aguardar_fila_vazia()

    enviados = [c.get("comando") for c in comandos if c.get("acao") == "MANUAL"]
    assert "TURN_LEFT" in enviados, f"girou para o lado errado: {comandos}"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_gesto_vem_centralizado_nao_gira_a_toa():
    """Quem ja esta a frente nao precisa de giro - so de avanco."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(AtenderGesto(bus, duracao_s=0.05))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("gesto.detectado", {"tipo": "vem", "centro_x": 0.5})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    await asyncio.sleep(0.4)
    await bus.aguardar_fila_vazia()

    enviados = [c.get("comando") for c in comandos if c.get("acao") == "MANUAL"]
    assert "MOVE_FORWARD" in enviados
    assert "TURN_RIGHT" not in enviados and "TURN_LEFT" not in enviados

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_gesto_sim_nao_move_o_robo():
    """Joinha e confirmacao, nao ordem de andar. Um "sim" que fizesse o robo
    vir seria pior que nao ter gesto nenhum."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(AtenderGesto(bus, duracao_s=0.05))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("gesto.detectado", {"tipo": "sim", "centro_x": 0.9})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    await asyncio.sleep(0.4)
    await bus.aguardar_fila_vazia()

    assert comandos == [], f"o sim mexeu o robo: {comandos}"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


# ---------- o obstaculo tem que fazer o robo DESVIAR, nao congelar ----------


async def _rodar_vigilancia(bus, maestro, segundos: float):
    """Coloca um obstaculo e deixa o maestro trabalhar por um tempo."""
    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    await asyncio.sleep(segundos)
    await bus.aguardar_fila_vazia()


@pytest.mark.asyncio
async def test_obstaculo_gira_em_vez_de_congelar(monkeypatch):
    """REGRESSAO do comportamento de ate 2026-07-28.

    `VigilanciaObstaculo.executar` era `while True: sleep(1.0)`: diante de um
    obstaculo real o robo tomava o controle com prioridade 100 e ficava
    parado ate alguem tirar o obstaculo da frente. Nenhum teste pegava isso
    porque nao dormir nao e erro - so a ausencia de comando denuncia.
    """
    monkeypatch.setattr(comportamentos, "PAUSA_APOS_GIRO_DESVIO_S", 0.05)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _rodar_vigilancia(bus, maestro, 0.2)

    assert maestro.ativo_nome == "vigilancia_obstaculo"
    assert comandos, "obstaculo detectado e o robo nao tentou desviar (congelou)"
    assert all(c["acao"] == "GIRAR" for c in comandos), comandos
    assert all(c["graus"] == comportamentos.PASSO_GIRO_DESVIO_GRAUS for c in comandos)

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_desvio_nao_usa_acao_manual(monkeypatch):
    """O giro de fuga NAO pode se passar por joystick.

    `ControleManual` (prio 95) assina navigation.comando e assume o controle
    em qualquer `acao: MANUAL`. Se o desvio usasse esse verbo, ao liberar a
    vigilancia o maestro entregaria o robo a um "operador" que nao existe,
    por TIMEOUT_INATIVIDADE_MANUAL_S - e a autonomia ficaria presa atras dele.
    """
    monkeypatch.setattr(comportamentos, "PAUSA_APOS_GIRO_DESVIO_S", 0.05)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    manual = ControleManual(bus)
    maestro.registrar(manual)
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _rodar_vigilancia(bus, maestro, 0.2)

    assert not manual.quer_rodar(), "o desvio acordou o controle manual"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_desvio_inverte_o_sentido_quando_o_lado_nao_abre(monkeypatch):
    """O sentido inicial e arbitrario de proposito (o mapeamento entre angulo
    do radar e lado fisico nunca foi medido nesta montagem) - o que impede o
    palpite errado de custar caro e o laco se corrigir sozinho."""
    monkeypatch.setattr(comportamentos, "PAUSA_APOS_GIRO_DESVIO_S", 0.02)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    # tempo para passar de TENTATIVAS_ANTES_DE_INVERTER giros
    await _rodar_vigilancia(bus, maestro, 0.25)

    sentidos = [c["sentido"] for c in comandos]
    assert len(set(sentidos)) == 2, f"nunca tentou o outro lado: {sentidos}"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_desvio_para_quando_a_frente_libera(monkeypatch):
    """Quem decide que liberou e o firmware: o maestro preempta a vigilancia
    quando motion.status sai de OBSTACLE_DETECTED. Nao ha segunda fonte de
    verdade sobre isso dentro do comportamento."""
    monkeypatch.setattr(comportamentos, "PAUSA_APOS_GIRO_DESVIO_S", 0.02)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _rodar_vigilancia(bus, maestro, 0.1)
    await bus.publish("motion.status", {"estado": "IDLE"})
    await asyncio.sleep(0.05)
    await _passo(maestro)
    quantos_ao_liberar = len(comandos)
    await asyncio.sleep(0.15)
    await bus.aguardar_fila_vazia()

    assert maestro.ativo_nome == "repouso"
    assert len(comandos) == quantos_ao_liberar, "continuou girando com a frente livre"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus
