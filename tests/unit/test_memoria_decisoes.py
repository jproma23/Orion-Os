"""O robo pergunta uma vez por situacao e aprende (2026-08-07).

O caso que motivou tudo: 505 chamadas ao modelo num dia para 4 decisoes
identicas, porque com os sensores mudos a situacao nao mudava e ele
reperguntava do mesmo jeito.

O teste que mais importa e o `test_situacao_repetida_nao_pergunta_de_novo`:
sem ele, um cache que nunca acerta passa despercebido - ele nao quebra
nada, so continua gastando.
"""

import json

from motion_core.behavior.memoria_decisoes import MemoriaDeDecisoes, assinatura

CONTEXTO = """QUEM EU SOU E COMO DEVO AGIR:
- Eu sou o Fofao.

O QUE EU SINTO AGORA:
- a minha frente: NAO SEI a distancia agora
- estado do corpo: IDLE

QUEM EU VEJO:
- neste exato momento nao estou vendo ninguem

TUDO O QUE EU VI HOJE:
- as 20:11 eu vi: vi Joao Paulo
"""

OPCOES = ["explorar", "observar_ambiente", "repouso"]


def test_mesma_situacao_da_a_mesma_assinatura():
    assert assinatura(CONTEXTO, OPCOES) == assinatura(CONTEXTO, OPCOES)


def test_o_diario_do_dia_NAO_muda_a_assinatura():
    """Se o diario entrasse, toda situacao pareceria nova e o cache seria inutil.

    O diario cresce a cada evento do dia - foi por isso que ele ficou de
    fora da assinatura.
    """
    com_mais_diario = CONTEXTO + "- as 21:30 eu vi: vi Marah\n- as 22:00 eu vi: uma cadeira\n"

    assert assinatura(com_mais_diario, OPCOES) == assinatura(CONTEXTO, OPCOES)


def test_sensor_diferente_MUDA_a_assinatura():
    """O que muda a decisao tem de mudar a pergunta."""
    com_obstaculo = CONTEXTO.replace(
        "- a minha frente: NAO SEI a distancia agora",
        "- a minha frente: OBSTACULO a 20 cm - perto demais para avancar",
    )

    assert assinatura(com_obstaculo, OPCOES) != assinatura(CONTEXTO, OPCOES)


def test_ver_alguem_MUDA_a_assinatura():
    com_pessoa = CONTEXTO.replace(
        "- neste exato momento nao estou vendo ninguem",
        "- neste exato momento estou vendo: Joao Paulo",
    )

    assert assinatura(com_pessoa, OPCOES) != assinatura(CONTEXTO, OPCOES)


def test_cardapio_diferente_MUDA_a_assinatura():
    """Mudou o que ele PODE fazer, a escolha pode ser outra."""
    assert assinatura(CONTEXTO, ["repouso"]) != assinatura(CONTEXTO, OPCOES)


def test_situacao_repetida_nao_pergunta_de_novo(tmp_path):
    """O teste que da sentido a tudo: a segunda vez sai de graca."""
    memoria = MemoriaDeDecisoes(caminho=tmp_path / "d.json")
    chave = assinatura(CONTEXTO, OPCOES)

    assert memoria.lembrar(chave) is None, "nao podia saber antes de aprender"
    memoria.aprender(chave, "observar_ambiente", "nao sei a distancia")

    lembrada = memoria.lembrar(chave)

    assert lembrada is not None, "esqueceu o que acabou de aprender"
    assert lembrada["comportamento"] == "observar_ambiente"
    assert memoria.reaproveitadas == 1
    assert memoria.perguntadas == 1


def test_o_que_foi_aprendido_sobrevive_ao_reinicio(tmp_path):
    """O servico reinicia varias vezes por dia - reaprender custaria cota."""
    caminho = tmp_path / "d.json"
    chave = assinatura(CONTEXTO, OPCOES)
    MemoriaDeDecisoes(caminho=caminho).aprender(chave, "observar_ambiente", "porque sim")

    outra = MemoriaDeDecisoes(caminho=caminho)

    assert outra.lembrar(chave)["comportamento"] == "observar_ambiente"


def test_decisao_vencida_e_esquecida(tmp_path):
    """Validade existe para a decisao nao congelar, nao para forcar repergunta."""
    relogio = {"t": 1000.0}
    memoria = MemoriaDeDecisoes(
        caminho=tmp_path / "d.json", validade_s=60.0, agora=lambda: relogio["t"]
    )
    chave = assinatura(CONTEXTO, OPCOES)
    memoria.aprender(chave, "observar_ambiente")

    relogio["t"] += 61.0

    assert memoria.lembrar(chave) is None


def test_teto_de_situacoes_guardadas(tmp_path):
    memoria = MemoriaDeDecisoes(caminho=tmp_path / "d.json", maximo=5)

    for i in range(20):
        memoria.aprender(f"chave{i}", "repouso")

    assert memoria.quantas == 5
    # as mais RECENTES vencem
    assert memoria.lembrar("chave19") is not None
    assert memoria.lembrar("chave0") is None


def test_arquivo_corrompido_nao_derruba_o_robo(tmp_path):
    """Aprender e otimizacao de cota - nao pode impedir o robo de decidir."""
    caminho = tmp_path / "d.json"
    caminho.write_text("{{{ isto nao e json", encoding="utf-8")

    memoria = MemoriaDeDecisoes(caminho=caminho)

    assert memoria.quantas == 0
    memoria.aprender("x", "repouso")
    assert memoria.lembrar("x") is not None


def test_esquecer_tira_da_memoria_e_do_disco(tmp_path):
    caminho = tmp_path / "d.json"
    memoria = MemoriaDeDecisoes(caminho=caminho)
    memoria.aprender("x", "repouso")

    memoria.esquecer("x")

    assert memoria.lembrar("x") is None
    assert json.loads(caminho.read_text(encoding="utf-8")) == {}


def test_resumo_conta_a_economia(tmp_path):
    memoria = MemoriaDeDecisoes(caminho=tmp_path / "d.json")
    chave = assinatura(CONTEXTO, OPCOES)
    memoria.aprender(chave, "repouso")
    for _ in range(9):
        memoria.lembrar(chave)

    assert "90%" in memoria.resumo()
