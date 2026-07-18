"""Testes de captura de camera (Cap 8 s.2-3).

Pula graciosamente onde `cv2` nao esta instalado (ex.: neste Raspberry Pi -
Vision Core roda so no Notebook, Cap 2/Cap 8 EDR-0018).
"""
from unittest.mock import MagicMock

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from orion.vision.captura import CapturaCamera, ErroCamera  # noqa: E402


@pytest.mark.asyncio
async def test_indice_invalido_levanta_erro_camera():
    captura = CapturaCamera(indice=99)
    with pytest.raises(ErroCamera):
        await captura.abrir()


@pytest.mark.asyncio
async def test_ler_frame_sem_abrir_levanta_erro():
    captura = CapturaCamera(indice=0)
    with pytest.raises(ErroCamera):
        await captura.ler_frame()


@pytest.mark.asyncio
async def test_fechar_sem_ter_aberto_nao_falha():
    captura = CapturaCamera(indice=0)
    await captura.fechar()  # nao deve levantar excecao
    assert captura.aberta is False


@pytest.mark.asyncio
async def test_espelhado_desfaz_o_espelhamento_da_camera_frontal():
    # frame assimetrico (metade esquerda preta, direita branca) para
    # distinguir espelhado de nao-espelhado sem ambiguidade
    frame_original = np.zeros((10, 10, 3), dtype=np.uint8)
    frame_original[:, 5:] = 255

    captura_falsa = MagicMock()
    captura_falsa.read.return_value = (True, frame_original)

    captura = CapturaCamera(indice=0, espelhado=True)
    captura._captura = captura_falsa  # abrir() ja testado a parte

    frame_lido = await captura.ler_frame()

    assert np.array_equal(frame_lido, cv2.flip(frame_original, 1))
    assert not np.array_equal(frame_lido, frame_original)


@pytest.mark.asyncio
async def test_sem_espelhado_mantem_o_frame_como_veio():
    frame_original = np.zeros((10, 10, 3), dtype=np.uint8)
    frame_original[:, 5:] = 255

    captura_falsa = MagicMock()
    captura_falsa.read.return_value = (True, frame_original)

    captura = CapturaCamera(indice=0, espelhado=False)
    captura._captura = captura_falsa

    frame_lido = await captura.ler_frame()

    assert np.array_equal(frame_lido, frame_original)
