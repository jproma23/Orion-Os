"""Testes da memoria cognitiva (saber) - o "Eu ja sei?" do ORION.

Cobrem o caminho inteiro que o layout desta sessao desenha: o robo guarda
o que aprendeu (`saber_guardar`), responde do proprio acervo quando a fala
cobriu o registro (`saber_buscar` com cobertura) e conta quanto consultou a
IA (`registrar_consulta_ia` + `saber_stats`) - a medida de dependencia.
"""
import asyncio

import pytest
import pytest_asyncio

from motion_core.memory.api import MemoryAPI
from motion_core.memory.database import DatabaseManager
from orion.kernel.event_bus import EventBus


@pytest_asyncio.fixture
async def contexto(tmp_path):
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    db = DatabaseManager(tmp_path / "orion.db", tmp_path / "backups")
    db.iniciar()
    api = MemoryAPI(db, bus)
    yield api, bus
    db.fechar()
    bus.parar()
    await tarefa_bus


@pytest.mark.asyncio
async def test_migracao_cria_tabelas_cognitivas(contexto):
    api, _ = contexto
    tabelas = [
        r["name"]
        for r in api._db.conexao.execute(
            "SELECT name FROM sqlite_master WHERE type='table' OR type='virtual'"
        ).fetchall()
    ]
    for esperada in (
        "conhecimentos", "experiencias", "procedimentos",
        "dispositivos", "relacoes", "saber_fts",
    ):
        assert esperada in tabelas, f"tabela {esperada} ausente"


@pytest.mark.asyncio
async def test_saber_guardar_e_buscar_pelo_conceito(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {
            "tipo": "conceito",
            "titulo": "BME280",
            "corpo": "Sensor de temperatura, pressao e umidade, "
            "comunicacao I2C, endereco padrao 0x76.",
            "fonte": "dono",
            "confianca": 0.9,
        },
    )

    achados = await api.saber_buscar("o que é um BME280?", limite=3)
    assert len(achados) == 1
    assert achados[0]["categoria"] == "conhecimentos"
    assert achados[0]["titulo"] == "BME280"
    assert "I2C" in achados[0]["corpo"]
    assert "nota_bm25" in achados[0]


@pytest.mark.asyncio
async def test_saber_busca_procedimento_normaliza_titulo_corpo(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "procedimentos",
        {"nome": "Ler sensor de distancia", "passos": "Pingar, esperar o eco."},
    )

    achados = await api.saber_buscar("como leio o sensor de distancia", limite=3)
    assert len(achados) == 1
    assert achados[0]["categoria"] == "procedimentos"
    assert achados[0]["titulo"] == "Ler sensor de distancia"


@pytest.mark.asyncio
async def test_saber_nao_responde_palavra_solta_sem_cobertura(contexto):
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Lampada", "corpo": "Di jpa LED na cabeca, liga e apaga."},
    )

    # Uma palavra generica ("isso") nao cobre o assunto - nada responde.
    assert await api.saber_buscar("isso ai doutor", limite=3) == []
    # Ate a pergunta legitima responde.
    assert len(await api.saber_buscar("o que e a lampada", limite=3)) == 1


@pytest.mark.asyncio
async def test_saber_nao_responde_comando_de_acao_sozinho(contexto):
    """Achado real (2026-08-16): "der uma olha" casava com "Pensamento
    sistemico do dono" porque "olha" virava termo util e o texto do registro
    continha a palavra no corpo - o "Eu ja sei?" despejava texto cru fora de
    contexto. Verbos de acao/comando nao sao temas de conhecimento: pedido
    para o robo FAZER algo nao pode responder do acervo."""
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Pensamento sistemico do dono",
         "corpo": "Joao nao olha so o componente: enxerga a cadeia causal."},
    )

    # Comando de acao sozinho ("der uma olha", "olha isso") nao responde.
    assert await api.saber_buscar("der uma olha", limite=3) == []
    assert await api.saber_buscar("olha isso", limite=3) == []

    # Pergunta legitima sobre o MESMO registro ainda responde.
    achados = await api.saber_buscar(
        "como e o pensamento sistemico do dono", limite=3
    )
    assert len(achados) == 1


@pytest.mark.asyncio
async def test_saber_contador_de_consultas_ia(contexto):
    api, _ = contexto

    await api.registrar_consulta_ia()
    await api.registrar_consulta_ia()

    stats = await api.saber_stats()
    assert stats["consultas_ia"] == 2
    assert stats["total_conhecimento_local"] == 0


@pytest.mark.asyncio
async def test_saber_stats_conta_quatro_categorias(contexto):
    api, _ = contexto
    await api.saber_guardar("conhecimentos", {"titulo": "A", "corpo": "x"})
    await api.saber_guardar("experiencias", {"situacao": "caiu", "o_que_aconteceu": "tombou"})
    await api.saber_guardar("procedimentos", {"nome": "Subir", "passos": "girar"})
    await api.saber_guardar("dispositivos", {"nome": "led", "tipo": "led", "acesso": "GPIO17"})
    await api.saber_guardar("relacoes", {
        "origem_tipo": "dispositivo", "origem_id": 1, "relacao": "alimenta",
        "destino_tipo": "skill", "destino_id": 1,
    })

    stats = await api.saber_stats()
    assert stats["conhecimentos"] == 1
    assert stats["experiencias"] == 1
    assert stats["procedimentos"] == 1
    assert stats["dispositivos"] == 1
    assert stats["relacoes"] == 1
    assert stats["total_conhecimento_local"] == 5


