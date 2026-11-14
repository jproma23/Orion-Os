"""A IA que conversa VE a camera (2026-11-14).

Dono: "tem camera mais ele encherga?" - nao enxergava. O modelo so recebia o
resumo do YOLO em texto. Estes testes provam: a foto vai na mensagem ATUAL
quando a pergunta pede olhar, nao vai no historico, nao vai em pergunta que
nao e visual, e o YOLO volta a responder sozinho quando a IA cai.
"""
import base64

import pytest

from orion.mission import mission_planner as mp
from orion.mission.ai_manager import AiManager as AIManager

JPEG = b"\xff\xd8\xff\xe0 foto falsa \xff\xd9"


def _ai():
    return AIManager.__new__(AIManager)


def test_mensagem_com_foto_leva_image_url_so_na_fala_atual():
    ai = _ai()
    ai.historico = type("H", (), {"mensagens": lambda self: [
        {"role": "user", "content": "oi"}, {"role": "assistant", "content": "ola"},
    ]})()
    msgs = ai._montar_mensagens("sistema", "que cor e a minha camisa?", True, imagem_jpeg=JPEG)

    assert [m["content"] for m in msgs[1:3]] == ["oi", "ola"], "historico sem foto"
    atual = msgs[-1]["content"]
    assert isinstance(atual, list)
    texto = next(p for p in atual if p["type"] == "text")["text"]
    assert "que cor e a minha camisa?" in texto
    url = next(p for p in atual if p["type"] == "image_url")["image_url"]["url"]
    assert url == "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def test_sem_foto_a_mensagem_continua_texto_puro():
    ai = _ai()
    msgs = ai._montar_mensagens("sistema", "oi", False)
    assert msgs[-1] == {"role": "user", "content": "oi"}


@pytest.mark.parametrize("frase", [
    "o que voce esta vendo?",
    "o que você vê?",
    "que cor é a minha camiseta?",
    "o que eu estou segurando na mão?",
    "olha isso aqui",
    "quantos dedos eu tenho levantado?",
    "o que está escrito aqui?",
    "o que é isso?",
    "descreve a sala",
])
def test_pergunta_visual_leva_a_foto(frase):
    p = mp.MissionPlanner.__new__(mp.MissionPlanner)
    p._foto_camera = lambda: JPEG
    assert p._foto_se_visual(frase) == JPEG


@pytest.mark.parametrize("frase", [
    "que horas sao?", "me conta uma piada", "anda pra frente", "vem aqui", "faz isso",
])
def test_pergunta_que_nao_e_visual_nao_paga_a_foto(frase):
    p = mp.MissionPlanner.__new__(mp.MissionPlanner)
    p._foto_camera = lambda: JPEG
    assert p._foto_se_visual(frase) is None


def test_camera_sem_quadro_ou_com_erro_nao_derruba_a_conversa():
    p = mp.MissionPlanner.__new__(mp.MissionPlanner)
    p._foto_camera = lambda: None
    assert p._foto_se_visual("o que voce ve?") is None

    def quebrada():
        raise RuntimeError("camera caiu")

    p._foto_camera = quebrada
    assert p._foto_se_visual("o que voce ve?") is None


@pytest.mark.asyncio
async def test_com_foto_o_yolo_nao_responde_na_frente_da_ia_mas_volta_sem_ia():
    """Substitui, nao empilha: com a foto ligada quem responde "o que e isso?"
    e a IA olhando; se a IA cair, a cena do YOLO responde sozinha."""
    p = mp.MissionPlanner.__new__(mp.MissionPlanner)
    p._consultar_visao = lambda: {"objetos": [{"classe": "cup", "confianca": 0.9}]}
    p._motivo_cego = None
    p._foto_camera = lambda: JPEG

    assert await p._responder_sobre_o_que_ve("o que é isso?") is None
    sozinho = await p._responder_sobre_o_que_ve("o que é isso?", mesmo_com_foto=True)
    assert sozinho is not None and "nada" not in sozinho.lower()

    p._foto_camera = None  # sem foto ligada: o comportamento antigo, intacto
    assert await p._responder_sobre_o_que_ve("o que é isso?") is not None
