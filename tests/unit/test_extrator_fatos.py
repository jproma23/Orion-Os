"""O extrator aprende fatos sem inventar, e sem atrasar a fala.

A tabela `conhecimento` existia no schema desde sempre e o `ai_manager` ja
sabia injetar seu conteudo no prompt - mas ninguem escrevia nela. Este modulo
e o produtor que faltava, e o que estes testes protegem e o que ele NAO pode
fazer: inventar fato e segurar a conversa.
"""
from __future__ import annotations

import asyncio

import pytest

from orion.mission.extrator_fatos import ExtratorFatos


class IaFalsa:
    def __init__(self, resposta: str = "NADA") -> None:
        self.resposta = resposta
        self.chamadas: list[dict] = []

    async def responder(self, texto, contexto=None, usar_historico=True, prompt_sistema=None):
        self.chamadas.append({"texto": texto, "usar_historico": usar_historico})
        return self.resposta


class MemoriaFalsa:
    def __init__(self, existentes: list[dict] | None = None) -> None:
        self.gravados: list[dict] = []
        self._existentes = existentes or []

    async def remember(self, categoria, dados):
        self.gravados.append({"categoria": categoria, **dados})
        return len(self.gravados)

    async def recall(self, categoria, filtro=None, limite=20):
        return list(self._existentes)


def _extrator(ia, memoria, turnos=2):
    return ExtratorFatos(ia, memoria, turnos_por_lote=turnos)


# ---------------------------------------------------------------------------
# Aprender
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aprende_um_fato_e_grava_na_tabela_certa():
    ia = IaFalsa("cafe: gosta de cafe forte")
    memoria = MemoriaFalsa()
    e = _extrator(ia, memoria)

    e.agendar("eu tomo cafe forte todo dia", "anotado!")
    e.agendar("e voce?", "eu nao tomo cafe")
    await e.encerrar()

    assert len(memoria.gravados) == 1
    fato = memoria.gravados[0]
    assert fato["categoria"] == "conhecimento"
    assert fato["chave"] == "cafe"
    assert fato["valor"] == "gosta de cafe forte"
    assert fato["fonte"] == "conversa"


@pytest.mark.asyncio
async def test_extracao_nao_entra_no_historico_da_conversa():
    """Se a instrucao de extracao caisse no historico de curto prazo, a
    proxima fala do dono seria respondida no meio dela - e a extracao
    seguinte viria contaminada pelo papo. Mesma separacao do `decidir()`."""
    ia = IaFalsa("NADA")
    e = _extrator(ia, MemoriaFalsa())

    e.agendar("oi", "ola")
    e.agendar("tudo bem?", "tudo")
    await e.encerrar()

    assert ia.chamadas[0]["usar_historico"] is False


# ---------------------------------------------------------------------------
# Recusar - a parte que importa mais
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nada_e_a_resposta_normal_e_nao_grava_coisa_alguma():
    """A maioria das conversas nao produz fato. Um extrator que sempre acha
    alguma coisa esta inventando."""
    ia = IaFalsa("NADA")
    memoria = MemoriaFalsa()
    e = _extrator(ia, memoria)

    e.agendar("bom dia", "bom dia!")
    e.agendar("que horas sao?", "sao 10h")
    await e.encerrar()

    assert memoria.gravados == []


@pytest.mark.asyncio
async def test_nada_com_frase_em_volta_ainda_e_nada():
    """Modelo pequeno gosta de explicar: "NADA. Nao houve fatos duraveis."."""
    e = _extrator(IaFalsa("NADA. Nao houve fatos duraveis nesta conversa."), m := MemoriaFalsa())
    e.agendar("a", "b")
    e.agendar("c", "d")
    await e.encerrar()
    assert m.gravados == []


@pytest.mark.asyncio
async def test_chave_que_e_frase_inteira_e_descartada():
    """Modelo desobediente devolve "a pessoa gosta de: cafe". Aquilo nao e
    chave: viraria lixo impossivel de casar na busca por palavra."""
    e = _extrator(IaFalsa("a pessoa gosta de: cafe forte"), m := MemoriaFalsa())
    e.agendar("a", "b")
    e.agendar("c", "d")
    await e.encerrar()
    assert m.gravados == []


@pytest.mark.asyncio
async def test_fato_ja_conhecido_nao_e_gravado_de_novo():
    ia = IaFalsa("cafe: gosta de cafe forte")
    memoria = MemoriaFalsa(existentes=[{"chave": "cafe", "valor": "gosta de cafe"}])
    e = _extrator(ia, memoria)

    e.agendar("a", "b")
    e.agendar("c", "d")
    await e.encerrar()

    assert memoria.gravados == []


@pytest.mark.asyncio
async def test_no_maximo_tres_fatos_por_lote():
    # Chaves com 3+ letras de proposito: chave de uma letra so e recusada pelo
    # extrator, e recusada com razao - nao casaria com nada na busca por
    # palavra do `_conhecimento_relevante`.
    ia = IaFalsa("cafe: forte\nmusica: rock\ntrabalho: dentista\nhorta: tomate")
    memoria = MemoriaFalsa()
    e = _extrator(ia, memoria)

    e.agendar("x", "y")
    e.agendar("z", "w")
    await e.encerrar()

    assert len(memoria.gravados) == 3