@pytest.mark.asyncio
async def test_contar_repeticoes_filtra_situacao_resultado_e_janela(contexto):
    """O degrau do ciclo fechado conta so o desfecho que interessa: mesma
    situacao, mesmo resultado, dentro da janela. Uma falha antiga fora da
    janela, ou de outra situacao, NAO conta - senao o robo pararia por
    qualquer coisa que aconteceu ha horas."""
    api, _ = contexto
    await api.saber_guardar("experiencias", {
        "situacao": "avancar", "o_que_falhou": "obstaculo", "resultado": "falhou",
    })
    await api.saber_guardar("experiencias", {
        "situacao": "avancar", "o_que_falhou": "obstaculo", "resultado": "falhou",
    })
    await api.saber_guardar("experiencias", {
        "situacao": "seguir_pessoa", "o_que_falhou": "perdeu alvo", "resultado": "falhou",
    })
    await api.saber_guardar("experiencias", {
        "situacao": "avancar", "o_que_falhou": "obstaculo", "resultado": "funcionou",
    })

    assert await api.contar_repeticoes("avancar", "falhou", 300) == 2
    assert await api.contar_repeticoes("seguir_pessoa", "falhou", 300) == 1
    assert await api.contar_repeticoes("avancar", "funcionou", 300) == 1
    assert await api.contar_repeticoes("girar", "falhou", 300) == 0


@pytest.mark.asyncio
async def test_contar_repeticoes_respeita_a_janela_de_tempo(contexto):
    """Uma falha antiga nao entra na janela: passada a janela, o numero zera.
    E o que impede o degrau de travar o robo para sempre num corredor que um
    dia esteve bloqueado."""
    api, _ = contexto
    from datetime import UTC, datetime, timedelta

    antigo = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    await api.saber_guardar("experiencias", {
        "situacao": "avancar", "o_que_falhou": "obstaculo", "resultado": "falhou",
        "criado_em": antigo,
    })
    await api.saber_guardar("experiencias", {
        "situacao": "avancar", "o_que_falhou": "obstaculo", "resultado": "falhou",
    })

    assert await api.contar_repeticoes("avancar", "falhou", 300) == 1
    assert await api.contar_repeticoes("avancar", "falhou", 3600) == 2


@pytest.mark.asyncio
async def test_saber_nao_casa_pedaco_de_palavra(contexto):
    """Achado real (2026-10-25), com as duas falas do teste do dono: a
    cobertura achava PEDACO de palavra, e conversa comum virava ficha de
    codigo lida em voz alta."""
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "src/orion/mission/diario.py",
         "corpo": "Diario do robo. Funcoes: hojeate, observacoesdehoje."},
    )
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "src/orion/mission/recados.py",
         "corpo": "Recados: guardar uma mensagem. Funcoes: mesmo_nome, deixar."},
    )

    assert await api.saber_buscar("Bom dia! Como voce esta hoje?", limite=3) == []
    assert await api.saber_buscar("Diga em uma frase curta qual e o seu nome.", limite=3) == []
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Aula", "corpo": "Cada aula e uma chamada de IA na autoria."},
    )
    assert await api.saber_buscar("Como voce se chama?", limite=3) == []


@pytest.mark.asyncio
async def test_saber_ainda_aceita_plural(contexto):
    """A palavra inteira continua com folga de plural/genero: a fala no
    singular acha o registro escrito no plural. (O sentido contrario nem chega
    aqui: o FTS busca por prefixo, e "lampadas*" nao acha "lampada".)"""
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Lampadas da cabeca", "corpo": "LEDs que ligam e apagam."},
    )
    assert len(await api.saber_buscar("como funciona a lampada da cabeca", limite=3)) == 1


@pytest.mark.asyncio
async def test_saber_atualizar_troca_o_texto_e_a_busca(contexto):
    """2026-10-25: o nome mudou e o conhecimento gravado precisava mudar
    junto. Depois de atualizar, a busca acha o texto novo e nao o velho."""
    api, _ = contexto
    ref = await api.saber_guardar(
        "conhecimentos",
        {"titulo": "Quem sou eu", "corpo": "Sou o Fofao, robo cuidador da casa."},
    )

    await api.saber_atualizar(
        "conhecimentos", ref, {"corpo": "Sou o Jarvis, robo cuidador da casa."}
    )

    achados = await api.saber_buscar("quem sou eu robo cuidador", limite=3)
    assert achados and "Jarvis" in achados[0]["corpo"]
    # o indice tambem foi regravado: o nome velho nao acha mais nada
    assert await api.saber_buscar("fofao", limite=3) == []


@pytest.mark.asyncio
async def test_saber_palavra_unica_so_no_corpo_nao_responde(contexto):
    """Achado real (2026-11-01): "precisa da re" virou so "precisa", que
    estava no CORPO dos principios do dono - e ele leu o registro em voz alta."""
    api, _ = contexto
    await api.saber_guardar(
        "conhecimentos",
        {
            "titulo": "Principios intelectuais do dono",
            "corpo": "Realidade: vale o que funciona. Simplicidade: so o que precisa.",
        },
    )

    assert await api.saber_buscar("precisa da re", limite=3) == []
    # A palavra que E o assunto continua achando.
    assert len(await api.saber_buscar("principios", limite=3)) == 1
