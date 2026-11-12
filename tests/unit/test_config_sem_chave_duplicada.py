"""Nenhum YAML do projeto pode repetir chave na mesma secao (auditoria 12/11).

O YAML aceita a repeticao calado e fica com a ULTIMA: `ia_estrategica` tinha
`intervalo_s: 300` e `intervalo_s: 600` - quem lia o arquivo via 300, o robo
rodava 600. Config que mente e pior que config que falta.
"""
import glob

import pytest
import yaml


class _CarregadorQueDenuncia(yaml.SafeLoader):
    pass


def _mapa_sem_repeticao(loader, node, deep=False):
    vistas: dict = {}
    for chave_no, _ in node.value:
        chave = loader.construct_object(chave_no, deep=deep)
        if chave in vistas:
            linha = chave_no.start_mark.line + 1
            raise AssertionError(f"chave '{chave}' repetida (linhas {vistas[chave]} e {linha})")
        vistas[chave] = chave_no.start_mark.line + 1
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_CarregadorQueDenuncia.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapa_sem_repeticao
)

ARQUIVOS = sorted(glob.glob("config/*.yaml") + glob.glob("nav2/**/*.yaml", recursive=True))


@pytest.mark.parametrize("arquivo", ARQUIVOS)
def test_yaml_sem_chave_repetida(arquivo):
    with open(arquivo, encoding="utf-8") as f:
        yaml.load(f, Loader=_CarregadorQueDenuncia)


def test_achou_os_yaml():
    assert "config/orion.yaml" in [a.replace("\\", "/") for a in ARQUIVOS]
