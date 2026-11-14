"""Obstaculo no chao pela camera (2026-11-14).

O detector e provado nas FOTOS REAIS da casa (tests/fixtures/chao/, tiradas
com a cabeca a -25 graus em 14/11): piso branco brilhante com rejunte marrom,
reflexo de lampada, migalhas - e o pano roxo e o tapete enrolado. A vigia e
provada com detector e IA falsos: so pergunta quando confirma algo perto, uma
vez por obstaculo, sem travar o laco da visao, e so para se a IA mandar.
"""
import asyncio
from pathlib import Path

import cv2
import numpy as np
import pytest

from orion.vision.obstaculo_chao import Achado, DetectorChao, VigiaChao, _ler_resposta

FOTOS = Path(__file__).resolve().parents[1] / "fixtures" / "chao"


def _foto(nome):
    imagem = cv2.imread(str(FOTOS / nome))
    assert imagem is not None, f"fixture {nome} sumiu"
    return imagem


def test_piso_limpo_com_rejunte_e_reflexo_nao_tem_nada_perto():
    """chao_0: so piso, rejunte, reflexo e migalhas - nada pode chegar perto."""
    achados = DetectorChao().analisar(_foto("chao_0.jpg"))
    assert not [a for a in achados if a.base >= 0.5], achados


def test_rejunte_grosso_perto_da_camera_nao_e_obstaculo():
    """chao_-30: o rejunte perto da camera engrossa (~12 px) e chega ate a
    borda de baixo - era o falso positivo do prototipo. O pano roxo, longe
    (no alto), continua acusado."""
    achados = DetectorChao().analisar(_foto("chao_-30.jpg"))
    assert not [a for a in achados if a.base >= 0.72], achados
    assert any(a.base < 0.6 and a.x < 0.3 for a in achados), "o pano roxo sumiu"


def test_tapete_enrolado_perto_e_acusado_no_corredor():
    """tapete: o que o LiDAR nao ve e o dono mostrou na foto de 14/11."""
    achados = DetectorChao().analisar(_foto("tapete_perto.jpg"))
    assert any(a.no_corredor and a.base >= 0.72 for a in achados), achados


def test_algo_em_cima_da_faixa_de_referencia_ainda_e_acusado():
    """O obstaculo cobre a propria referencia (muito perto): sem a memoria do
    piso, o detector tomaria o obstaculo por chao e veria nada."""
    d = DetectorChao()
    piso = np.full((480, 640, 3), 200, np.uint8)
    d.analisar(piso)  # aprende o piso
    coberto = piso.copy()
    coberto[380:, :] = (40, 30, 120)  # pano escuro cobrindo a faixa de baixo
    assert any(a.base >= 0.92 for a in d.analisar(coberto))


@pytest.mark.parametrize("texto,esperado", [
    ("PARAR - tapete enrolado no caminho", ("parar", "tapete enrolado no caminho")),
    ("Seguir - so o reflexo da lampada", ("seguir", "so o reflexo da lampada")),
    ("**PARAR** - fio solto", ("parar", "fio solto")),
    ("Nao consigo pensar agora.", ("sem_decisao", "Nao consigo pensar agora.")),
    ("", ("sem_decisao", "")),
])
def test_ler_resposta(texto, esperado):
    assert _ler_resposta(texto) == esperado


class DetectorFalso:
    def __init__(self):
        self.achados = []

    def analisar(self, frame):
        return list(self.achados)


def _vigia(resposta="PARAR - tapete enrolado"):
    relogio = {"t": 1000.0}
    perguntas, comandos, avisos = [], [], []

    async def ia(prompt, jpeg):
        perguntas.append((prompt, jpeg))
        return resposta

    async def nav(dados):
        comandos.append(dados)

    async def publicar(topico, dados):
        avisos.append((topico, dados))

    det = DetectorFalso()
    v = VigiaChao(ia, nav, publicar, detector=det, relogio=lambda: relogio["t"])
    v.ativo = True
    return v, det, relogio, perguntas, comandos, avisos


QUADRO = np.zeros((480, 640, 3), np.uint8)
PERTO = Achado(x=0.5, largura=0.4, base=0.8, area=0.1, no_corredor=True)


