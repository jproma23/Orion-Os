"""Mission Brain: a rede em Python puro (2026-08-07).

O que estes testes guardam, em ordem de importancia:

1. **a rede APRENDE** - uma rede escrita a mao pode ter o gradiente com
   sinal trocado e mesmo assim rodar sem erro nenhum, so nao aprendendo
   nada. O teste do problema separavel e o unico que pega isso;
2. o rotulo sai do lugar certo (teste/doc acompanham, nao sao o assunto);
3. o idf vem do TREINO - recalcular no teste vazaria o futuro e inflaria
   o acerto;
4. gravar e ler de volta nao muda a resposta.
"""

import json

import pytest

from tools.mission_brain import Modelo, Rede, Vetorizador, _rotulo, fatiar


def test_fatiar_tira_acento_e_faz_bigrama():
    pedacos = fatiar("Reconexão da Serial")

    assert "reconexao" in pedacos, "o acento tinha que sumir"
    assert "serial" in pedacos
    assert "reconexao_serial" in pedacos, "faltou o bigrama"
    assert "da" not in pedacos, "palavra de duas letras nao entra"


def test_rotulo_ignora_teste_e_doc_quando_ha_codigo():
    arquivos = [
        "src/orion/voice/sintese.py",
        "src/orion/voice/voice_core.py",
        "tests/unit/test_voice_sintese.py",
        "docs/journal.md",
    ]

    assert _rotulo(arquivos) == "voz"


def test_rotulo_de_commit_so_de_doc_e_docs():
    assert _rotulo(["docs/journal.md"]) == "docs"


def test_rotulo_pega_a_area_dominante():
    arquivos = [
        "motion_core/behavior/comportamentos.py",
        "motion_core/behavior/goap.py",
        "motion_core/webui/server.py",
    ]

    assert _rotulo(arquivos) == "comportamento"


def test_rotulo_prefere_o_caminho_mais_especifico():
    """motion_core/behavior/ tem de vencer motion_core/, que vem depois."""
    assert _rotulo(["motion_core/behavior/goap.py"]) == "comportamento"
    assert _rotulo(["motion_core/desligamento.py"]) == "motion_core"


def test_vetorizador_so_conhece_o_vocabulario_do_treino():
    v = Vetorizador(min_df=1)
    v.ajustar(["serial caiu", "serial voltou"])

    vetor = v.transformar("serial pancadaria")

    termos = {t for t, i in v.indice.items() if i in vetor}
    assert any("serial" in t for t in termos)
    assert not any("pancadaria" in t for t in termos), "palavra nova nao pode entrar"


def test_vetorizador_sem_termo_conhecido_devolve_vazio():
    v = Vetorizador(min_df=1)
    v.ajustar(["serial caiu"])

    assert v.transformar("xxxxx yyyyy") == {}


def test_a_rede_aprende_um_problema_separavel():
    """O teste que pega gradiente errado: sem aprender, fica no chute.

    Duas classes, vocabulario disjunto. Uma rede sa acerta tudo; uma com
    o sinal do gradiente trocado fica em ~50% e roda sem erro nenhum.
    """
    textos = [
        "motor roda giro esquerda",
        "motor passo roda direita",
        "motor giro passo eixo",
        "audio microfone fala voz",
        "audio voz fala escuta",
        "microfone escuta audio ruido",
    ]
    areas = ["motor", "motor", "motor", "audio", "audio", "audio"]

    modelo = Modelo(n_escondidas=8, semente=0)
    modelo.ajustar(textos, areas, epocas=120)

    assert modelo.prever("motor roda giro") == "motor"
    assert modelo.prever("voz microfone fala") == "audio"


def test_a_versao_linear_tambem_aprende():
    """n_escondidas=0 e o adversario do `avaliar` - tem de funcionar."""
    textos = [
        "motor roda giro esquerda",
        "motor passo roda direita",
        "motor giro passo eixo",
        "audio microfone fala voz",
        "audio voz fala escuta",
        "microfone escuta audio ruido",
    ]
    areas = ["motor", "motor", "motor", "audio", "audio", "audio"]

    modelo = Modelo(n_escondidas=0, semente=0)
    modelo.ajustar(textos, areas, epocas=120)

    assert modelo.prever("motor roda giro") == "motor"
    assert modelo.prever("voz microfone fala") == "audio"


def test_probabilidades_somam_um_e_vem_ordenadas():
    modelo = Modelo(n_escondidas=4, semente=0)
    modelo.ajustar(
        ["motor roda giro", "audio voz fala", "camera imagem quadro"],
        ["motor", "audio", "visao"],
        epocas=40,
    )

    ordenado = modelo.probabilidades("motor roda")

    assert abs(sum(p for _, p in ordenado) - 1.0) < 1e-9
    assert [p for _, p in ordenado] == sorted((p for _, p in ordenado), reverse=True)


def test_mesma_semente_da_o_mesmo_resultado():
    textos = ["motor roda giro", "audio voz fala", "camera imagem quadro"]
    areas = ["motor", "audio", "visao"]

    def _treinar(semente):
        m = Modelo(n_escondidas=8, semente=semente)
        m.ajustar(textos, areas, epocas=40)
        return m.probabilidades("motor giro")

    assert _treinar(0) == _treinar(0)


def test_json_ida_e_volta_nao_muda_a_resposta():
    modelo = Modelo(n_escondidas=8, semente=0)
    modelo.ajustar(
        ["motor roda giro", "audio voz fala", "camera imagem quadro"],
        ["motor", "audio", "visao"],
        epocas=60,
    )
    antes = modelo.probabilidades("motor roda giro")

    relido = Modelo.de_json(json.loads(json.dumps(modelo.para_json())))

    assert relido.probabilidades("motor roda giro") == antes


def test_prever_sem_treinar_avisa():
    with pytest.raises(AssertionError):
        Modelo().probabilidades("qualquer coisa")


def test_rede_crua_produz_distribuicao_valida():
    rede = Rede(n_entradas=10, n_escondidas=4, classes=["a", "b", "c"], semente=0)

    p = rede.prever({0: 1.0, 3: 0.5})

    assert len(p) == 3
    assert all(0.0 <= v <= 1.0 for v in p)
    assert abs(sum(p) - 1.0) < 1e-9
