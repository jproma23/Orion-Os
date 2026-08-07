"""Mission Memory: indice do projeto sobre si mesmo (2026-08-06).

Monta um repositorio de mentira em tmp_path - journal, git de verdade e
EDRs - e confere o que a busca precisa acertar para servir de alguma coisa:
achar sem acento, nao confundir sessao com commit, e devolver a REFERENCIA
(hash/data), que e o que faz a resposta ser conferivel.
"""

import subprocess

import pytest

from tools import mission_memory


def _repo_de_mentira(raiz):
    """Journal + EDR + git com dois commits de mensagem parecida com as reais."""
    (raiz / "docs").mkdir(parents=True)
    (raiz / "docs/edr").mkdir()
    (raiz / "docs/journal.md").write_text(
        "# Journal\n"
        "\n"
        "preambulo que nao e sessao nenhuma\n"
        "\n"
        "## Sessao de 2026-08-05 - o Whisper inventava\n"
        "\n"
        "905 janelas transcritas, 27 estavam em laco.\n"
        "\n"
        "## Sessao de 2026-08-06 - a reconexao da serial\n"
        "\n"
        "Tirar o ESP32 do USB travava tudo ate reiniciar o servico.\n",
        encoding="utf-8",
    )
    (raiz / "docs/edr/EDR-0021-ia-remota.md").write_text(
        "# EDR-0021 - IA remota e memoria hibrida\n\nDecidido em 2026-07-20.\n",
        encoding="utf-8",
    )

    def _git(*args):
        subprocess.run(["git", *args], cwd=raiz, check=True, capture_output=True)

    _git("init", "-q")
    _git("config", "user.email", "teste@orion")
    _git("config", "user.name", "teste")
    _git("add", "-A")
    _git("commit", "-q", "-m", "docs: journal e EDR")
    (raiz / "codigo.py").write_text("# nada\n", encoding="utf-8")
    _git("add", "-A")
    _git(
        "commit",
        "-q",
        "-m",
        "[serial] tirar o ESP32 do USB travava tudo\n\n"
        "A maquina de reconexao ja existia e faltava passar o callback.\n",
    )
    return raiz


def test_construir_conta_os_tres_tipos(tmp_path):
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"

    contagem = mission_memory.construir(db, raiz)

    assert contagem["journal"] == 2, "o preambulo antes do primeiro ## nao e sessao"
    assert contagem["edr"] == 1
    assert contagem["commit"] == 2


def test_busca_ignora_acento_nos_dois_sentidos(tmp_path):
    """Quem pergunta com pressa digita 'reconexao'; o texto diz 'reconexao'.

    O contrario tambem: perguntar com til tem que achar o texto sem til.
    """
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"
    mission_memory.construir(db, raiz)

    sem_acento = mission_memory.buscar(db, "reconexao serial")
    com_acento = mission_memory.buscar(db, "reconexão serial")

    assert sem_acento, "nao achou escrevendo sem acento"
    assert com_acento, "nao achou escrevendo com acento"
    assert {a.ref for a in sem_acento} == {a.ref for a in com_acento}


def test_busca_devolve_a_referencia_conferivel(tmp_path):
    """Sem hash/data a resposta nao vale nada - ninguem consegue conferir."""
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"
    mission_memory.construir(db, raiz)

    achados = mission_memory.buscar(db, "ESP32 USB travava")

    assert achados
    for achado in achados:
        assert achado.ref, "achado sem referencia"
        assert achado.trecho, "achado sem trecho"
    commits = [a for a in achados if a.tipo == "commit"]
    assert commits, "o commit que fala do ESP32 tinha que aparecer"
    # hash curto do git tem pelo menos 7 caracteres hexadecimais
    assert len(commits[0].ref) >= 7


def test_filtro_por_tipo(tmp_path):
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"
    mission_memory.construir(db, raiz)

    so_journal = mission_memory.buscar(db, "ESP32 USB", tipo="journal")

    assert so_journal
    assert {a.tipo for a in so_journal} == {"journal"}


def test_pontuacao_nao_derruba_a_consulta(tmp_path):
    """Aspas, hifen e parenteses sao OPERADORES no FTS5.

    Sem tratamento, perguntar `"que horas" (o robo)` sobe OperationalError
    com sintaxe crua na cara de quem perguntou.
    """
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"
    mission_memory.construir(db, raiz)

    achados = mission_memory.buscar(db, '"serial" - (reconexao)?')

    assert achados, "a pontuacao derrubou a busca"


def test_pergunta_sem_palavra_avisa_em_vez_de_estourar(tmp_path):
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"
    mission_memory.construir(db, raiz)

    with pytest.raises(ValueError):
        mission_memory.buscar(db, "?!...")


def test_construir_e_idempotente(tmp_path):
    """Reconstruir nao pode duplicar - o indice e artefato derivado."""
    raiz = _repo_de_mentira(tmp_path / "repo")
    db = tmp_path / "mm.db"

    primeira = mission_memory.construir(db, raiz)
    segunda = mission_memory.construir(db, raiz)

    assert primeira == segunda
    assert dict(mission_memory.estado(db)) == segunda


