"""Testes dos comportamentos concretos plugados no maestro (EDR-0020)."""
import asyncio
import logging

import pytest

from motion_core.behavior import behavior_core, comportamentos
from motion_core.behavior.behavior_core import MODO_MANUAL, BehaviorCore
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
async def test_obstaculo_nao_tira_o_controle_de_quem_decide():
    """INVERTIDO em 2026-07-30, por decisao do dono.

    Este teste afirmava o contrario: que a vigilancia (100) preemptava a IA
    (40) diante do obstaculo. Era verdade, e era o defeito - a IA perdia o
    controle exatamente no instante em que havia uma manobra para decidir, e o
    `GIRAR` da vigilancia apagava a decisao dela na vaga do NavigationCore.

    O dono: *"vigilancia tem que vigiar e mostrar dados e nao decidir; a ia
    estrategica é quem com os dados em maos decide a manobra"*. Entao o
    obstaculo agora e INFORMACAO para quem ja esta no controle, nao um motivo
    para trocar de dono.
    """
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
    assert maestro.ativo_nome == "ia_estrategica", (
        "a vigilancia tomou o controle de quem devia decidir a manobra"
    )

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_nao_preempta_ninguem():
    """A vigilancia informa sem tomar o volante (2026-07-30).

    Antes ela preemptava o repouso e devolvia ao sair, e o teste media essa
    troca. Como ela deixou de comandar, nao ha mais troca para medir: o
    obstaculo aparece, o fato e publicado, e quem estava no controle continua.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    pedidos = []
    bus.subscribe(comportamentos.TOPICO_RECONSIDERAR, lambda e: pedidos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    await bus.aguardar_fila_vazia()
    assert maestro.ativo_nome == "repouso"
    assert pedidos, "o obstaculo nao virou informacao para ninguem"

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

    # dono chamou "Javis" (evento vindo do Notebook)
    await bus.publish("voice.wake_detected", {"texto_janela": "jarvis"})
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
    await maestro.definir_modo_controle(MODO_MANUAL)

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
    # O joystick só entra na arbitragem com o volante na mão do operador
    # (2026-07-30) - o maestro nasce em AUTONOMO.
    await maestro.definir_modo_controle(MODO_MANUAL)

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
    await maestro.definir_modo_controle(MODO_MANUAL)

    await bus.publish("navigation.comando", {"acao": "HOLD"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_obstaculo_nao_arranca_o_joystick_da_mao_do_operador():
    """Quem protege o operador e o PISO, nao uma troca de comportamento.

    Este teste afirmava que a vigilancia (100) preemptava o controle manual
    (95) no obstaculo. Ela nao comanda mais, entao o operador segue dirigindo
    - e quem impede a batida continua sendo a parada reativa do Mega, que e
    independente do maestro (Cap 18) e nunca foi afrouxada. O obstaculo vira
    informacao na tela, no lugar de um solavanco no controle.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())
    await maestro.definir_modo_controle(MODO_MANUAL)

    await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

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
    await maestro.definir_modo_controle(MODO_MANUAL)

    await bus.publish("voice.wake_detected", {"texto_janela": "jarvis"})
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
async def test_obstaculo_nao_interrompe_o_atendimento():
    """Atender o dono (80) nao e mais cortado por um obstaculo.

    Fazia sentido quando a vigilancia precisava do controle para girar; agora
    ela informa, e cortar a conversa no meio para anunciar uma parede seria
    piorar o atendimento sem ganhar seguranca nenhuma - o piso reativo do Mega
    e o mesmo com o Atender no controle.
    """
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
    assert maestro.ativo_nome == "atender"

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

    # dono chama "Javis" no meio -> Atender (80) preempta a Vigília (60)
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
async def test_vigilancia_informa_e_nao_comanda():
    """A vigilancia VIGIA E INFORMA - decisao do dono em 2026-07-30.

    Ate esta data ela assumia o controle em prioridade 100 e girava em laco,
    escolhendo a manobra por conta propria. O dono: *"vigilancia tem que
    vigiar e mostrar dados e nao decidir; a ia estrategica é quem com os dados
    em maos decide a manobra"*.

    Duas afirmacoes, e as duas importam: ela nao manda comando de navegacao
    NENHUM, e nao tira o controle de quem decide.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    pedidos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    bus.subscribe(comportamentos.TOPICO_RECONSIDERAR, lambda e: pedidos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    await _rodar_vigilancia(bus, maestro, 0.2)

    assert not comandos, f"a vigilancia comandou a navegacao: {comandos}"
    assert pedidos, "viu o obstaculo e nao informou ninguem"
    assert pedidos[0]["motivo"] == "obstaculo_frontal"
    assert maestro.ativo_nome == "repouso", (
        "a vigilancia tomou o controle - com isso a IA (prio 40) nunca manobra"
    )

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_vigilancia_nao_atropela_a_decisao_da_ia():
    """REGRESSAO do bug medido em producao em 2026-07-30.

    O `NavigationCore` guarda UM comando pendente ("o ultimo vence", sem fila,
    de proposito - comando de joystick velho executado atrasado e pior que
    descartado). A vigilancia em prioridade 100 preemptava a IA em 40 e
    publicava `GIRAR` na mesma vaga, apagando o EXPLORE que a IA tinha acabado
    de deixar la. Resultado no log: **5444 "IA decidiu" e ZERO
    "explore: comecando"** - sem uma linha denunciando o atropelo.

    Aqui o teste olha a vaga do jeito que o NavigationCore olha: o ULTIMO
    comando publicado. Se a vigilancia voltar a comandar, ele deixa de ser o
    da IA e este teste falha.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(VigilanciaObstaculo(bus))
    laco = asyncio.create_task(maestro.executar())

    # a IA decidiu explorar e deixou o comando na vaga
    await bus.publish("navigation.comando", {"acao": "EXPLORE"})
    await bus.aguardar_fila_vazia()
    # ... e o obstaculo aparece logo depois
    await _rodar_vigilancia(bus, maestro, 0.2)

    assert comandos[-1]["acao"] == "EXPLORE", (
        f"a decisao da IA foi sobrescrita antes de executar: {comandos}"
    )

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_ia_reconsidera_na_hora_em_vez_de_esperar_o_ciclo():
    """Sem o laco de giro da vigilancia, a manobra e decisao da IA - e esperar
    o ciclo normal deixaria o robo encostado no obstaculo ate a proxima
    consulta. O dono pediu o oposto: *"e nao fica parado"*.

    O intervalo aqui e longo de proposito: se o atalho nao funcionar, o teste
    estoura no timeout em vez de passar por acidente de temporizacao.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = _ComunicacaoFalsa(acao="virar_esquerda")
    ia = IaEstrategica(bus, comm, intervalo_s=300.0)
    tarefa_ia = ia.iniciar()

    # deixa a primeira consulta (a do boot) acontecer e conta quantas foram
    await asyncio.sleep(0.1)
    consultas_iniciais = len(comm.pedidos)

    await bus.publish(
        comportamentos.TOPICO_RECONSIDERAR,
        {"origem": "vigilancia_obstaculo", "motivo": "obstaculo_frontal"},
    )
    await bus.aguardar_fila_vazia()
    await asyncio.sleep(0.1)

    assert len(comm.pedidos) > consultas_iniciais, (
        "pediram decisao imediata e a IA continuou dormindo o ciclo inteiro"
    )

    ia.parar()
    tarefa_ia.cancel()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_vigilancia_nunca_assume_o_controle():
    """Rede contra a volta silenciosa do problema: se alguem devolver o
    controle a esta classe, `executar` falha alto em vez de voltar a decidir
    manobra em dois lugares ao mesmo tempo."""
    bus = EventBus()
    vigilancia = VigilanciaObstaculo(bus)

    await bus.publish("motion.status", {"estado": "OBSTACLE_DETECTED"})
    assert vigilancia.quer_rodar() is False

    with pytest.raises(RuntimeError, match="NAO assume o controle"):
        await vigilancia.executar()


@pytest.mark.asyncio
async def test_ia_estrategica_recebe_o_quadro_do_mundo():
    """REGRESSAO de 2026-07-29: o contexto desta IA era literalmente
    `{"comportamento_ativo": "repouso"}`.

    Ela escolhia entre avancar, girar e seguir pessoa sem saber se havia
    obstaculo, gente na sala ou bateria. O `EstadoDoMundo` montava
    esse quadro inteiro e o entregava ao `ponte_conselho`, que esta desabilitado
    - o retrato ia para um caminho desligado enquanto quem decide recebia uma
    linha.
    """
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = _ComunicacaoFalsa(acao="observar_ambiente")
    ia = IaEstrategica(
        bus,
        comm,
        montar_contexto=lambda: "obstaculo_frente_cm: 34\npessoa_nome: João Paulo",
    )

    await ia._consultar()

    assert comm.pedidos, "nao consultou a IA"
    contexto = comm.pedidos[-1]["contexto"]
    assert "obstaculo_frente_cm: 34" in contexto
    assert "João Paulo" in contexto
    assert "comportamento_ativo" in contexto  # o que ela ja tinha continua indo

    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_ia_consulta_mesmo_se_o_contexto_falhar():
    """Decidir com pouco contexto e ruim; decidir nunca e pior (Cap 6 s.8).
    Sem esta garantia, um erro ao montar o retrato deixaria o robo sem IA
    nenhuma e ninguem saberia por que."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comm = _ComunicacaoFalsa(acao="observar_ambiente")

    def _explode():
        raise RuntimeError("telemetria fora do ar")

    ia = IaEstrategica(bus, comm, montar_contexto=_explode)

    await ia._consultar()

    assert comm.pedidos, "a falha ao montar contexto cancelou a consulta inteira"
    assert "comportamento_ativo" in comm.pedidos[-1]["contexto"]

    bus.parar()
    await tarefa_bus


