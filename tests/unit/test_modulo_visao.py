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

    def carregar_pessoas_conhecidas(self, pessoas) -> None:
        self.familia = list(pessoas)


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
    pytest.importorskip("face_recognition")
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
    pytest.importorskip("face_recognition")
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
# O ModuloVisao so expunha iniciar()/encerrar(), e por isso `conversar_jarvis.py`
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


# --- Os rostos da familia depois de um boot em que o Pi ainda nao subiu ---
#
# Falha real de 2026-08-05: o Notebook ligou antes do Pi, a busca dos rostos
# aconteceu UMA vez e falhou, e o robo passou o dia inteiro sem reconhecer
# ninguem - sem erro, sem teste reprovando e sem nada denunciando.

ROSTO = {"id": 1, "nome": "João Paulo", "embedding_face": b"x" * 1024}


class _BancoQueDemora:
    """Recusa as `falhas_antes` primeiras chamadas, como o enlace ainda subindo."""

    def __init__(self, falhas_antes: int) -> None:
        self.falhas_antes = falhas_antes
        self.chamadas = 0

    async def __call__(self):
        self.chamadas += 1
        if self.chamadas <= self.falhas_antes:
            raise ConnectionError("link com o Pi ainda nao esta de pe")
        return [ROSTO]


@pytest.mark.asyncio
async def test_rostos_entram_quando_o_pi_sobe_depois(monkeypatch):
    monkeypatch.setattr("orion.vision.modulo.INTERVALO_RETENTATIVA_FAMILIA_S", 0.01)
    nucleo = NucleoFalso()
    banco = _BancoQueDemora(falhas_antes=2)
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo, carregar_conhecidos=banco)

    await modulo.iniciar()
    await asyncio.sleep(0.1)

    # o boot falhou de carregar (as 2 primeiras chamadas recusaram) - e era
    # nesse estado que o robo ficava preso o dia inteiro, tratando o dono
    # como estranho. O que importa e que ele NAO desiste no boot.
    assert banco.chamadas > 1, "so tentou uma vez - voltou a ser a carga de tiro unico"
    assert nucleo.familia == [ROSTO], "os rostos tinham que entrar assim que o Pi respondesse"
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_banco_vazio_tambem_faz_insistir(monkeypatch):
    # lista vazia nao levanta excecao, mas deixa o robo igualmente cego para
    # quem e da casa - tem que insistir do mesmo jeito
    monkeypatch.setattr("orion.vision.modulo.INTERVALO_RETENTATIVA_FAMILIA_S", 0.01)
    nucleo = NucleoFalso()
    respostas = [[], [], [ROSTO]]

    async def _banco():
        return respostas.pop(0)

    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=nucleo, carregar_conhecidos=_banco)
    await modulo.iniciar()
    await asyncio.sleep(0.1)

    assert nucleo.familia == [ROSTO]
    await modulo.encerrar()


@pytest.mark.asyncio
async def test_encerrar_mata_a_retentativa_dos_rostos(monkeypatch):
    # tarefa orfa tentando para sempre depois do desligamento ja custou caro
    # neste projeto - ver EDR-0023
    monkeypatch.setattr("orion.vision.modulo.INTERVALO_RETENTATIVA_FAMILIA_S", 0.01)
    banco = _BancoQueDemora(falhas_antes=10_000)
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso(), carregar_conhecidos=banco)

    await modulo.iniciar()
    await asyncio.sleep(0.05)
    await modulo.encerrar()

    parou_em = banco.chamadas
    await asyncio.sleep(0.05)
    assert banco.chamadas == parou_em, "a retentativa continuou viva depois de encerrar()"


# ---------------------------------------------------------------------------
# O AGORA CONSULTAVEL (2026-08-09)
#
# O planner responde "o que e isso?" perguntando `ver_agora()` aqui. Estes
# testes prendem o contrato: delega ao nucleo, nao abre camera, e nunca
# levanta - nucleo ausente ou quebrado devolve None, que vira "nao estou
# vendo nada" na conversa.
# ---------------------------------------------------------------------------


class NucleoComVisaoAtual:
    def __init__(self, cena):
        self._cena = cena

    def ver_agora(self):
        return self._cena


def test_ver_agora_delega_ao_nucleo():
    cena = {"objetos": [{"classe": "chair", "confianca": 0.8}]}
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoComVisaoAtual(cena))

    assert modulo.ver_agora() == cena


def test_ver_agora_sem_nucleo_devolve_none_sem_levantar():
    # nucleo=None acontece antes de iniciar(): a camera ainda nao montou.
    # "nao estou vendo nada" e resposta, nao mudo.
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=None)

    assert modulo.ver_agora() is None


def test_ver_agora_com_nucleo_quebrado_devolve_none_sem_levantar():
    class NucleoQuebrado:
        def ver_agora(self):
            raise RuntimeError("camera pegou fogo")

    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoQuebrado())

    assert modulo.ver_agora() is None


def test_ver_agora_com_nucleo_sem_o_metodo_devolve_none_sem_levantar():
    # dublês antigos e nucleos de outra versao nao tem ver_agora - o contrato
    # nao pode depender de todo mundo ter atualizado junto.
    modulo = ModuloVisao(EventBus(), CONFIG_VISAO, nucleo=NucleoFalso())

    assert modulo.ver_agora() is None