def test_buscar_sem_indice_diz_o_que_fazer(tmp_path):
    with pytest.raises(FileNotFoundError, match="construir"):
        mission_memory.buscar(tmp_path / "nao_existe.db", "qualquer coisa")


# --------------------------------------------------------------- codigo


def _repo_com_codigo(raiz):
    """Repo minimo com codigo em Python, firmware e um teste (que fica fora)."""
    (raiz / "motion_core/behavior").mkdir(parents=True)
    (raiz / "tests/unit").mkdir(parents=True)
    (raiz / "firmware/hardware_core/include").mkdir(parents=True)

    (raiz / "motion_core/behavior/comportamentos.py").write_text(
        "import os\n"
        "\n"
        "\n"
        "# A cabeca volta ao centro por OCIOSIDADE, nao por relogio -\n"
        "# relogio cru arrancaria a cabeca do dedo de quem esta mexendo.\n"
        "def recentrar_cabeca(pan, tilt):\n"
        "    \"\"\"Devolve a cabeca ao centro depois de ociosa.\"\"\"\n"
        "    return 0, 0\n"
        "\n"
        "\n"
        "class Repouso:\n"
        "    \"\"\"Comportamento base do maestro, prioridade minima.\"\"\"\n"
        "\n"
        "    prioridade = 10\n"
        "\n"
        "    def quer_rodar(self):\n"
        "        return True\n",
        encoding="utf-8",
    )
    (raiz / "tests/unit/test_nao_indexar.py").write_text(
        "def test_isto_nao_pode_entrar_no_indice():\n"
        "    assert True  # palavrachavesoDoTeste\n",
        encoding="utf-8",
    )
    (raiz / "firmware/hardware_core/include/pins.h").write_text(
        "// Espelhamento e fato de HARDWARE, por isso mora aqui.\n"
        "constexpr bool DIR_INVERTIDO_DIREITO = true;\n",
        encoding="utf-8",
    )
    return raiz


def test_codigo_e_fatiado_por_funcao_e_classe(tmp_path):
    raiz = _repo_com_codigo(tmp_path / "repo")

    docs = mission_memory.ler_codigo(raiz)

    nomes = {d.titulo.split(" :: ")[-1] for d in docs if "::" in d.titulo}
    assert "recentrar_cabeca" in nomes
    assert "Repouso" in nomes


def test_o_comentario_de_cima_vem_junto(tmp_path):
    """Neste projeto o PORQUE mora no comentario que antecede a funcao."""
    raiz = _repo_com_codigo(tmp_path / "repo")

    docs = mission_memory.ler_codigo(raiz)
    trecho = next(d for d in docs if d.titulo.endswith("recentrar_cabeca"))

    assert "OCIOSIDADE" in trecho.texto, "o comentario acima da funcao ficou de fora"


def test_referencia_do_codigo_tem_arquivo_e_linha(tmp_path):
    raiz = _repo_com_codigo(tmp_path / "repo")

    docs = mission_memory.ler_codigo(raiz)
    trecho = next(d for d in docs if d.titulo.endswith("recentrar_cabeca"))

    assert trecho.ref.startswith("motion_core/behavior/comportamentos.py:")
    assert trecho.ref.split(":")[-1].isdigit()


def test_os_testes_nao_entram_no_indice(tmp_path):
    """Quem pergunta "onde isso e tratado" quer a implementacao."""
    raiz = _repo_com_codigo(tmp_path / "repo")

    docs = mission_memory.ler_codigo(raiz)

    assert not any("tests/" in d.ref for d in docs)


def test_firmware_entra_por_arquivo_inteiro(tmp_path):
    """O `ast` e de Python; .h e .cpp sao pequenos e entram inteiros."""
    raiz = _repo_com_codigo(tmp_path / "repo")

    docs = mission_memory.ler_codigo(raiz)
    pins = [d for d in docs if d.ref.endswith("pins.h")]

    assert len(pins) == 1
    assert "DIR_INVERTIDO_DIREITO" in pins[0].texto


def test_arquivo_com_sintaxe_quebrada_nao_derruba_a_carga(tmp_path):
    """Um .py invalido no repo nao pode impedir o indice inteiro."""
    raiz = _repo_com_codigo(tmp_path / "repo")
    (raiz / "motion_core/quebrado.py").write_text("def (((", encoding="utf-8")

    docs = mission_memory.ler_codigo(raiz)

    assert docs, "a carga parou por causa de um arquivo invalido"


def test_construir_inclui_o_codigo(tmp_path):
    raiz = _repo_com_codigo(tmp_path / "repo")
    (raiz / "docs").mkdir()
    (raiz / "docs/journal.md").write_text(
        "## Sessao de 2026-08-07 - nada\n\ncorpo\n", encoding="utf-8"
    )
    db = tmp_path / "mm.db"

    contagem = mission_memory.construir(db, raiz)

    assert contagem.get("codigo", 0) >= 3
    achados = mission_memory.buscar(db, "recentrar cabeca ociosidade", tipo="codigo")
    assert achados
