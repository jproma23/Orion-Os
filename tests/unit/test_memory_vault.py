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
