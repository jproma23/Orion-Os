"""Testes do contrato de autoridade da IA (EDR-0026).

Estes testes existem para uma coisa so: garantir que o limite da autoridade
da IA continue sendo verdade depois de alguem editar o catalogo daqui a tres
meses. Sao o contrato virando prova executavel, nao decoracao.
"""
import pytest

from motion_core.behavior import contrato


def test_catalogo_nao_alcanca_comando_proibido():
    """Regra 5: o proibido e inalcancavel. Nenhuma acao do catalogo pode
    desembocar num comando da lista negra."""
    for acao in contrato.ACOES.values():
        comando = acao.dados.get("comando")
        assert comando not in contrato.PROIBIDO_PARA_IA, acao.nome


def test_a_re_nunca_esta_ao_alcance_da_ia():
    """MOVE_DISTANCE e o comando da re (distancia negativa) e a re nao tem
    seguranca reativa - o SafetyManager so olha o sensor frontal (Cap 18).
    Andar para tras sozinho e andar cego."""
    assert "MOVE_DISTANCE" in contrato.PROIBIDO_PARA_IA
    comandos = {a.dados.get("comando") for a in contrato.ACOES.values()}
    assert "MOVE_DISTANCE" not in comandos


def test_a_ia_nao_mexe_na_propria_seguranca():
    """Quem e vigiado nao ajusta o vigia."""
    for proibido in ("CALIBRATE_IMU", "CALIBRATE_COMPASS", "SET_IMPACT_THRESHOLD"):
        assert proibido in contrato.PROIBIDO_PARA_IA


def test_todo_movimento_passa_pela_navegacao():
    """Regra 2: movimento autonomo nunca vai direto ao Arduino - e o
    NavigationCore que checa obstaculo antes de avancar (Cap 12 s.6)."""
    for acao in contrato.ACOES.values():
        if acao.move_rodas:
            assert acao.topico == "navigation.comando", acao.nome


def test_a_ia_tem_autoridade_motora_de_verdade():
    """A outra metade do contrato: autoridade TOTAL dentro dele. Um catalogo
    que nao move o robo seria o problema de antes com outro nome."""
    que_movem = {a.nome for a in contrato.ACOES.values() if a.move_rodas}
    assert {"avancar", "virar_esquerda", "virar_direita", "explorar", "seguir_pessoa"} <= que_movem


def test_descansar_nao_esta_no_diretorio():
    """Decisao do usuario (2026-07-28): nao existe descansar no diretorio do
    maestro. Um robo cuidador nao tem "nao fazer nada" entre as entregas."""
    assert "descansar" not in contrato.ACOES


@pytest.mark.parametrize("resposta", [None, "", "descansar", "AUTODESTRUIR", "nada", "  "])
def test_resolver_abstem_se_estiver_fora_do_contrato(resposta):
    """Regra 6: fora do catalogo nunca vira chute."""
    assert contrato.resolver(resposta) is None


def test_resolver_tolera_caixa_e_espaco():
    """O modelo devolve texto; nao pode falhar por causa de um espaco."""
    assert contrato.resolver("  EXPLORAR  ") is contrato.ACOES["explorar"]


def test_abstencao_nao_e_uma_acao():
    """A abstencao existe como MECANISMO, nunca como verbo do catalogo."""
    assert contrato.ABSTENCAO not in contrato.ACOES
