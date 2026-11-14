"""Curiosidade do explorador (2026-11-14): entrar no corredor, nao no canto.

Mapas desenhados a mao, 5 cm por celula: -1 desconhecido, 0 livre, 100 parede.
"""
import importlib.util
from pathlib import Path

import numpy as np

_ARQ = (
    Path(__file__).resolve().parents[2]
    / "nav2" / "src" / "orion_nav2" / "orion_nav2" / "explorar_escolha.py"
)
_spec = importlib.util.spec_from_file_location("explorar_escolha", _ARQ)
ee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ee)

RES = 0.05
P, L, D = 100, 0, -1


def _sala_com_corredor():
    """Sala 2x2 m vista; corredor estreito saindo para a DIREITA (desconhecido
    grande atras); e uma fresta de canto LONGE, com parede logo atras."""
    g = np.full((120, 200), D, dtype=np.int16)
    g[20:60, 20:60] = L           # a sala (2 x 2 m), vista
    g[19, 19:61] = P              # paredes
    g[60, 19:61] = P
    g[19:61, 19] = P
    g[19:61, 60] = P
    g[36:44, 60] = L              # PORTA na parede direita -> corredor desconhecido
    # canto de baixo-esquerda: uma frestinha de desconhecido cercada de parede
    g[21:28, 21] = D
    g[20:29, 20] = P
    return g


def test_entra_no_corredor_e_nao_no_canto():
    g = _sala_com_corredor()
    pose = (25 * RES, 40 * RES)   # perto do canto esquerdo
    alvo = ee.escolher(g, RES, 0.0, 0.0, pose, [])
    assert alvo is not None
    # a porta fica em x ~ 3,0 m (coluna 60)
    assert alvo["x"] > 2.7, alvo
    assert alvo["ganho_m2"] > 1.0


def test_parede_corta_o_ganho():
    """Desconhecido atras de parede nao conta: a contagem anda SO pelo desconhecido."""
    g = np.full((60, 60), P, dtype=np.int16)
    g[10:50, 10:30] = L
    g[0:60, 30] = P                # parede inteira na direita, de ponta a ponta
    g[0:60, 31:] = D               # muito desconhecido... atras da parede
    g[5:10, 10:30] = D             # so uma faixa fina de desconhecido em cima
    grupos = ee.grupos_de_fronteira(g, RES, 0.0, 0.0)
    for x, y, n, celulas in grupos:
        assert ee.ganho_m2(g, RES, celulas) < 0.5


def test_lista_negra_tira_o_alvo_que_falhou():
    g = _sala_com_corredor()
    pose = (25 * RES, 40 * RES)
    primeiro = ee.escolher(g, RES, 0.0, 0.0, pose, [])
    segundo = ee.escolher(g, RES, 0.0, 0.0, pose, [(primeiro["x"], primeiro["y"])])
    assert segundo is None or abs(segundo["x"] - primeiro["x"]) > 0.4


def test_casa_toda_vista_acaba():
    g = np.full((40, 40), P, dtype=np.int16)
    g[1:39, 1:39] = L
    assert ee.escolher(g, RES, 0.0, 0.0, (1.0, 1.0), []) is None
