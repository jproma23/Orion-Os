"""Mapear a casa (2026-11-14): da fala "mapeia a casa" ao explorador no Nav2.

Nenhum teste chama docker: o explorador e trocado por um falso que escreve o
log como o de verdade (formato copiado do data/mapas/explorar.log de 01/11) e
o GerenteNav2 por um que so conta as chamadas. O que se prova e a COLAGEM -
a parte que faltava e por isso o robo respondia "nao consigo".
"""
import asyncio

import pytest

from motion_core.navigation import mapeamento as modulo
from motion_core.navigation.mapeamento import Mapeamento
from orion.kernel.event_bus import EventBus, Evento
from orion.mission.acoes_faladas import bloco_para_prompt, extrair

LOG_COMPLETO = """\
[INFO] [1793533017.5] [orion_explorador]: aguardando mapa, TF e Nav2...
[INFO] [1.6] [orion_explorador]: alvo 1: (0.16, -0.76), 0.8 m, fronteira de 233 celulas, 3 grupos
[INFO] [1793533140.6] [orion_explorador]: alvo 1: chegou
[INFO] [3.6] [orion_explorador]: alvo 2: (0.51, 0.69), 0.9 m, fronteira de 139 celulas, 3 grupos
[INFO] [1793533215.8] [orion_explorador]: alvo 2: chegou
[INFO] [6.9] [orion_explorador]: sem fronteira alcancavel: exploracao COMPLETA (0 grupos)
[INFO] [1793533217.9] [orion_explorador]: mapa salvo em /opt/orion_nav2/mapas/casa.pgm
"""


class BusFalso:
    def __init__(self):
        self.publicados = []
        self.assinantes = {}

    async def publish(self, topico, dados=None, prioridade=None):
        self.publicados.append((topico, dados))

    def subscribe(self, topico, handler):
        self.assinantes.setdefault(topico, []).append(handler)

    async def entregar(self, topico, dados=None):
        for handler in self.assinantes.get(topico, []):
            await handler(Evento(topico=topico, dados=dados or {}))


class GerenteFalso:
    def __init__(self, sobe=True):
        self.sobe = sobe
        self.subidas = 0
        self.usos = 0

    async def garantir_no_ar(self):
        self.subidas += 1
        return self.sobe

    def marcar_uso(self):
        self.usos += 1


def _explorador_falso(monkeypatch, m, pasta, log=LOG_COMPLETO, espera=None):
    """Troca o `docker exec` por um que escreve o log e sai com 0.

    `espera`: um asyncio.Event - o falso so termina quando ele for setado OU
    quando o PARAR aparecer, como o explorador de verdade.
    """
    chamadas = []

    async def rodar():
        chamadas.append(sorted(p.name for p in pasta.iterdir()))
        if espera is not None:
            while not espera.is_set() and not (pasta / modulo.ARQUIVO_PARAR).exists():
                await asyncio.sleep(0.01)
        texto = log
        if (pasta / modulo.ARQUIVO_PARAR).exists():
            texto += "[INFO] [1.0] [orion_explorador]: alvo 3: parado pelo dono\n"
        (pasta / modulo.ARQUIVO_LOG).write_text(texto)
        return 0

    monkeypatch.setattr(m, "_rodar_explorador", rodar)
    return chamadas


def _terminado(bus):
    return [d for t, d in bus.publicados if t == "navigation.mapeamento_terminado"]


@pytest.mark.asyncio
async def test_mapear_guarda_o_mapa_velho_tira_o_parar_e_termina_completo(tmp_path, monkeypatch):
    """O caso inteiro, com a pasta como ela estava no Pi em 14/11."""
    (tmp_path / "casa.pgm").write_bytes(b"P5 velho")
    (tmp_path / "casa.yaml").write_text("image: casa.pgm\n")
    # O PARAR de 01/11 ainda estava la: sem tira-lo o explorador sairia no
    # primeiro segundo sem mexer uma roda.
    (tmp_path / "PARAR").touch()

    bus, gerente = BusFalso(), GerenteFalso()
    m = Mapeamento(bus, gerente=gerente, pasta_mapas=tmp_path)
    chamadas = _explorador_falso(monkeypatch, m, tmp_path)

    await bus.entregar("navigation.mapear")
    await m._tarefa

    assert gerente.subidas == 1, "o Nav2 tem que subir antes do explorador"
    visto = chamadas[0]
    assert "PARAR" not in visto
    assert "casa.pgm" not in visto, "o mapa velho seria sobrescrito sem aviso"
    assert any(n.startswith("casa_") and n.endswith(".pgm") for n in visto)
    assert any(n.startswith("casa_") and n.endswith(".yaml") for n in visto)

    [fim] = _terminado(bus)
    assert fim["resultado"] == "completo"
    assert fim["alvos"] == 2
    assert m.ultimo == fim


