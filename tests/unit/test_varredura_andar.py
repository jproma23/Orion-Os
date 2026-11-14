"""Modo varredura ao andar (2026-11-14): andando, a cabeca olha o chao.

Relogio falso e trava falsa: prova a LOGICA - liga so andando para a frente,
varre centro/esquerda/centro/direita, respeita o respiro entre dois alvos do
Nav2, sai do FOLLOW e avisa o Notebook nas duas pontas.
"""
import pytest

from motion_core.navigation import varredura_andar as modulo
from motion_core.navigation.trava_lidar import FRENTE, GIRO, TRAS
from motion_core.navigation.varredura_andar import VarreduraAoAndar


class Bus:
    def __init__(self):
        self.publicados = []

    async def publish(self, topico, dados=None, prioridade=None):
        self.publicados.append((topico, dados))

    def cabeca(self):
        return [(d["pan"], d["tilt"]) for t, d in self.publicados if t == "motion.pan_tilt"]

    def avisos(self):
        return [d["ativo"] for t, d in self.publicados if t == modulo.TOPICO_VARREDURA]


class Comm:
    def __init__(self):
        self.remotos = []

    async def publish(self, topico, dados, local=True):
        self.remotos.append((topico, dados, local))


class Trava:
    sentido_em_curso = None


class Nav:
    modo = "HOLD"


def _montar():
    relogio = {"t": 100.0}
    bus, comm, trava, nav = Bus(), Comm(), Trava(), Nav()
    v = VarreduraAoAndar(bus, trava, nav, comm, relogio=lambda: relogio["t"])
    return v, bus, comm, trava, nav, relogio


async def _avancar(v, relogio, segundos, passo=0.1):
    fim = relogio["t"] + segundos
    while relogio["t"] < fim - 1e-9:
        relogio["t"] += passo
        await v.passo()


@pytest.mark.asyncio
async def test_parado_nao_mexe_na_cabeca():
    v, bus, comm, trava, nav, relogio = _montar()
    await _avancar(v, relogio, 3.0)
    assert bus.cabeca() == [] and not v.ativa


@pytest.mark.asyncio
async def test_andando_para_frente_abaixa_e_varre_e_avisa_o_notebook():
    v, bus, comm, trava, nav, relogio = _montar()
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 4 * modulo.PERMANENCIA_S - 0.05)

    assert v.ativa
    pans = [p for p, _ in bus.cabeca()]
    assert pans == list(modulo.SEQUENCIA_PAN_GRAUS)
    assert all(t == modulo.TILT_CHAO_GRAUS for _, t in bus.cabeca())
    assert bus.avisos() == [True]
    assert comm.remotos == [
        (modulo.TOPICO_VARREDURA, {"ativo": True, "tilt": modulo.TILT_CHAO_GRAUS}, False)
    ]


@pytest.mark.asyncio
async def test_pausa_curta_entre_alvos_nao_levanta_a_cabeca():
    v, bus, comm, trava, nav, relogio = _montar()
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 1.0)
    trava.sentido_em_curso = None
    await _avancar(v, relogio, modulo.RESPIRO_S - 0.5)
    assert v.ativa, "o Nav2 pausa entre um alvo e outro"
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 0.5)
    assert bus.avisos() == [True]


@pytest.mark.asyncio
async def test_parou_de_vez_desliga_e_centraliza():
    v, bus, comm, trava, nav, relogio = _montar()
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 1.0)
    trava.sentido_em_curso = None
    await _avancar(v, relogio, modulo.RESPIRO_S + 0.2)
    assert not v.ativa
    assert bus.cabeca()[-1] == (0.0, 0.0)
    assert bus.avisos() == [True, False]


@pytest.mark.parametrize("sentido", [TRAS, GIRO])
@pytest.mark.asyncio
async def test_re_e_giro_nao_varrem(sentido):
    """A camera fica na frente: na re ela olharia para o lado errado."""
    v, bus, comm, trava, nav, relogio = _montar()
    trava.sentido_em_curso = sentido
    await _avancar(v, relogio, 2.0)
    assert not v.ativa and bus.cabeca() == []


@pytest.mark.asyncio
async def test_no_me_siga_a_cabeca_e_da_pessoa():
    v, bus, comm, trava, nav, relogio = _montar()
    nav.modo = type("M", (), {"value": "FOLLOW"})()
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 2.0)
    assert not v.ativa and bus.cabeca() == []


@pytest.mark.asyncio
async def test_notebook_fora_nao_impede_a_varredura():
    v, bus, comm, trava, nav, relogio = _montar()

    async def quebrado(*a, **k):
        raise ConnectionError("sem enlace")

    comm.publish = quebrado
    trava.sentido_em_curso = FRENTE
    await _avancar(v, relogio, 1.0)
    assert v.ativa and bus.cabeca()
