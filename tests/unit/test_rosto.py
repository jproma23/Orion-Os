"""O rosto leve (2026-11-12): estado, envelope da fala, leitor SSE e desenho."""
import importlib.util
import os
import random
from pathlib import Path

import pytest

from orion.display import rosto_estado as re_
from orion.display.rosto_estado import EstadoRosto

np = pytest.importorskip("numpy")


def _estado(agora=0.0):
    return EstadoRosto(agora, sorteio=random.Random(1))


# ------------------------------------------------------------ expressoes


@pytest.mark.parametrize(
    "voz, esperado",
    [
        ("LISTENING", re_.OUVINDO),
        ("THINKING", re_.PENSANDO),
        ("SPEAKING", re_.FALANDO),
        ("ERROR", re_.TRISTE),
        ("IDLE", re_.NEUTRO),
    ],
)
def test_estado_da_voz_vira_expressao(voz, esperado):
    e = _estado()
    e.ao_evento("voice.status", {"estado": voz}, 1.0)
    assert e.quadro(1.0).expressao == esperado


def test_feliz_ao_reconhecer_e_passa():
    e = _estado()
    e.ao_evento("vision.person_recognized", {"centro_x": 0.5}, 1.0)
    assert e.quadro(1.1).expressao == re_.FELIZ
    assert e.quadro(1.0 + re_.DURACAO_FELIZ_S + 0.1).expressao == re_.NEUTRO


def test_susto_com_obstaculo():
    e = _estado()
    e.ao_evento("motion.obstacle_front", {}, 1.0)
    assert e.quadro(1.1).expressao == re_.SUSTO


def test_sonolento_depois_de_muito_tempo_sem_nada():
    e = _estado(0.0)
    assert e.quadro(re_.OCIOSO_PARA_SONOLENTO_S + 1).expressao == re_.SONOLENTO
    e.ao_evento("voice.status", {"estado": "IDLE"}, re_.OCIOSO_PARA_SONOLENTO_S + 2)
    assert e.quadro(re_.OCIOSO_PARA_SONOLENTO_S + 2).expressao == re_.NEUTRO


def test_falando_vence_o_feliz_passageiro():
    e = _estado()
    e.ao_evento("voice.saudacao", {}, 1.0)
    e.ao_evento("voice.status", {"estado": "SPEAKING"}, 1.1)
    assert e.quadro(1.2).expressao == re_.FALANDO


# ------------------------------------------------------------ boca e olhos


def test_boca_segue_o_envelope_da_fala_e_fecha_no_fim():
    e = _estado()
    e.ao_evento("voice.status", {"estado": "SPEAKING"}, 0.0)
    e.ao_evento("voice.fala_envelope", {"passo_s": 0.04, "niveis": [1.0] * 25 + [0.0] * 25}, 0.0)
    t = 0.0
    while t < 0.9:  # 30 fps durante a parte alta
        t += 1 / 30
        aberta = e.quadro(t).abertura_boca
    assert aberta > 0.8
    while t < 2.0:  # parte baixa + depois do fim do envelope
        t += 1 / 30
        fechada = e.quadro(t).abertura_boca
    assert fechada < 0.05


def test_boca_fechada_fora_da_fala():
    e = _estado()
    e.ao_evento("voice.fala_envelope", {"passo_s": 0.04, "niveis": [1.0] * 50}, 0.0)
    assert e.quadro(0.5).abertura_boca == 0.0  # nao esta SPEAKING


def test_pisca_sozinho():
    e = _estado()
    aberturas = [e.quadro(i / 30).abertura_olhos for i in range(30 * 8)]
    assert min(aberturas) < 0.2 and max(aberturas) == 1.0


def test_olhos_vao_para_a_pessoa_e_voltam():
    e = _estado()
    e.ao_evento("vision.person_detected", {"caixa": [560, 100, 640, 400]}, 0.0)
    t = 0.0
    for _ in range(15):
        t += 1 / 30
        x = e.quadro(t).olhar_x
    assert x > 0.6  # pessoa na direita da imagem
    for _ in range(90):  # sem ver ninguem por 3 s
        t += 1 / 30
        x = e.quadro(t).olhar_x
    assert abs(x) < 0.05


# ------------------------------------------------------------ envelope da fala