# ---------------------------------------------------------------------------
# Nao atrasar a fala
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agendar_volta_na_hora_mesmo_com_a_ia_lenta():
    """A garantia central: `agendar` NAO espera a extracao.

    Uma chamada de modelo a mais por turno viraria espera audivel - este
    projeto lutou para baixar de 3,50 s para 0,66 s ate a primeira frase.
    """

    class IaLenta(IaFalsa):
        async def responder(self, texto, contexto=None, usar_historico=True, prompt_sistema=None):
            await asyncio.sleep(5)
            return "NADA"

    e = _extrator(IaLenta(), MemoriaFalsa())
    e.agendar("a", "b")

    inicio = asyncio.get_running_loop().time()
    e.agendar("c", "d")  # este fecha o lote e dispara a extracao lenta
    decorrido = asyncio.get_running_loop().time() - inicio

    assert decorrido < 0.1  # voltou na hora, sem esperar os 5 s
    e._tarefa.cancel()


@pytest.mark.asyncio
async def test_so_extrai_quando_o_lote_fecha():
    """Extrair a cada turno multiplicaria o custo e veria menos: "sim, esse
    mesmo" nao diz nada sozinho, e diz tudo depois da pergunta anterior."""
    ia = IaFalsa("NADA")
    e = ExtratorFatos(ia, MemoriaFalsa(), turnos_por_lote=4)

    for i in range(3):
        e.agendar(f"pergunta {i}", f"resposta {i}")
    assert ia.chamadas == []  # ainda nao fechou o lote

    e.agendar("pergunta 3", "resposta 3")
    await asyncio.sleep(0)
    await e.encerrar()
    assert len(ia.chamadas) == 1


@pytest.mark.asyncio
async def test_falha_da_ia_nao_derruba_o_robo():
    """Perder um fato e barato; derrubar o processo da conversa nao e."""

    class IaQuebrada(IaFalsa):
        async def responder(self, texto, contexto=None, usar_historico=True, prompt_sistema=None):
            raise RuntimeError("sem internet (simulado)")

    e = _extrator(IaQuebrada(), m := MemoriaFalsa())
    e.agendar("a", "b")
    e.agendar("c", "d")
    await e.encerrar()  # nao levanta
    assert m.gravados == []


@pytest.mark.asyncio
async def test_turno_vazio_e_ignorado():
    """Transcricao vazia acontece o tempo todo com o microfone atual."""
    ia = IaFalsa("NADA")
    e = _extrator(ia, MemoriaFalsa())
    e.agendar("", "resposta")
    e.agendar("   ", "resposta")
    assert ia.chamadas == []


# ---------------------------------------------------------------------------
# DESTILAR POR OCIOSIDADE (2026-08-03)
#
# O lote por contagem presumia conversas longas. O banco desmentiu: 32 das 37
# sessoes gravadas tiveram menos de 4 turnos, entao o gatilho quase nunca
# disparava - e o que sobrava morria no desligamento, porque `encerrar()` nao
# era chamado por ninguem. 152 conversas gravadas, tabela `conhecimento` vazia.
# ---------------------------------------------------------------------------

FALA_LONGA = "eu trabalho de noite e durmo de dia"  # >= _MINIMO_LETRAS_FALA


@pytest.mark.asyncio
async def test_destila_por_ociosidade_sem_encher_o_lote():
    """A conversa curta e a MAIS COMUM - ela tem que produzir fato tambem."""
    ia = IaFalsa("trabalho: trabalha de noite")
    memoria = MemoriaFalsa()
    e = ExtratorFatos(ia, memoria, turnos_por_lote=4, segundos_ate_destilar=0.01)

    e.agendar(FALA_LONGA, "anotado!")
    assert ia.chamadas == []  # ainda nao: a conversa pode continuar

    await asyncio.sleep(0.05)  # silencio -> a conversa acabou
    assert len(ia.chamadas) == 1
    assert memoria.gravados[0]["chave"] == "trabalho"


@pytest.mark.asyncio
async def test_turno_novo_rearma_a_espera():
    """O relogio conta SILENCIO, nao tempo desde o inicio da conversa - senao
    uma conversa longa seria cortada no meio."""
    ia = IaFalsa("NADA")
    e = ExtratorFatos(ia, MemoriaFalsa(), turnos_por_lote=9, segundos_ate_destilar=0.06)

    e.agendar(FALA_LONGA, "sei")
    await asyncio.sleep(0.04)
    e.agendar(FALA_LONGA + " tambem", "entendi")  # antes do prazo: rearma
    await asyncio.sleep(0.04)
    assert ia.chamadas == []  # nao destilou no meio da conversa

    await asyncio.sleep(0.05)
    assert len(ia.chamadas) == 1