async def _quadro(v, relogio, passo=0.5):
    relogio["t"] += passo
    await v.processar_frame(QUADRO)
    if v.tarefa is not None:
        await v.tarefa


@pytest.mark.asyncio
async def test_desligada_nao_olha():
    v, det, relogio, perguntas, *_ = _vigia()
    v.ativo = False
    det.achados = [PERTO]
    for _ in range(4):
        await _quadro(v, relogio)
    assert perguntas == []


@pytest.mark.asyncio
async def test_um_quadro_so_nao_basta_dois_confirmam_e_a_ia_manda_parar():
    v, det, relogio, perguntas, comandos, avisos = _vigia()
    det.achados = [PERTO]
    await _quadro(v, relogio)
    assert perguntas == [], "um quadro pode ser ruido"
    await _quadro(v, relogio)
    assert len(perguntas) == 1
    prompt, jpeg = perguntas[0]
    assert "CHAO" in prompt and jpeg[:2] == b"\xff\xd8"
    assert comandos == [{"acao": "HOLD", "origem": "visao_chao"}]
    assert avisos[0][0] == "vision.obstaculo_chao"
    assert avisos[0][1]["decisao"] == "parar"


@pytest.mark.asyncio
async def test_muito_perto_pergunta_no_primeiro_quadro():
    v, det, relogio, perguntas, *_ = _vigia()
    det.achados = [Achado(0.5, 0.4, 0.95, 0.1, True)]
    await _quadro(v, relogio)
    assert len(perguntas) == 1


@pytest.mark.asyncio
async def test_ia_diz_seguir_ele_nao_para():
    v, det, relogio, perguntas, comandos, avisos = _vigia("SEGUIR - reflexo no piso")
    det.achados = [PERTO]
    await _quadro(v, relogio)
    await _quadro(v, relogio)
    assert len(perguntas) == 1 and comandos == []
    assert avisos[0][1]["decisao"] == "seguir"


@pytest.mark.asyncio
async def test_ia_fora_nao_para_sozinho():
    """Decisao do dono: sensor informa, quem decide e a IA."""
    v, det, relogio, perguntas, comandos, avisos = _vigia("Nao consigo pensar agora.")
    det.achados = [PERTO]
    await _quadro(v, relogio)
    await _quadro(v, relogio)
    assert comandos == []
    assert avisos[0][1]["decisao"] == "sem_decisao"


@pytest.mark.asyncio
async def test_o_mesmo_obstaculo_nao_queima_a_cota():
    v, det, relogio, perguntas, *_ = _vigia("SEGUIR - sombra")
    det.achados = [PERTO]
    for _ in range(10):  # 5 s de obstaculo a frente
        await _quadro(v, relogio)
    assert len(perguntas) == 1


@pytest.mark.asyncio
async def test_longe_ou_fora_do_corredor_nao_pergunta():
    v, det, relogio, perguntas, *_ = _vigia()
    det.achados = [
        Achado(0.5, 0.3, 0.4, 0.1, True),   # longe (no alto da imagem)
        Achado(0.05, 0.1, 0.9, 0.05, False),  # perto, mas fora do caminho
    ]
    for _ in range(4):
        await _quadro(v, relogio)
    assert perguntas == []


@pytest.mark.asyncio
async def test_cabeca_girando_espera_assentar():
    v, det, relogio, perguntas, *_ = _vigia()
    det.achados = [Achado(0.5, 0.4, 0.95, 0.1, True)]

    class Ev:
        dados = {"pan": 30.0}

    await v.ao_mover_cabeca(Ev())
    await v.processar_frame(QUADRO)  # mesmo instante: servo ainda girando
    assert perguntas == []
    await _quadro(v, relogio)
    assert len(perguntas) == 1


@pytest.mark.asyncio
async def test_a_pergunta_nao_trava_o_laco_da_visao():
    lento = asyncio.Event()
    v, det, relogio, perguntas, *_ = _vigia()

    async def ia_lenta(prompt, jpeg):
        await lento.wait()
        return "PARAR - fio"

    v._perguntar_ia = ia_lenta
    det.achados = [Achado(0.5, 0.4, 0.95, 0.1, True)]
    relogio["t"] += 0.5
    await asyncio.wait_for(v.processar_frame(QUADRO), timeout=0.5)
    lento.set()
    await v.tarefa