def test_envelope_da_fala_abre_na_voz_e_fecha_na_pausa():
    from orion.voice.audio_utils import envelope_da_fala

    taxa = 16000
    t = np.arange(int(taxa * 0.4)) / taxa
    voz = (8000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    silencio = np.zeros(int(taxa * 0.4), np.int16)
    niveis = envelope_da_fala(np.concatenate([voz, silencio, voz]), taxa)
    assert len(niveis) == 30  # 1,2 s / 40 ms
    assert min(niveis[:10]) > 0.9 and max(niveis[10:20]) == 0.0 and min(niveis[20:]) > 0.9
    assert envelope_da_fala(np.zeros(10, np.int16), taxa) == []


# ------------------------------------------------------------ SSE e desenho

pygame = pytest.importorskip("pygame")
_spec = importlib.util.spec_from_file_location(
    "rosto_tool", Path(__file__).resolve().parents[2] / "tools" / "rosto.py"
)
rosto_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rosto_tool)


def test_leitor_sse_entrega_eventos_e_ignora_ping():
    linhas = [
        b": ping\n", b"\n",
        b"event: voice.status\n", b'data: {"estado": "SPEAKING"}\n', b"\n",
        b"event: lixo\n", b"data: {nao e json\n", b"\n",
    ]
    recebidos = []
    rosto_tool.ler_sse(linhas, lambda t, d: recebidos.append((t, d)))
    assert recebidos == [("voice.status", {"estado": "SPEAKING"})]


@pytest.mark.parametrize(
    "expressao",
    [
        re_.NEUTRO, re_.OUVINDO, re_.PENSANDO, re_.FALANDO,
        re_.FELIZ, re_.SUSTO, re_.TRISTE, re_.SONOLENTO,
    ],
)
def test_desenha_toda_expressao_sem_tela(expressao):
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from orion.display.rosto_desenho import COR, desenhar
    from orion.display.rosto_estado import Rosto

    tela = pygame.Surface((960, 540))
    desenhar(tela, Rosto(expressao, 1.0, 0.3, -0.2, 0.6))
    # tem rosto desenhado: algum pixel na cor do traco
    assert any(
        tela.get_at((x, y))[:3] == COR for x in range(0, 960, 4) for y in range(0, 540, 4)
    )


# ------------------------------------------------ Firefox so durante a aula


class _ProcessoFalso:
    def __init__(self):
        self.vivo = True

    def poll(self):
        return None if self.vivo else 0

    def terminate(self):
        self.vivo = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.vivo = False


def _navegador():
    abertos = []

    def lancar():
        abertos.append(_ProcessoFalso())
        return abertos[-1]

    return rosto_tool.NavegadorDaAula(lancar=lancar), abertos


def test_firefox_abre_no_comeco_e_fecha_depois_da_graca():
    nav, abertos = _navegador()
    nav.ao_evento("display.aula", {"estado": "comecou"}, 0.0)
    assert nav.aberto and len(abertos) == 1
    nav.ao_evento("display.aula", {"passo": 3}, 5.0)  # pulso nao mexe
    nav.ao_evento("display.aula", {"estado": "terminou"}, 60.0)
    nav.tique(60.0 + rosto_tool.GRACA_FIM_AULA_S - 1)
    assert nav.aberto  # ainda mostrando o fechamento
    nav.tique(60.0 + rosto_tool.GRACA_FIM_AULA_S + 0.1)
    assert not nav.aberto


def test_aula_nova_na_graca_nao_fecha_nem_abre_outro():
    nav, abertos = _navegador()
    nav.ao_evento("display.aula", {"estado": "comecou"}, 0.0)
    nav.ao_evento("display.aula", {"estado": "terminou"}, 10.0)
    nav.ao_evento("display.aula", {"estado": "comecou"}, 12.0)
    nav.tique(10.0 + rosto_tool.GRACA_FIM_AULA_S + 1)
    assert nav.aberto and len(abertos) == 1


def test_firefox_que_cai_no_meio_da_aula_reabre():
    nav, abertos = _navegador()
    nav.ao_evento("display.aula", {"estado": "comecou"}, 0.0)
    abertos[0].vivo = False
    nav.tique(rosto_tool.ESPERA_REABRIR_S + 0.1)
    assert nav.aberto and len(abertos) == 2


def test_fora_da_aula_nunca_abre():
    nav, abertos = _navegador()
    for t in range(0, 100, 5):
        nav.ao_evento("voice.status", {"estado": "SPEAKING"}, float(t))
        nav.tique(float(t))
    assert abertos == []
