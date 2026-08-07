"""Identificacao da build para a marca d'agua (2026-08-07).

O que estes testes guardam: que as datas saem do GIT e nao de uma constante
escrita a mao (que mente no dia seguinte), e que a falta de git NAO derruba
a tela - marca d'agua e informacao, nao funcao vital.
"""

import os
import subprocess

from orion.display import versao


def _git(raiz, *args, data=None):
    """Roda git com o ambiente HERDADO - so a data entra por cima.

    Env minimo demais faz `git commit` sair com 128 mesmo com
    GIT_AUTHOR_* definido: ele ainda precisa do resto do ambiente para
    achar o proprio binario auxiliar.
    """
    ambiente = dict(os.environ)
    if data:
        ambiente["GIT_AUTHOR_DATE"] = data
        ambiente["GIT_COMMITTER_DATE"] = data
    subprocess.run(
        ["git", "-c", "user.name=teste", "-c", "user.email=teste@orion", *args],
        cwd=raiz,
        check=True,
        capture_output=True,
        env=ambiente,
    )


def _repo(raiz, data="2026-07-16T12:00:00"):
    raiz.mkdir(parents=True)
    _git(raiz, "init", "-q")
    (raiz / "a.txt").write_text("primeiro", encoding="utf-8")
    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-q", "-m", "primeiro", data=data)
    return raiz


def test_datas_vem_do_git(tmp_path):
    raiz = _repo(tmp_path / "repo")

    build = versao.descobrir(raiz, "1.2.3", "Fulano")

    assert build.criado_em == "2026-07-16"
    assert build.atualizado_em == "2026-07-16"
    assert build.commit != versao.DESCONHECIDO
    assert len(build.commit) >= 7


def test_criado_em_e_o_PRIMEIRO_commit_nao_o_ultimo(tmp_path):
    """A data de criacao nao pode andar junto com a de atualizacao."""
    raiz = _repo(tmp_path / "repo", data="2026-07-16T12:00:00")
    (raiz / "b.txt").write_text("segundo", encoding="utf-8")
    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-q", "-m", "segundo", data="2026-08-07T12:00:00")

    build = versao.descobrir(raiz, "1.2.3", "Fulano")

    assert build.criado_em == "2026-07-16", "a criacao andou junto com a atualizacao"
    assert build.atualizado_em == "2026-08-07"


def test_sem_git_a_tela_nao_cai(tmp_path):
    """Copia sem .git: os campos do git caem, o resto continua."""
    pasta = tmp_path / "sem_git"
    pasta.mkdir()

    build = versao.descobrir(pasta, "1.2.3", "Fulano")

    assert build.criado_em == versao.DESCONHECIDO
    assert build.atualizado_em == versao.DESCONHECIDO
    assert build.commit == versao.DESCONHECIDO
    # o que nao depende do git sobrevive
    assert build.versao == "1.2.3"
    assert build.autor == "Fulano"
    assert build.software == versao.SOFTWARE


def test_versao_e_autor_vazios_viram_desconhecido(tmp_path):
    build = versao.descobrir(tmp_path, "", "")

    assert build.versao == versao.DESCONHECIDO
    assert build.autor == versao.DESCONHECIDO


def test_como_dict_tem_os_campos_que_a_tela_le():
    """O JS le estas chaves; renomear qualquer uma apaga a linha na tela."""
    build = versao.Build("ORION OS", "0.1.0", "João Paulo", "2026-07-16", "2026-08-07", "abc1234")

    d = build.como_dict()

    assert set(d) == {"software", "versao", "autor", "criado_em", "atualizado_em", "commit"}
