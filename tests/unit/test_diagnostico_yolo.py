"""tools/diagnostico_yolo.py - os elos testados em separado (2026-11-12).

O YOLO de verdade nao roda aqui (o modelo e falso); o que se prova e que a
ferramenta APONTA o elo certo: imagem lisa, imagem boa, achado abaixo/acima
dos limiares e o detector de congelado do codigo novo.
"""
import importlib.util
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

_CAMINHO = Path(__file__).resolve().parents[2] / "tools" / "diagnostico_yolo.py"
_spec = importlib.util.spec_from_file_location("diagnostico_yolo", _CAMINHO)
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)

CONF = {"confidence_threshold": 0.55, "confianca_para_descrever": 0.35, "yolo_imgsz": 320}


def _cena() -> np.ndarray:
    g = np.random.default_rng(0)
    cinza = cv2.GaussianBlur((g.random((480, 640)) * 255).astype(np.float32), (0, 0), 3)
    cinza = cv2.normalize(cinza, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return cv2.cvtColor(cinza, cv2.COLOR_GRAY2BGR)


class _Caixa:
    def __init__(self, cls, conf):
        self.cls = [cls]
        self.conf = [conf]


class _ModeloFalso:
    names = {0: "person", 41: "cup"}

    def __call__(self, imagem, imgsz, conf, verbose):
        caixas = [_Caixa(0, 0.80), _Caixa(41, 0.40), _Caixa(41, 0.30)]
        return [types.SimpleNamespace(boxes=caixas)]


def test_imagem_lisa_e_apontada(tmp_path, capsys):
    arquivo = tmp_path / "teto.jpg"
    cv2.imwrite(str(arquivo), np.full((480, 640, 3), 10, np.uint8))
    assert diag.pegar_imagem("", str(arquivo)) is not None
    assert "escura e lisa" in capsys.readouterr().out


def test_imagem_boa_passa(tmp_path, capsys):
    arquivo = tmp_path / "sala.jpg"
    cv2.imwrite(str(arquivo), _cena())
    diag.pegar_imagem("", str(arquivo))
    saida = capsys.readouterr().out
    assert "imagem com conteudo" in saida and "FALHA" not in saida


def test_yolo_mostra_cada_achado_com_o_limiar_que_ele_passa(capsys):
    diag.checar_yolo(_ModeloFalso(), _cena(), CONF)
    saida = capsys.readouterr().out
    assert "person" in saida and "acao+descreve" in saida
    assert "so descreve" in saida  # copo a 0,40
    assert "ABAIXO dos dois limiares" in saida  # copo a 0,30


def test_detector_de_congelado_do_codigo_novo_passa(capsys):
    diag.checar_congelado(_cena())
    assert "[OK]" in capsys.readouterr().out
