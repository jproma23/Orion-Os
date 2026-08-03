"""Testes do modulo de visao e do contrato ModuloOrion (EDR-0023).

Usa um nucleo dublê no lugar do VisionCore real: assim o ciclo de vida
(subir, vigiar, desligar) e testado sem camera, sem YOLO e sem
face_recognition instalados.
"""
import asyncio

import pytest

from orion.kernel.event_bus import EventBus
from orion.kernel.modulo import ModuloOrion
from orion.vision.modulo import ModuloVisao, _nome_do_arquivo_do_modelo

CONFIG_VISAO = {
    "resolution": [640, 480],
    "fps": 15,
    "yolo_model": "yolov8n",
    "confidence_threshold": 0.55,
    "pan_limits_degrees": [-80, 80],
    "tilt_limits_degrees": [-30, 45],
    "servo_max_speed_deg_s": 60,
    "camera_indice_principal": 2,
    "camera_frontal_espelhada": True,
}


class NucleoFalso:
    """Imita o VisionCore: um executar() que roda ate parar() ser chamado."""

    def __init__(self, falhar: bool = False) -> None:
        self.falhar = falhar
        self.parou = False
        self.rodou = False

    async def executar(self) -> None:
        self.rodou = True
        if self.falhar:
            raise RuntimeError("camera pegou fogo")
        while not self.parou:
            await asyncio.sleep(0.01)

    def parar(self) -> None:
        self.parou = True


def test_modelo_ganha_sufixo_pt():
    # sem o .pt o Ultralytics trata como nome de catalogo e tenta baixar -
    # quebrando a promessa de funcionar sem internet
    assert _nome_do_arquivo_do_modelo("yolov8n") == "yolov8n.pt"
    assert _nome_do_arquivo_do_modelo("yolov8n.pt") == "yolov8n.pt"


def test_cumpre_o_contrato_de_modulo():
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso())
    assert isinstance(modulo, ModuloOrion)
    assert modulo.nome == "vision"


@pytest.mark.asyncio
async def test_iniciar_sobe_o_nucleo_e_fica_saudavel():
    nucleo = NucleoFalso()
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo)

    assert not modulo.esta_saudavel()  # antes de iniciar, nao esta de pe
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert nucleo.rodou
    assert modulo.esta_saudavel()
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_encerrar_solta_o_nucleo_e_e_idempotente():
    nucleo = NucleoFalso()
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo)
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    await modulo.encerrar()
    assert nucleo.parou
    assert not modulo.esta_saudavel()

    # contrato do EDR-0023: encerrar() nunca levanta, mesmo repetido - o
    # desligamento tambem roda depois de falha parcial de boot
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_nucleo_que_morre_deixa_o_modulo_doente():
    """Camera que explode no meio do caminho nao pode passar por saudavel -
    e assim que o Watchdog vai perceber o problema (Cap 6 s.8)."""
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso(falhar=True))
    await modulo.iniciar()
    await asyncio.sleep(0.05)

    assert not modulo.esta_saudavel()
    await modulo.encerrar()


# ----- confirmacao de alvo vem da config (2026-07-29) -----
#
# O numero vinha FIXO em 3 no `_construir`, contra a regra 6 da arquitetura, e
# por nao estar na config nunca apareceu em investigacao nenhuma. Ele e o nó do
# impasse medido em producao: `vision.person_detected` sai a cada UM quadro e e
# nele que o AcompanharPessoa (prio 45) TRANCA a cabeca, mas so com alvo
# CONFIRMADO a visao a move. Com 3 aqui, 1 quadro trancava e 3 eram precisos
# para mover - 208 trancas numa hora e zero movimento.


def _modulo_com(conf_extra: dict) -> ModuloVisao:
    return ModuloVisao(EventBus(), {**CONFIG_VISAO, **conf_extra})


def test_confirmacao_de_alvo_vem_do_orion_yaml(monkeypatch):
    # O monkeypatch abaixo aponta para dentro de `vision_core`, e RESOLVER esse
    # nome importa o modulo de verdade - com cv2, YOLO e tudo. O import tardio
    # do `_construir` protege quem so importa este arquivo, mas nao protege
    # quem mira o alvo. No Pi (sem o extra `vision`) isto falhava; skipar e o
    # mesmo tratamento que test_vision_core.py ja da.
    pytest.importorskip("cv2")
    capturado = {}

    class VisionCoreEspiao:
        def __init__(self, *_args, **kwargs):
            capturado.update(kwargs)

    # `_construir` faz `from orion.vision.vision_core import VisionCore` DENTRO
    # do metodo (import tardio, para nao exigir cv2/YOLO so por importar este
    # arquivo) - entao o nome a trocar mora no modulo de origem.
    monkeypatch.setattr("orion.vision.vision_core.VisionCore", VisionCoreEspiao)

    _modulo_com({"target_confirmation_frames": 4})._construir()
    assert capturado["frames_confirmacao_alvo"] == 4


def test_confirmacao_de_alvo_default_e_1_nao_3(monkeypatch):
    """Config antiga sem a chave nao pode reintroduzir o impasse: o default
    tem que ser 1, o valor que alinha a tranca com o motor."""
    pytest.importorskip("cv2")  # mesmo motivo do teste acima
    capturado = {}

    class VisionCoreEspiao:
        def __init__(self, *_args, **kwargs):
            capturado.update(kwargs)

    # `_construir` faz `from orion.vision.vision_core import VisionCore` DENTRO
    # do metodo (import tardio, para nao exigir cv2/YOLO so por importar este
    # arquivo) - entao o nome a trocar mora no modulo de origem.
    monkeypatch.setattr("orion.vision.vision_core.VisionCore", VisionCoreEspiao)

    _modulo_com({})._construir()
    assert capturado["frames_confirmacao_alvo"] == 1


# ---------------------------------------------------------------------------
# A VISAO NUNCA ERA PAUSADA NO ALIVIO DE CARGA (corrigido em 2026-07-30)
#
# O ModuloVisao so expunha iniciar()/encerrar(), e por isso `conversar_fofao.py`
# passava `pausar_visao=None` ao AlivioCarga. O maior consumidor de CPU/RAM do
# Notebook (YOLO + reconhecimento facial) seguia rodando a todo vapor durante
# um aperto de memoria. Pendencia declarada em comentario desde o merge de
# 26/07 - eram exatamente estes dois metodos que faltavam.
# ---------------------------------------------------------------------------


class _NucleoPausavel:
    """Dublê que so registra se foi pausado/retomado."""

    def __init__(self) -> None:
        self.pausado = False

    def pausar(self) -> None:
        self.pausado = True

    def retomar(self) -> None:
        self.pausado = False


def test_pausar_e_retomar_chegam_ao_nucleo():
    nucleo = _NucleoPausavel()
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo)

    modulo.pausar()
    assert nucleo.pausado, "o alivio de carga pediu e a visao continuou rodando"

    modulo.retomar()
    assert not nucleo.pausado


def test_pausar_nunca_levanta_mesmo_com_peca_quebrada():
    """Alivio parcial e melhor que nenhum: sob pressao de memoria, uma peca
    que falha ao pausar nao pode impedir as outras de pausarem nem derrubar
    quem pediu."""

    class _NucleoQuebrado:
        def pausar(self):
            raise RuntimeError("boom")

        def retomar(self):
            raise RuntimeError("boom")

    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=_NucleoQuebrado())

    modulo.pausar()   # nao levanta
    modulo.retomar()  # nao levanta