# ---------------------------------------------------------------------------
# A RONDA DEIXOU DE SER INALCANCAVEL (2026-07-30)
#
# Ela estava registrada no maestro desde 19/07 e rodou 0 vezes no log inteiro.
# Nao era desinteresse da IA - eram DUAS travas independentes:
#   1. `ronda` nao estava no catalogo do contrato, e `resolver()` devolve None
#      para tudo que nao esta la: a IA nao PODIA escolhe-la;
#   2. o unico caminho ate `Ronda.pedir()` passava pela `ponte_conselho`, que
#      e None porque `conselho_ia.habilitado: false` no orion.yaml.
#
# E havia uma terceira, que so apareceu ao consertar as duas: a IaEstrategica
# vive em prioridade 40 e SEGURA o controle no laco de `executar()`. A Ronda
# vive em 20. Sem soltar o controle, a IA pediria a ronda e engoliria o
# proprio pedido - trocando uma trava por outra.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ia_pede_olhar_em_volta_e_a_ronda_assume_de_verdade():
    """Ponta a ponta: a IA escolhe, o evento sai, a Ronda ASSUME o controle.

    Este teste existe porque "o fio foi ligado" e "o fio funciona" sao coisas
    diferentes neste projeto - e a diferenca ja custou dias.
    """
    from motion_core.behavior.comportamentos import Ronda

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ronda = Ronda(bus)
    maestro.registrar(ronda, discricionario=True)
    ia = IaEstrategica(bus, _ComunicacaoFalsa(acao="olhar_em_volta"))
    maestro.registrar(ia)
    laco = asyncio.create_task(maestro.executar())

    await _passo(maestro)
    assert maestro.ativo_nome == "repouso"
    assert not ronda.quer_rodar()

    await ia._consultar()
    # A IA assume, publica `behavior.ronda` e solta; o bus entrega; a Ronda
    # passa a querer rodar; o maestro entrega a vez.
    for _ in range(12):
        await bus.aguardar_fila_vazia()
        await asyncio.sleep(0.05)
        if maestro.ativo_nome == "ronda":
            break

    assert ronda.quer_rodar(), "a IA pediu e a Ronda nao ficou sabendo"
    assert maestro.ativo_nome == "ronda", "a Ronda foi pedida e nunca assumiu"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_acao_delegada_faz_a_ia_soltar_o_controle():
    """A terceira trava, isolada: sem soltar, a IA de prio 40 seguraria o
    controle e a Ronda de prio 20 nunca teria vez."""
    from motion_core.behavior.comportamentos import Ronda

    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Ronda(bus), discricionario=True)
    ia = IaEstrategica(bus, _ComunicacaoFalsa(acao="olhar_em_volta"))
    maestro.registrar(ia)
    laco = asyncio.create_task(maestro.executar())

    await ia._consultar()
    for _ in range(12):
        await bus.aguardar_fila_vazia()
        await asyncio.sleep(0.05)
        if not ia.quer_rodar():
            break

    assert not ia.quer_rodar(), "a IA delegou e continuou querendo o controle"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_acao_nao_delegada_continua_segurando_o_controle():
    """CONTRAPROVA. Sem ela, a mudanca acima poderia ter feito a IA soltar o
    controle SEMPRE - e `avancar`/`explorar` deixariam de ter dono, o que e
    uma regressao silenciosa e bem pior que o bug original."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia = IaEstrategica(bus, _ComunicacaoFalsa(acao="observar_ambiente"))
    maestro.registrar(ia)
    laco = asyncio.create_task(maestro.executar())

    await ia._consultar()
    await asyncio.sleep(0.1)

    assert ia.quer_rodar()
    assert maestro.ativo_nome == "ia_estrategica"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_volante_volta_para_auto_depois_de_ocioso(monkeypatch):
    """*"quando passar mais de 3 minutos ocioso passar para auto"* (dono, 30/07).

    Sem isto, um clique em "manual" deixava o Jarvis parado para sempre esperando
    alguem lembrar de devolver o controle - e foi o que aconteceu na bancada: o
    dono achou que o rele tinha queimado, quando o robo so estava em manual sem
    ninguem no joystick.

    O tempo e encurtado aqui porque o teste mede a REGRA, nao os 180 s.
    """
    monkeypatch.setattr(behavior_core, "SEGUNDOS_OCIOSO_ATE_AUTO", 0.15)
    # O ControleManual segura `quer_rodar()` por TIMEOUT_INATIVIDADE_MANUAL_S
    # (1,5 s em producao) a cada comando, e enquanto ele quer rodar a atividade
    # e renovada - encurtar os dois e o que deixa o teste medir a regra em vez
    # de esperar 3 minutos de relogio.
    monkeypatch.setattr(comportamentos, "TIMEOUT_INATIVIDADE_MANUAL_S", 0.05)
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())

    await maestro.definir_modo_controle(MODO_MANUAL)
    assert maestro.modo_controle == MODO_MANUAL

    # operador dirigindo: enquanto chega comando, o volante FICA com ele
    for _ in range(3):
        await bus.publish("navigation.comando", {"acao": "MANUAL", "comando": "MOVE_CONTINUOUS"})
        await asyncio.sleep(0.02)
    assert maestro.modo_controle == MODO_MANUAL, "o volante fugiu da mao do operador"

    # ... e parou de mexer
    await asyncio.sleep(0.5)
    assert maestro.modo_controle == "autonomo", "ficou preso em manual apos a ociosidade"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


# ---------------------------------------------------------------------------
# GOAP LIGADO NA IA (2026-08-03)
#
# O planejador existia desde 02/08 - 500 linhas, testes, travas de import - e
# `modelo_goap` so era importado por `tests/unit/test_goap.py`. Nada em
# producao o chamava, apesar de o docstring do `goap.py` dizer "isto SUBSTITUI
# a escolha de acao unica". Estes testes cobrem a ligacao que faltava.
# ---------------------------------------------------------------------------


class _RetratoFalso:
    """Duble do RetratoDoMundo - so os campos que `goap.estado_de` le."""

    def __init__(self, **campos) -> None:
        self.telemetria_viva = campos.get("telemetria_viva", True)
        self.obstaculo_frente_cm = campos.get("obstaculo_frente_cm", 200.0)
        self.bateria_nivel = campos.get("bateria_nivel", "OK")
        self.pessoa_presente = campos.get("pessoa_presente", False)
        self.pessoa_conhecida = campos.get("pessoa_conhecida", False)
        self.estado_hardware = campos.get("estado_hardware", "IDLE")


@pytest.mark.asyncio
async def test_goap_oferece_objetivos_e_nao_acoes():
    """Com retrato em maos, a IA escolhe OBJETIVO. E a troca de vocabulario que
    o GOAP significa - e ela nao exige nada do outro lado do fio, porque o
    conselheiro do Notebook so usa `opcoes` como enum."""
    from motion_core.behavior import modelo_goap

    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="entender_o_ambiente")
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso())

    await ia._consultar()

    assert comm.pedidos[0]["opcoes"] == list(modelo_goap.NOMES_OBJETIVOS)
    assert "avancar" not in comm.pedidos[0]["opcoes"]


@pytest.mark.asyncio
async def test_goap_monta_plano_de_dois_passos():
    """O caso que o esquema de acao unica NUNCA conseguiu produzir: querer
    seguir alguem sem estar vendo ninguem. Antes `seguir_pessoa` simplesmente
    nao servia e nada sabia dizer "entao olhe em volta primeiro"."""
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    # Frente bloqueada e desconhecida: chegar a "frente livre" exige medir
    # antes de girar.
    retrato = _RetratoFalso(obstaculo_frente_cm=None)
    ia = IaEstrategica(bus, comm, retrato=lambda: retrato)

    await ia._consultar()

    assert len(ia._plano_atual) == 2, ia._plano_atual
    assert ia._plano_atual[0] == "observar_ambiente"
    # SO o primeiro passo vira acao: o mundo pode mudar antes do segundo.
    assert ia._acao_atual == "observar_ambiente"
    assert ia._objetivo_atual == "poder_seguir_em_frente"


@pytest.mark.asyncio
async def test_goap_sem_caminho_e_abstencao_e_nao_chute():
    """Corpo mudo: nao ha plano para quase nada. Abster-se e a resposta certa,
    e o maestro segue pela escada de prioridades."""
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso(telemetria_viva=False))

    await ia._consultar()

    assert ia._acao_atual is None
    assert ia.quer_rodar() is False


@pytest.mark.asyncio
async def test_goap_objetivo_fora_da_lista_vira_abstencao():
    """Mesma regra 6 do contrato, agora no vocabulario dos objetivos."""
    bus = EventBus()
    ia = IaEstrategica(bus, _ComunicacaoFalsa(acao="dominar_o_mundo"),
                       retrato=lambda: _RetratoFalso())

    await ia._consultar()

    assert ia._acao_atual is None
    assert ia._plano_atual == []


@pytest.mark.asyncio
async def test_retrato_quebrado_nao_derruba_a_ia():
    """Ler o mundo pode falhar. Sem estado nao ha plano - mas o robo continua
    de pe, decidindo pela escada normal."""
    bus = EventBus()

    def _explode():
        raise RuntimeError("barramento caiu (simulado)")

    ia = IaEstrategica(bus, _ComunicacaoFalsa(acao="entender_o_ambiente"), retrato=_explode)

    await ia._consultar()  # nao levanta

    assert ia._acao_atual is None


@pytest.mark.asyncio
async def test_sem_retrato_continua_no_modo_antigo():
    """Retrocompatibilidade: sem modelo de mundo (ferramentas de bancada,
    testes antigos) a IA volta a escolher UMA acao, como antes."""
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="observar_ambiente")
    ia = IaEstrategica(bus, comm)  # sem retrato

    await ia._consultar()

    assert "observar_ambiente" in comm.pedidos[0]["opcoes"]
    assert ia._acao_atual == "observar_ambiente"
    assert ia._plano_atual == []


# ---------------------------------------------------------------------------
# PROMESSA VAZIA: a acao que promete um efeito e nunca o entrega (2026-08-04)
#
# O robo passou 8 horas repetindo, de 31 em 31 segundos:
#
#     IA decidiu 'observar_ambiente' (nao sei a distancia a frente)
#     objetivo 'entender_o_ambiente' -> plano observar_ambiente
#
# `observar_ambiente` promete `frente_conhecida: True`. Com os ultrassons
# desplugados (escolha do dono desde 03/08) esse efeito nunca chega, e o
# planejador acreditava na promessa para sempre. Nada denunciava: sem erro,
# sem excecao, sem teste vermelho.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promessa_nunca_cumprida_tira_a_acao_do_planejador():
    """O laco de producao inteiro, reproduzido: sensor de frente mudo e a IA
    pedindo 'entender_o_ambiente' a cada ciclo.

    Sem o conserto este teste roda 20 ciclos e a acao continua sendo planejada
    nos 20 - que e exatamente o que o robo fez durante 8 horas.
    """
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="entender_o_ambiente")
    # Ultrassom fora do ar: `obstaculo_frente_cm` e None para sempre, entao
    # `frente_conhecida` nunca vira True por mais que o robo olhe.
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso(obstaculo_frente_cm=None))

    for _ in range(20):
        await ia._consultar()

    # As DUAS acoes que prometem `frente_conhecida` foram desmascaradas.
    assert "observar_ambiente" in ia._acoes_sem_efeito
    assert "olhar_em_volta" in ia._acoes_sem_efeito
    # E sem elas nao ha caminho ate o objetivo: a IA se abstem em vez de
    # repetir o mesmo passo inutil, e o maestro segue pela escada.
    assert ia._acao_atual is None
    assert ia.quer_rodar() is False


@pytest.mark.asyncio
async def test_acao_que_cumpre_a_promessa_continua_no_planejador():
    """A trava nao pode morder quem trabalha.

    Com o sensor de frente RESPONDENDO, `observar_ambiente` entrega o que
    promete e tem que seguir disponivel - senao o conserto acima teria custado
    o planejador inteiro.
    """
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    # Frente medida e bloqueada (10 cm): `frente_conhecida` e True, entao a
    # promessa de `observar_ambiente` esta cumprida a cada ciclo.
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso(obstaculo_frente_cm=10.0))

    for _ in range(20):
        await ia._consultar()

    assert ia._acoes_sem_efeito == set()
    assert ia._promessas_quebradas == {}


@pytest.mark.asyncio
async def test_acao_descartada_volta_quando_o_efeito_reaparece():
    """O descarte e temporario, e a volta NAO depende de a acao ser planejada
    de novo.

    Este e o ponto que quase virou bug: uma acao fora do planejador nunca mais
    seria escolhida, entao esperar por uma nova execucao dela para reabilita-la
    seria esperar para sempre. A conferencia e feita contra o ESTADO DO MUNDO -
    o ToF instalado, o ultrassom religado, e ela volta.
    """
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="entender_o_ambiente")
    retrato = _RetratoFalso(obstaculo_frente_cm=None)
    ia = IaEstrategica(bus, comm, retrato=lambda: retrato)

    for _ in range(20):
        await ia._consultar()
    assert "observar_ambiente" in ia._acoes_sem_efeito

    # O sensor volta a responder.
    retrato.obstaculo_frente_cm = 150.0
    await ia._consultar()

    assert ia._acoes_sem_efeito == set()


@pytest.mark.asyncio
async def test_acao_sem_efeito_modelado_nunca_e_descartada():
    """`avancar` e a maioria das acoes de movimento tem efeito VAZIO de
    proposito (ver o cabecalho do modelo_goap): elas mudam o mundo fisico sem
    mudar predicado nenhum. Quem nao promete nada nao pode ser cobrado - se
    fosse, o robo perderia justamente as acoes que o movem."""
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    # Frente livre e conhecida: o plano ate `poder_seguir_em_frente` ja esta
    # satisfeito, entao forcamos o passo pela mao para cobrar `avancar`.
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso())

    ia._passo_anterior = "avancar"
    for _ in range(20):
        await ia._consultar()
        ia._passo_anterior = "avancar"

    assert "avancar" not in ia._acoes_sem_efeito


@pytest.mark.asyncio
async def test_plano_repetido_nao_repete_a_linha_de_log(caplog):
    """O log e ferramenta de diagnostico, nao metrica de ciclo.

    Em 2026-08-04 o mesmo plano foi registrado 3.051 vezes e foi preciso
    filtrar o log com `grep -v` para conseguir ler qualquer outra coisa.
    """
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso(obstaculo_frente_cm=10.0))

    with caplog.at_level(logging.INFO, logger="motion_core.behavior.comportamentos"):
        for _ in range(5):
            await ia._consultar()

    linhas = [r for r in caplog.records if "-> plano" in r.getMessage()]
    assert len(linhas) == 1, [r.getMessage() for r in linhas]


@pytest.mark.asyncio
async def test_girar_sem_achar_saida_nao_tira_o_girar_do_planejador():
    """A trava de promessa NAO pode morder quem so tentou.

    Este teste existe por um defeito MEU, pego pelo teste do log em
    2026-08-04: a primeira versao de `_cobrar_promessas` cobrava todo efeito,
    e `virar_esquerda` promete `frente_livre: True`. O modelo diz, com todas as
    letras, que ali e INTENCAO - girar procurando saida. Um robo encarando um
    canto giraria tres vezes, nao acharia saida nenhuma e perderia a acao de
    girar, ficando preso exatamente onde ela e indispensavel.

    Ele fica 20 ciclos de cara para uma parede que nao sai do lugar.
    """
    bus = EventBus()
    comm = _ComunicacaoFalsa(acao="poder_seguir_em_frente")
    # Parede a 10 cm que nunca some, por mais que ele gire.
    ia = IaEstrategica(bus, comm, retrato=lambda: _RetratoFalso(obstaculo_frente_cm=10.0))

    for _ in range(20):
        await ia._consultar()

    assert "virar_esquerda" not in ia._acoes_sem_efeito
    assert "virar_direita" not in ia._acoes_sem_efeito
    # E ele continua tentando, que e o comportamento certo.
    assert ia._acao_atual in ("virar_esquerda", "virar_direita")


@pytest.mark.asyncio
async def test_velocidade_do_nav2_conta_como_operador_no_volante():
    """2026-11-01: o Nav2 guiava pelo /velocidade e o maestro achava que
    ninguem dirigia - em 3 min devolvia o volante a IA e o Nav2 levava 409."""
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(ControleManual(bus))
    laco = asyncio.create_task(maestro.executar())
    await maestro.definir_modo_controle(MODO_MANUAL)

    await bus.publish("navigation.comando", {"acao": "VELOCIDADE", "v_m_s": 0.1, "w_rad_s": 0.0})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "controle_manual"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_pedir_manual_de_novo_zera_o_cronometro():
    """2026-11-01: MANUAL tocado de novo era ignorado e o volante voltava a IA
    21 s depois, contando da primeira entrega."""
    bus = EventBus()
    maestro = BehaviorCore(bus)
    await maestro.definir_modo_controle(MODO_MANUAL)
    maestro._ultima_atividade_manual -= 170.0
    antes = maestro._ultima_atividade_manual
    await maestro.definir_modo_controle(MODO_MANUAL)
    assert maestro._ultima_atividade_manual > antes + 100


class _ComunicacaoComValor(_ComunicacaoFalsa):
    def __init__(self, acao: str, valor: float) -> None:
        super().__init__(acao=acao)
        self._valor = valor

    async def request(self, destino, payload, timeout_s=None):
        resposta = await super().request(destino, payload, timeout_s)
        resposta.payload["valor"] = self._valor
        return resposta


async def _executar_ia_com(acao: str, valor: float) -> list[dict]:
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    comandos = []
    bus.subscribe("navigation.comando", lambda e: comandos.append(e.dados))
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    ia = IaEstrategica(bus, _ComunicacaoComValor(acao, valor))
    maestro.registrar(ia)
    laco = asyncio.create_task(maestro.executar())
    await ia._consultar()
    await asyncio.sleep(0.1)
    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus
    return comandos


@pytest.mark.asyncio
async def test_ia_manda_o_numero_andar_400_mm_vira_goto_de_40_cm():
    """2026-11-01: o dono quer que ela saiba e MANDE quanto andar/desviar."""
    comandos = await _executar_ia_com("andar_mm", 400)
    assert {"acao": "GOTO", "graus": 0.0, "distancia_cm": 40.0} in comandos


@pytest.mark.asyncio
async def test_numero_acima_do_teto_e_cortado_e_sem_numero_nao_anda():
    assert {"acao": "GIRAR", "sentido": "DIREITA", "graus": 180.0} in await _executar_ia_com(
        "girar_direita_graus", 720
    )
    assert await _executar_ia_com("andar_mm", 0) == []



@pytest.mark.asyncio
async def test_ia_estrategica_nao_atropela_o_me_siga():
    """08/11 16:41: 6 s depois do "me siga" a IA decidiu explorar e trocou o
    FOLLOW pelo explore. Em FOLLOW ela nao consulta."""
    from motion_core.behavior.comportamentos import IaEstrategica
    from orion.kernel.event_bus import Evento as _Evento

    class _CommQueFalha:
        chamadas = 0

        async def request(self, *a, **k):
            _CommQueFalha.chamadas += 1
            raise AssertionError("nao era para consultar a IA em FOLLOW")

    ia = IaEstrategica.__new__(IaEstrategica)
    ia._modo_navegacao = None
    await IaEstrategica._ao_mudar_modo_navegacao(ia, _Evento(topico="navigation.mode_changed", dados={"modo": "FOLLOW"}))
    ia._comm = _CommQueFalha()
    await IaEstrategica._consultar(ia)
    assert _CommQueFalha.chamadas == 0


@pytest.mark.asyncio
async def test_atender_solta_no_fim_de_sessao_sem_resposta():
    # "Jarvis" e silencio: nao ha response_finished, so o fim de sessao.
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("voice.wake_detected", {"texto_janela": "jarvis"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"
    await bus.publish("voice.session_ended", {})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_atender_solta_sozinho_no_teto_quando_o_fim_se_perde():
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    maestro = BehaviorCore(bus)
    maestro.registrar(Repouso(bus))
    maestro.registrar(Atender(bus, teto_s=0.1))
    laco = asyncio.create_task(maestro.executar())

    await bus.publish("voice.wake_detected", {"texto_janela": "jarvis"})
    await asyncio.sleep(0.05)
    assert maestro.ativo_nome == "atender"
    await asyncio.sleep(0.2)  # nenhum fim chegou do Notebook
    assert maestro.ativo_nome == "repouso"

    maestro.parar()
    await laco
    bus.parar()
    await tarefa_bus