@pytest.mark.asyncio
async def test_ociosidade_nao_gasta_modelo_com_chamado_solto():
    """"Fofao!" e a sessao mais comum do banco: a palavra de ativacao entra
    como turno. Nao ha fato dentro de um chamado, so custo."""
    ia = IaFalsa("NADA")
    e = ExtratorFatos(ia, MemoriaFalsa(), turnos_por_lote=4, segundos_ate_destilar=0.01)

    e.agendar("Fofao!", "Oi! Estou aqui!")
    await asyncio.sleep(0.05)

    assert ia.chamadas == []
    assert len(e._acumulado) == 0, "o ruido tem que ser descartado, nao acumulado"


@pytest.mark.asyncio
async def test_encerrar_destila_o_lote_curto_sem_piso():
    """No desligamento nao se aplica o piso: aquele lote nao tem proxima
    chance. E ESTE o caminho que estava solto - ninguem chamava `encerrar()`."""
    ia = IaFalsa("nome: se chama Joao")
    memoria = MemoriaFalsa()
    e = ExtratorFatos(ia, memoria, turnos_por_lote=4, segundos_ate_destilar=999)

    e.agendar("oi", "ola")  # curto: o piso da ociosidade barraria
    await e.encerrar()

    assert len(ia.chamadas) == 1
    assert memoria.gravados[0]["chave"] == "nome"


@pytest.mark.asyncio
async def test_encerrar_cancela_a_espera_pendente():
    """Desligamento nao pode deixar tarefa orfa dormindo 90 s."""
    e = ExtratorFatos(IaFalsa("NADA"), MemoriaFalsa(), turnos_por_lote=4)
    e.agendar(FALA_LONGA, "sei")
    espera = e._tarefa_ociosidade
    assert espera is not None and not espera.done()

    await e.encerrar()
    assert espera.cancelled() or espera.done()


# ------------------- o fato nao morre porque o link caiu (2026-08-07)


class _MemoriaQueCai:
    """Recusa gravar ate `liberar()`. Imita o link TCP fora do ar.

    Foi assim de verdade em 05/08 22:28: `ErroTransporte: Transporte TCP
    nao conectado` no meio do `remember`, com o fato ja extraido - e ele
    se perdeu, junto com a chamada paga ao modelo que o produziu.
    """

    def __init__(self):
        self.no_ar = False
        self.gravados: list[dict] = []

    def liberar(self):
        self.no_ar = True

    async def remember(self, categoria, dados):
        if not self.no_ar:
            raise RuntimeError("Transporte TCP nao conectado")
        self.gravados.append(dados)
        return len(self.gravados)

    async def recall(self, categoria, limite=200):
        return list(self.gravados)


class _IaFixa:
    """Devolve sempre os mesmos fatos, sem chamar modelo nenhum."""

    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = 0

    async def responder(self, *args, **kwargs):
        self.chamadas += 1
        return self.resposta


@pytest.mark.asyncio
async def test_fato_extraido_sobrevive_ao_link_caido():
    """O que a IA ja produziu nao pode ser jogado fora por falha de rede."""
    memoria = _MemoriaQueCai()
    ia = _IaFixa("cor_favorita: azul")
    extrator = ExtratorFatos(ia, memoria)

    lote = [("qual a minha cor favorita? eu ja te disse antes", "azul")]
    await extrator._extrair(lote)

    assert memoria.gravados == [], "gravou com o link fora?"
    assert extrator._pendentes, "o fato foi jogado fora em vez de ficar pendente"

    # o link volta e a proxima destilacao entrega o que ficou
    memoria.liberar()
    await extrator._extrair([])

    chaves = [g["chave"] for g in memoria.gravados]
    assert "cor_favorita" in chaves, "o fato pendente nunca chegou ao banco"
    assert not extrator._pendentes, "a fila devia ter esvaziado"


@pytest.mark.asyncio
async def test_nao_gasta_chamada_de_modelo_para_entregar_pendente():
    """Reentregar o pendente nao pode custar outra extracao paga."""
    memoria = _MemoriaQueCai()
    ia = _IaFixa("cor_favorita: azul")
    extrator = ExtratorFatos(ia, memoria)

    await extrator._extrair([("uma fala longa o bastante para valer", "ok")])
    chamadas_apos_primeira = ia.chamadas

    memoria.liberar()
    await extrator._extrair([])

    assert memoria.gravados, "o pendente nao foi entregue"
    # a segunda chamada ao modelo aconteceu por causa do lote vazio, nao do
    # pendente - o que importa e o pendente ter saido sem exigir extracao nova
    assert ia.chamadas >= chamadas_apos_primeira


@pytest.mark.asyncio
async def test_fila_de_pendentes_tem_teto():
    """Link fora por dias nao pode fazer a fila crescer sem fim."""
    memoria = _MemoriaQueCai()
    extrator = ExtratorFatos(_IaFixa("x: y"), memoria)

    extrator._guardar_pendentes([(f"chave{i}", "v") for i in range(50)])

    assert len(extrator._pendentes) == ExtratorFatos._MAX_PENDENTES
    # os MAIS NOVOS vencem
    assert extrator._pendentes[-1] == ("chave49", "v")
