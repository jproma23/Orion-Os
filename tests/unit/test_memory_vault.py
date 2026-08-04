"""Testes do VaultConhecimento (Cap 11, EDR-0021)."""
from motion_core.memory.vault import VaultConhecimento


def test_escrever_e_ler_nota(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")

    vault.escrever_nota("Aniversario da Marah", "12 de marco.")

    assert vault.ler_nota("Aniversario da Marah") == "12 de marco."


def test_ler_nota_inexistente_retorna_none(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")
    assert vault.ler_nota("nao existe") is None


def test_escrever_nota_com_links_vira_wikilinks(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")

    vault.escrever_nota("Fofão conhece Kamal", "Kamal e morador.", links=["Kamal", "Familia"])

    conteudo = vault.ler_nota("Fofão conhece Kamal")
    assert "[[Kamal]]" in conteudo
    assert "[[Familia]]" in conteudo


def test_titulo_com_caracteres_invalidos_e_sanitizado(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")

    vault.escrever_nota("Pergunta: o que é isso?", "resposta")

    arquivos = list((tmp_path / "vault").glob("*.md"))
    assert len(arquivos) == 1
    assert "?" not in arquivos[0].name
    assert ":" not in arquivos[0].name


def test_titulo_vazio_apos_sanitizar_levanta_erro(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")
    try:
        vault.escrever_nota("???", "conteudo")
        assert False, "deveria ter levantado ValueError"
    except ValueError:
        pass


def test_buscar_por_titulo_e_conteudo(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("Cor favorita do Kamal", "Kamal gosta de azul.")
    vault.escrever_nota("Comida favorita da Marah", "Marah adora pizza.")

    por_titulo = vault.buscar("kamal")
    por_conteudo = vault.buscar("pizza")

    assert len(por_titulo) == 1
    assert por_titulo[0]["titulo"] == "Cor favorita do Kamal"
    assert len(por_conteudo) == 1
    assert por_conteudo[0]["titulo"] == "Comida favorita da Marah"


def test_buscar_sem_correspondencia_retorna_vazio(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("Nota qualquer", "conteudo qualquer")
    assert vault.buscar("inexistente") == []


def test_buscar_respeita_limite(tmp_path):
    vault = VaultConhecimento(tmp_path / "vault")
    for i in range(5):
        vault.escrever_nota(f"Nota {i}", "fato em comum")

    assert len(vault.buscar("fato em comum", limite=2)) == 2


def test_diretorio_e_criado_automaticamente(tmp_path):
    caminho = tmp_path / "vault_inexistente" / "sub"
    VaultConhecimento(caminho)
    assert caminho.is_dir()


# ---------------------------------------------------------------------------
# BUSCA POR PERGUNTA DE VERDADE (2026-08-04)
#
# Os testes acima buscavam sempre UMA palavra ("kamal", "pizza") - e foi por
# isso que o defeito passou. Quem chama `buscar` na vida real e o
# mission_planner, passando a FALA INTEIRA da pessoa. A versao antiga
# procurava a consulta toda como substring, entao nenhuma pergunta de verdade
# achava coisa nenhuma: o vault era uma gaveta que ninguem conseguia abrir.
#
# A licao geral: consulta de teste que nao se parece com a consulta real nao
# esta testando a funcao, esta testando o exemplo.
# ---------------------------------------------------------------------------


def test_pergunta_inteira_acha_a_nota(tmp_path):
    """O DEFEITO ORIGINAL. Antes disto, isto devolvia []."""
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("portao dos fundos", "O portao dos fundos trava quando chove.")

    achados = vault.buscar("Fofao, o portao dos fundos esta travando de novo?")

    assert [n["titulo"] for n in achados] == ["portao dos fundos"]


def test_acento_nao_atrapalha(tmp_path):
    """Quem fala diz "portão" e quem digitou pode ter escrito "portao"."""
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("Portão", "O portão trava quando chove.")

    assert len(vault.buscar("o portao esta travado?")) == 1


def test_a_nota_com_MAIS_palavras_em_comum_vem_primeiro(tmp_path):
    """Ordenar por data traria a mais recente, que nem sempre e a que responde
    a pergunta."""
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("chuva", "Quando chove a rua alaga.")
    vault.escrever_nota("portao", "O portao dos fundos trava quando chove.")

    achados = vault.buscar("o portao trava com chuva?")

    assert achados[0]["titulo"] == "portao"


def test_pergunta_so_de_palavras_de_ligacao_nao_traz_tudo(tmp_path):
    """"e ai, o que voce acha?" nao pode despejar o vault inteiro no prompt -
    seria pior que nao buscar, porque enche o contexto de coisa irrelevante."""
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("uma nota", "conteudo qualquer")
    vault.escrever_nota("outra nota", "outro conteudo")

    assert vault.buscar("e ai, o que voce acha?") == []


def test_palavra_curta_nao_casa_dentro_de_outra(tmp_path):
    """"sol" dentro de "resolver" traria nota nenhuma a ver com a pergunta."""
    vault = VaultConhecimento(tmp_path / "vault")
    vault.escrever_nota("nota", "preciso resolver isso amanha")

    assert vault.buscar("sol") == []
