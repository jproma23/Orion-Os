"""Chaves do orion.yaml que NENHUM codigo le (auditoria 12/11).

Config que ninguem le e promessa, nao comportamento: `security` parecia
proteger o robo e nao protegia nada. A lista abaixo e o estado CONHECIDO -
nao foi apagada porque escolher o que sai e decisao do dono. O teste falha
nos dois sentidos, para a lista nunca mentir:

- chave NOVA sem leitor (alguem escreveu config que nao liga em nada);
- chave da lista que PASSOU a ser lida (tire daqui).

"Ler" = o nome da chave aparece entre aspas em algum .py/.js do robo. As de
`voice.transcricao` vao inteiras por ** para o Whisper e nao entram.
"""
import glob
import re

import yaml

SEM_LEITOR_CONHECIDAS = {
    "system.language",
    "system.timezone",
    "system.profile",
    "communication.raspberry.transport",
    "communication.raspberry.maintenance_usb",
    # medidas do corpo guardadas como registro (test_medidas_corpo prende
    # roda_diametro_m ao firmware)
    "chassi.altura_m",
    "chassi.roda_diametro_m",
    "chassi.roda_apoio_distancia_m",
    "chassi.lidar_altura_m",
    "motion.acceleration_percent",
    "motion.autocalibrate_on_first_boot",
    "motion.min_rear_distance_cm",
    "lidar.rpm_maximo",
    "vision.resolution",
    "vision.camera_indice_luminosidade",
    "voice.piper_voice",
    "voice.volume_percent",
    "voice.preferred_mic",
    "display.avatar.initial_mode",
    "display.avatar.brightness_percent",
    "display.web.restricted_pages",
    "diagnostics",
    "lighting",
    "connectivity",
    "security",
}

#: Secoes cujo conteudo e repassado inteiro (**dict) - as folhas nao aparecem
#: no codigo pelo nome, e isso nao as torna mortas.
REPASSADAS_INTEIRAS = {"voice.transcricao"}


def _codigo() -> str:
    arquivos = (
        glob.glob("src/**/*.py", recursive=True)
        + glob.glob("motion_core/**/*.py", recursive=True)
        + glob.glob("tools/*.py")
        + glob.glob("nav2/**/*.py", recursive=True)
        + glob.glob("motion_core/webui/**/*.js", recursive=True)
    )
    return "".join(open(f, encoding="utf-8", errors="ignore").read() for f in arquivos)


def _sem_leitor() -> set[str]:
    config = yaml.safe_load(open("config/orion.yaml", encoding="utf-8"))
    codigo = _codigo()
    achadas: set[str] = set()

    def andar(secao: dict, caminho: list[str]) -> None:
        for chave, valor in secao.items():
            nome = ".".join(caminho + [str(chave)])
            if nome in REPASSADAS_INTEIRAS:
                continue
            citada = re.search(r"[\"']" + re.escape(str(chave)) + r"[\"']", codigo)
            if not citada:
                achadas.add(nome)
            elif isinstance(valor, dict):
                andar(valor, caminho + [str(chave)])

    andar(config, [])
    return achadas


def test_nenhuma_chave_nova_sem_leitor():
    novas = _sem_leitor() - SEM_LEITOR_CONHECIDAS
    assert not novas, f"config sem nenhum codigo que a leia: {sorted(novas)}"


def test_lista_nao_mente():
    voltaram = SEM_LEITOR_CONHECIDAS - _sem_leitor()
    assert not voltaram, f"passaram a ser lidas - tirar da lista: {sorted(voltaram)}"