@pytest.mark.asyncio
async def test_para_no_meio_cria_o_parar_e_o_resultado_e_parado(tmp_path, monkeypatch):
    bus = BusFalso()
    m = Mapeamento(bus, gerente=GerenteFalso(), pasta_mapas=tmp_path)
    _explorador_falso(monkeypatch, m, tmp_path, log="", espera=asyncio.Event())

    await bus.entregar("navigation.mapear")
    await asyncio.sleep(0.05)
    assert m.em_andamento

    await bus.entregar("navigation.parar", {"origem": "notebook", "humana": True})
    await asyncio.wait_for(m._tarefa, timeout=1.0)

    assert (tmp_path / "PARAR").exists()
    [fim] = _terminado(bus)
    assert fim["resultado"] == "parado"


@pytest.mark.asyncio
async def test_parar_sem_mapeamento_nao_cria_parar(tmp_path):
    """Todo STOP do robo passa por navigation.parar - e quase nenhum e para
    o mapeamento. Um PARAR solto faria o PROXIMO mapeamento morrer... se o
    proximo nao o apagasse; mesmo assim, nao se deixa lixo."""
    bus = BusFalso()
    Mapeamento(bus, gerente=GerenteFalso(), pasta_mapas=tmp_path)
    await bus.entregar("navigation.parar", {"origem": "operador"})
    assert not (tmp_path / "PARAR").exists()


@pytest.mark.asyncio
async def test_nav2_que_nao_sobe_falha_sem_rodar_o_explorador(tmp_path, monkeypatch):
    bus = BusFalso()
    m = Mapeamento(bus, gerente=GerenteFalso(sobe=False), pasta_mapas=tmp_path)
    chamadas = _explorador_falso(monkeypatch, m, tmp_path)

    await bus.entregar("navigation.mapear")
    await m._tarefa

    assert chamadas == []
    [fim] = _terminado(bus)
    assert fim["resultado"] == "falhou"


@pytest.mark.asyncio
async def test_pedido_repetido_nao_abre_segundo_explorador(tmp_path, monkeypatch):
    """"Mapeia a casa" duas vezes seguidas: um explorador so (dois mandariam
    alvos ao mesmo Nav2, um cancelando o do outro)."""
    bus = BusFalso()
    m = Mapeamento(bus, gerente=GerenteFalso(), pasta_mapas=tmp_path)
    libera = asyncio.Event()
    chamadas = _explorador_falso(monkeypatch, m, tmp_path, espera=libera)

    await bus.entregar("navigation.mapear")
    await asyncio.sleep(0.05)
    await bus.entregar("navigation.mapear")
    libera.set()
    await m._tarefa

    assert len(chamadas) == 1


@pytest.mark.asyncio
async def test_navigation_core_mapear_solta_a_trava_e_chama_o_mapeamento():
    """A acao MAPEAR (o que a fala vira) chega ao Mapeamento pela navegacao -
    e solta a trava da VELOCIDADE que um STOP anterior deixou, senao o Nav2
    mandaria /cmd_vel e as rodas recusariam todos."""
    from conftest import FakeTransporte

    from motion_core.navigation.navigation_core import NavigationCore
    from orion.communication.service import ComunicacaoService

    bus = EventBus()
    comm = ComunicacaoService("motion_core", bus, max_retries=1, ack_timeout_ms=50)
    comm.adicionar_link("hardware_core", FakeTransporte())
    nav = NavigationCore(
        bus, comm, {"max_speed_percent": 60, "min_front_distance_cm": 25, "mission_timeout_s": 30},
        {"patrol_scan_before_segment": True, "obstacle_retry_max": 2,
         "follow_lost_timeout_s": 30, "reduced_speed_near_person_percent": 30},
    )
    pedidos = []

    async def gravar(evento):
        pedidos.append(evento.dados)

    bus.subscribe("navigation.mapear", gravar)
    tarefa_bus = asyncio.create_task(bus.iniciar())
    try:
        nav._velocidade_travada = True  # como fica depois de qualquer STOP
        await bus.publish("navigation.comando", {"acao": "MAPEAR"})
        for _ in range(50):
            if pedidos:
                break
            await asyncio.sleep(0.01)
        assert pedidos == [{}]
        assert not nav.velocidade_travada
    finally:
        bus.parar()
        tarefa_bus.cancel()


def test_a_fala_mapear_casa_vira_a_acao_mapear():
    """O modelo so escolhe o que o prompt lista - e o nome tem que levar a MAPEAR."""
    assert "mapear_casa" in bloco_para_prompt()
    acao, _, _ = extrair("#acao:mapear_casa\nVou mapear a casa.")
    assert acao is not None
    assert acao.entrega == "navegacao"
    assert acao.acao_navegacao == "MAPEAR"


def test_sem_ia_a_frase_do_dono_tambem_mapeia():
    """A frase EXATA de 14/11 tem que acionar mesmo com a cota da IA acabada."""
    from orion.mission.comando_local import casar

    for frase in ("faz um mapeamento de toda cassa", "Jarvis, mapeia a casa inteira",
                  "explora a casa"):
        achado = casar(frase)
        assert achado is not None and achado.acao.nome == "mapear_casa", frase
    assert casar("nao mapeia a casa agora") is None
