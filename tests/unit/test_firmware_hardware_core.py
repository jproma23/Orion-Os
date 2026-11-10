"""O firmware do corpo e dos ouvidos, preso por texto (auditoria de 2026-11-10).

Nao ha como rodar o C++ aqui (o ESP32 nao esta na maquina de teste, e a regra
e nao gravar placa nenhuma). O que se prende e o CONTRATO que o C++ promete e
que ja mentiu uma vez - mesmo padrao de test_medidas_corpo.py. A compilacao
de verdade e feita a parte, com o PlatformIO (o relatorio do bloco 4 mostra o
[SUCCESS]); este arquivo garante que ninguem desfaz os consertos sem um teste
vermelho avisar.

Cada teste diz o achado da auditoria (docs/auditoria_2026-11-09.md) que
prende.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from orion.communication.protocol import Mensagem, TipoMensagem

RAIZ = Path(__file__).resolve().parents[2]
HC = RAIZ / "firmware/hardware_core"
OUVIDOS = RAIZ / "firmware/ouvidos_esp32"


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


MAIN = _ler(HC / "src/main.cpp")
EXECUTOR = _ler(HC / "include/command_executor.h")
PINOS = _ler(HC / "include/pins.h")
MOTOR = _ler(HC / "include/motor_manager.h")
SAFETY = _ler(HC / "include/safety_manager.h")
FRAMING = _ler(HC / "include/framing.h")
BUSSOLA = _ler(HC / "include/bussola_manager.h")
PROTOCOLO_H = _ler(HC / "include/protocolo.h")
PID_CPP = _ler(HC / "src/velocity_pid.cpp")
CONF = yaml.safe_load(_ler(RAIZ / "config/orion.yaml"))


def _sem_comentarios(codigo: str) -> str:
    """Tira os comentarios `//` - o que se prende e o CODIGO, nao a prosa."""
    return re.sub(r"//[^\n]*", "", codigo)


def _corpo_da_funcao(codigo: str, assinatura: str) -> str:
    """Corpo `{...}` da primeira funcao cuja assinatura casa (chaves balanceadas)."""
    inicio = codigo.index(assinatura)
    abre = codigo.index("{", inicio)
    nivel = 0
    for i in range(abre, len(codigo)):
        if codigo[i] == "{":
            nivel += 1
        elif codigo[i] == "}":
            nivel -= 1
            if nivel == 0:
                return codigo[abre + 1 : i]
    raise AssertionError(f"chaves desbalanceadas em {assinatura!r}")


def _constante(codigo: str, nome: str) -> float:
    casado = re.search(rf"constexpr \w[\w ]* {nome} = ([0-9.]+)\w*;", codigo)
    assert casado, f"{nome} sumiu"
    return float(casado.group(1))


# ---------- C4-01: comando desconhecido = NACK, no lugar do ACK ----------


def test_comando_desconhecido_responde_nack_e_nao_ack():
    """C4-01. O firmware dava ACK ANTES de saber se o comando existia, e o
    send() do Python resolve no primeiro ACK/NACK que chega - um NACK depois
    do ACK seria ignorado. Entao o ACK tem que vir DEPOIS do executar()."""
    codigo = _sem_comentarios(MAIN)
    processar = _corpo_da_funcao(codigo, "void processarQuadro(")
    assert 'enviarPayloadVazio("ACK"' not in processar, (
        "processarQuadro voltou a dar ACK antes de reconhecer o comando"
    )
    tratar = _corpo_da_funcao(codigo, "bool tratarComando(")
    assert re.search(
        r'if \(comandos\.executar\(comando, payload\)\) \{\s*enviarPayloadVazio\("ACK"', tratar
    ), "ACK do CommandExecutor tem que depender do retorno de executar()"
    assert "enviarNackDesconhecido(comando, origem, idMsg);" in tratar
    nack = _corpo_da_funcao(codigo, "void enviarNackDesconhecido(")
    assert '"NACK"' in nack
    assert 'payload["motivo"] = "comando_desconhecido"' in nack
    assert 'payload["comando"] = comando' in nack


def test_executor_nao_reconhece_comando_que_nao_existe_mais():
    """C4-01/C4-04. SCAN_FRONT (radar saiu em 19/09) e FAN_ON/FAN_OFF (rele
    do ventilador nao existe) tem que cair no `return false` -> NACK."""
    codigo = _sem_comentarios(EXECUTOR)
    executar = _corpo_da_funcao(codigo, "bool executar(")
    for morto in ("SCAN_FRONT", "FAN_ON", "FAN_OFF", "RADAR_SET_ANGLE"):
        assert f'"{morto}"' not in executar, f"{morto} voltou a ser aceito"
    assert executar.rstrip().endswith("return false;")


def test_nack_casa_com_o_que_o_python_espera():
    """C4-01, lado Python: o NACK do firmware precisa de `id_referencia` (para
    achar o send() pendente) e de `motivo` no payload (vira o texto do
    ErroComunicacao). O helper do C++ passa o idMsg como idReferencia."""
    assert re.search(
        r'enviarMensagem\(Serial, "NACK", origem, payload\.as<JsonObjectConst>\(\), idMsg\)',
        MAIN,
    )
    # E o tipo existe do lado de la.
    assert TipoMensagem("NACK") is TipoMensagem.NACK


# ---------- C1-05 / C4-03: homem-morto e heartbeat ----------

MOVIMENTO_ESPERADO = {
    "MOVE_FORWARD",
    "MOVE_CONTINUOUS",
    "MOVE_DISTANCE",
    "TURN_LEFT",
    "TURN_RIGHT",
    "CMD_VEL",
    "STOP",
    "DOCK",
    "TEST_WHEEL",
}


def test_so_comando_de_movimento_renova_o_homem_morto():
    """C1-05/C4-03. Qualquer COMMAND renovava o homem-morto - o SET_PAN_TILT
    da visao mantinha vivo para sempre um MOVE_FORWARD sem dono."""
    lista = re.search(r"COMANDOS_DE_MOVIMENTO\[\] = \{(.*?)\};", EXECUTOR, re.S)
    assert lista, "COMANDOS_DE_MOVIMENTO sumiu do command_executor.h"
    nomes = set(re.findall(r'"(\w+)"', lista.group(1)))
    assert nomes == MOVIMENTO_ESPERADO
    for nao_movimento in ("SET_PAN_TILT", "PAN_TILT_SOLTAR", "LIGHT_ON", "LIGHT_OFF",
                          "WHO_ARE_YOU", "RETURN_STATUS"):
        assert nao_movimento not in nomes
    # CALIBRATE_COMPASS so com "girar": true.
    renova = _corpo_da_funcao(EXECUTOR, "inline bool renovaHomemMorto(")
    assert re.search(r'"CALIBRATE_COMPASS"\) == 0\) return payload\["girar"\] \| false;', renova)


def test_processar_quadro_so_renova_com_movimento_reconhecido():
    """C1-05/C4-03: a unica renovacao no recebimento passa por
    renovaHomemMorto, e so para comando reconhecido."""
    processar = _corpo_da_funcao(_sem_comentarios(MAIN), "void processarQuadro(")
    atribuicoes = re.findall(r"ultimoComandoRecebido = millis\(\);", processar)
    assert len(atribuicoes) == 1
    assert re.search(
        r"if \(reconhecido && orion::renovaHomemMorto\(comando, msg\[\"payload\"\]\)\) \{\s*"
        r"ultimoComandoRecebido = millis\(\);",
        processar,
    )


def test_movimento_com_fim_nao_renova_mais_sozinho():
    """C1-05/C4-03: o loop renovava o homem-morto a cada volta durante
    MOVE_DISTANCE/TURN graus - Pi travado = anda ate o fim (2 m ~ 45 s)."""
    codigo = _sem_comentarios(MAIN)
    assert not re.search(
        r"if \(motores\.movimentoComFim\(\)\) \{\s*ultimoComandoRecebido", codigo
    )


def test_heartbeat_do_pi_e_registrado_e_segura_o_movimento_com_fim():
    """C1-05/C4-03: o HEARTBEAT do Pi era recebido e ignorado; agora ele e o
    que mantem vivo um movimento com fim."""
    processar = _corpo_da_funcao(_sem_comentarios(MAIN), "void processarQuadro(")
    assert re.search(
        r'strcmp\(tipo, "HEARTBEAT"\) == 0\) \{\s*ultimoHeartbeatPi = millis\(\);', processar
    )
    avaliar = _corpo_da_funcao(_sem_comentarios(SAFETY), "bool avaliar(")
    assert "motores.movimentoComFim()" in avaliar
    assert "idadeHeartbeatPiMs > TIMEOUT_HEARTBEAT_PI_MS" in avaliar
    # movimento com fim fica FORA do timeout de comando (50 cm = ~10 s > 5 s)
    assert re.search(r"motores\.emMovimento\(\) && !comFim", avaliar)
    assert '"heartbeat_pi_perdido"' in avaliar
    assert "safety.avaliar(motores, ultimoComandoRecebido, idadeHeartbeatPi)" in MAIN


def test_prazo_do_heartbeat_cabe_entre_um_heartbeat_e_a_perda_declarada():
    """O prazo tem que aguentar um heartbeat atrasado (> 1 intervalo) e parar
    o corpo antes de o Pi declarar a perda e reconectar (que reseta o ESP32)."""
    prazo_s = _constante(SAFETY, "TIMEOUT_HEARTBEAT_PI_MS") / 1000.0
    intervalo = CONF["communication"]["heartbeat_interval_s"]
    perda = CONF["communication"]["heartbeats_lost_threshold"] * intervalo
    assert intervalo < prazo_s < perda


def test_teto_do_movimento_com_fim_acima_de_todo_pedido_legitimo():
    """C1-05/C4-03: teto de distancia/giro no firmware e LIMITE, e nao pode
    cortar pedido legitimo de nenhum caminho do Python."""
    from motion_core.behavior.contrato import ACOES as ACOES_IA
    from orion.mission.acoes_faladas import ACOES as ACOES_VOZ

    teto_cm = _constante(EXECUTOR, "DISTANCIA_MAXIMA_CM")
    teto_graus = _constante(EXECUTOR, "GIRO_MAXIMO_GRAUS")
    assert teto_cm == 300.0 and teto_graus == 720.0
    for acao in ACOES_VOZ.values():
        if acao.parametro_numerico == "distancia_cm":
            assert acao.maximo <= teto_cm, acao.nome
        if acao.parametro_numerico == "graus":
            assert acao.maximo <= teto_graus, acao.nome
    for acao in ACOES_IA.values():
        if acao.parametro == "distancia_cm":
            assert acao.maximo * acao.escala <= teto_cm, acao.nome
        if acao.parametro == "graus":
            assert acao.maximo * acao.escala <= teto_graus, acao.nome
    executar = _corpo_da_funcao(EXECUTOR, "bool executar(")
    assert "constrain(distancia, -DISTANCIA_MAXIMA_CM, DISTANCIA_MAXIMA_CM)" in executar
    assert "constrain(graus, -GIRO_MAXIMO_GRAUS, GIRO_MAXIMO_GRAUS)" in executar


# ---------- C2-03: bussola congelada ----------


def test_rumo_velho_deixa_de_ser_valido():
    """C2-03. I2C caido mantinha o rumo anterior com rumo_valido=true para
    sempre; a malha da reta 'corrigia' o encoder com um rumo parado."""
    assert _constante(BUSSOLA, "IDADE_MAXIMA_RUMO_MS") <= 500
    valido = _corpo_da_funcao(BUSSOLA, "bool rumoValido() const")
    assert "(millis() - _ultimaLeituraBoaMs) <= IDADE_MAXIMA_RUMO_MS" in valido
    atualizar = _corpo_da_funcao(BUSSOLA, "void atualizar()")
    # So a leitura que VIROU rumo carimba a hora - nao a tentativa.
    assert re.search(r"_temLeitura = true;\s*_ultimaLeituraBoaMs = agora;", atualizar)


# ---------- C4-02: teto de duty ----------


def test_teto_de_duty_no_maximo_75_por_cento_e_aplicado_em_todo_caminho():
    """C4-02. Sem teto, roda travada ficava 1,5 s a 100% (10 V num motor de 6 V)."""
    assert _constante(MOTOR, "DUTY_MAX_FRACAO") <= 0.75
    codigo = _sem_comentarios(MOTOR)
    assert "roda.pid.definirSaidaMaxima(DUTY_MAX_FRACAO);" in codigo
    assert "fminf(fabsf(saidaFisica), DUTY_MAX_FRACAO)" in codigo
    assert "constrain(fracao, 0.0f, DUTY_MAX_FRACAO)" in codigo  # malha aberta
    assert "constrain(fracao, 0.0f, 1.0f)" not in codigo
    # TEST_WHEEL tambem informa o duty que a roda RECEBEU
    assert "constrain(fracao, 0.0f, 1.0f)" not in _sem_comentarios(MAIN)
    assert MAIN.count("constrain(fracao, 0.0f, orion::DUTY_MAX_FRACAO)") == 2
    # anti-windup no teto, nao em 1,0
    pid = _sem_comentarios(PID_CPP)
    assert "1.0f" not in _corpo_da_funcao(pid, "float VelocidadePid::atualizar(")
    assert "saida > teto && erro > 0.0f" in pid


# ---------- C4-04 / C4-05: pinos e boot ----------


def test_nenhum_pino_em_strapping_que_impede_o_boot():
    """C4-04. O rele da lanterna no GPIO 2 impedia a placa de bootar
    (05/08). Nada deste mapa pode ser 0, 2 ou 12."""
    pinos = dict(re.findall(r"constexpr uint8_t (\w+) = (\d+);", PINOS))
    pinos = {nome: int(n) for nome, n in pinos.items()}
    assert pinos, "nao achei pino nenhum no pins.h"
    proibidos = {nome: n for nome, n in pinos.items() if n in {0, 2, 12}}
    assert not proibidos, f"pino de strapping no mapa: {proibidos}"
    assert pinos["RELE_LANTERNA"] == 17  # decisao do dono, 05/08
    # os reles fantasma sairam de vez - inclusive do codigo
    for fantasma in ("RELE_MOTORES", "RELE_VENTILADOR"):
        assert fantasma not in pinos
        for fonte in (MAIN, EXECUTOR, MOTOR):
            assert fantasma not in _sem_comentarios(fonte)
    # nenhum pinMode/digitalWrite com numero literal escapando do mapa
    for fonte in (MAIN, EXECUTOR, MOTOR):
        assert not re.search(r"pinMode\(\s*\d", _sem_comentarios(fonte))


def test_motores_iniciam_antes_de_tudo_no_setup():
    """C4-05. Sem rele, os IN do L298N flutuam ate motores.iniciar(); ele
    vinha depois do WiFi, da serial, do I2C e da EEPROM."""
    setup = _sem_comentarios(_corpo_da_funcao(MAIN, "void setup()")).strip()
    assert setup.startswith("motores.iniciar();")
    assert setup.count("motores.iniciar();") == 1


def test_readme_do_corpo_nao_descreve_hardware_que_saiu():
    """C4-05: o README falava de TB6600/NEMA17 e 'encoders cancelados'."""
    readme = _ler(HC / "README.md")
    assert "CANCELADOS" not in readme
    assert "passos por software" not in readme
    assert "pull-down" in readme


# ---------- C4-06: tamanho do quadro ----------

# Os maiores COMMANDs reais (provas/c4_tamanho_quadro.py da auditoria), com
# floats do pior caso (17 digitos) e a origem mais longa que existe.
CASOS_QUADRO = {
    "SET_PAN_TILT": {"comando": "SET_PAN_TILT", "pan_graus": -12.345678901234567,
                     "tilt_graus": 7.123456789012345},
    "CMD_VEL": {"comando": "CMD_VEL", "v_m_s": 0.10999999999999999,
                "w_rad_s": -0.5999999999999999},
    "MOVE_DISTANCE": {"comando": "MOVE_DISTANCE", "distancia_cm": 123.45678901234,
                      "velocidade_percent": 30},
    "TURN_RIGHT": {"comando": "TURN_RIGHT", "graus": 90.0, "velocidade_percent": 35.0},
    "CALIBRATE_COMPASS": {"comando": "CALIBRATE_COMPASS", "girar": True,
                          "velocidade_percent": 70.0},
    "TEST_WHEEL": {"comando": "TEST_WHEEL", "lado": "ambos", "sentido": -1,
                   "potencia_percent": 40.0, "duracao_ms": 800, "varrer": True},
}


def _capacidade() -> int:
    casado = re.search(r"static constexpr size_t CAPACIDADE = (\d+);", FRAMING)
    assert casado, "CAPACIDADE sumiu do framing.h"
    return int(casado.group(1))


def test_capacidade_do_quadro_tem_folga():
    """C4-06. Com 320 o TEST_WHEEL do Notebook sobrava com 4-5 bytes."""
    assert _capacidade() >= 768
    # e cabe no buffer da UART que o setup() pede
    assert re.search(r"Serial\.setRxBufferSize\((\d+)\)", MAIN)
    rx = int(re.search(r"Serial\.setRxBufferSize\((\d+)\)", MAIN).group(1))
    assert _capacidade() <= rx


@pytest.mark.parametrize("origem", ["motion_core", "mission_core", "vision_core"])
@pytest.mark.parametrize("nome", sorted(CASOS_QUADRO))
def test_maior_comando_fica_abaixo_de_80_por_cento_da_capacidade(origem, nome):
    """C4-06. O firmware guarda o JSON + 2 bytes de CRC (sem os escapes)."""
    mensagem = Mensagem.nova(
        TipoMensagem.COMMAND, origem=origem, destino="hardware_core", payload=CASOS_QUADRO[nome]
    )
    no_buffer = len(mensagem.to_bytes()) + 2
    assert no_buffer < 0.8 * _capacidade(), f"{nome} de {origem}: {no_buffer} B"


def test_quadros_invalidos_e_commit_vao_na_telemetria_completa():
    """C4-06/C4-07: campos NOVOS (o Python le a telemetria por chave e
    ignora o que nao conhece)."""
    assert 'payload["quadros_invalidos"] = decodificador.quadrosInvalidos();' in MAIN
    assert 'payload["firmware_git"] = orion::COMMIT_FIRMWARE;' in MAIN
    quem = _corpo_da_funcao(MAIN, "void responderWhoAreYou(")
    assert 'payload["firmware_git"] = orion::COMMIT_FIRMWARE;' in quem
    assert 'payload["versao_modulo"] = orion::VERSAO_FIRMWARE;' in quem


# ---------- C4-07: versao com commit ----------


def test_versao_do_firmware_carrega_o_commit_com_fallback():
    assert re.search(r'#ifndef ORION_GIT_SHA\s*#define ORION_GIT_SHA "desconhecido"', PROTOCOLO_H)
    assert 'VERSAO_FIRMWARE = "0.1.0-fase2+" ORION_GIT_SHA;' in PROTOCOLO_H
    assert re.search(r"extra_scripts\s*=\s*pre:versao_git\.py", _ler(HC / "platformio.ini"))


class _EnvFalso:
    """O pedaco do `env` do SCons que os extra_scripts usam."""

    def __init__(self, **variaveis: str) -> None:
        self.variaveis = variaveis
        self.anexado: dict[str, list] = {}

    def subst(self, texto: str) -> str:
        return self.variaveis[texto.lstrip("$")]

    def Append(self, **chaves):  # noqa: N802 - nome da API do SCons
        for chave, valor in chaves.items():
            self.anexado.setdefault(chave, []).extend(valor)

    @staticmethod
    def StringifyMacro(valor: str) -> str:  # noqa: N802 - nome da API do SCons
        return '\\"' + valor + '\\"'


def _rodar_extra_script(script: Path, env: _EnvFalso) -> None:
    codigo = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    exec(codigo, {"Import": lambda _nome: None, "env": env, "__name__": "SCons"})


def test_versao_git_sem_git_nao_quebra_o_build(tmp_path):
    """C4-07: pasta fora de repositorio -> nenhum define, cai no fallback."""
    env = _EnvFalso(PROJECT_DIR=str(tmp_path))
    _rodar_extra_script(HC / "versao_git.py", env)
    assert "CPPDEFINES" not in env.anexado


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_versao_git_injeta_o_commit_e_marca_dirty(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "main.cpp").write_text("int x;\n", encoding="utf-8")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    env = _EnvFalso(PROJECT_DIR=str(tmp_path))
    _rodar_extra_script(HC / "versao_git.py", env)
    assert env.anexado["CPPDEFINES"] == [("ORION_GIT_SHA", f'\\"{sha}\\"')]

    (tmp_path / "main.cpp").write_text("int y;\n", encoding="utf-8")
    env = _EnvFalso(PROJECT_DIR=str(tmp_path))
    _rodar_extra_script(HC / "versao_git.py", env)
    assert env.anexado["CPPDEFINES"] == [("ORION_GIT_SHA", f'\\"{sha}-dirty\\"')]


# ---------- C4-08: ouvidos ----------

OUVIDOS_MAIN = _ler(OUVIDOS / "src/main.cpp")


def test_ouvidos_sem_wifi_no_boot_reiniciam_em_vez_de_ficar_surdos():
    """C4-08. Antes: 20 s e `return` - sem servidor, sem OTA, surdo ate
    alguem tirar da tomada."""
    setup = _corpo_da_funcao(_sem_comentarios(OUVIDOS_MAIN), "void setup()")
    trecho_wifi = setup[setup.index("WiFi.begin();") : setup.index("MDNS.begin")]
    assert "ESP.restart();" in trecho_wifi
    assert "return;" not in trecho_wifi
    assert _constante(OUVIDOS_MAIN, "ESPERA_WIFI_MS") <= 30000


def test_ouvidos_ota_com_senha_fora_do_git():
    """C4-08: senha em include/segredos.h (ignorado pelo git), com modelo
    commitado; sem o arquivo o build segue sem senha e avisa."""
    assert '#if __has_include("segredos.h")' in OUVIDOS_MAIN
    assert re.search(r"#ifdef OTA_SENHA\s*ArduinoOTA\.setPassword\(OTA_SENHA\);", OUVIDOS_MAIN)
    assert "#warning" in OUVIDOS_MAIN  # avisa, nao quebra
    assert "include/segredos.h" in _ler(OUVIDOS / ".gitignore").splitlines()
    assert '#define OTA_SENHA "' in _ler(OUVIDOS / "include/segredos.exemplo.h")
    assert re.search(r"extra_scripts\s*=\s*post:ota_senha\.py", _ler(OUVIDOS / "platformio.ini"))


def test_ota_senha_passa_auth_ao_upload_so_quando_ha_segredo(tmp_path):
    projeto = tmp_path / "ouvidos"
    (projeto / "include").mkdir(parents=True)
    env = _EnvFalso(PROJECT_INCLUDE_DIR=str(projeto / "include"), PROJECT_DIR=str(projeto))
    _rodar_extra_script(OUVIDOS / "ota_senha.py", env)
    assert "UPLOAD_FLAGS" not in env.anexado

    (projeto / "include/segredos.h").write_text('#define OTA_SENHA "abc123"\n', encoding="utf-8")
    env = _EnvFalso(PROJECT_INCLUDE_DIR=str(projeto / "include"), PROJECT_DIR=str(projeto))
    _rodar_extra_script(OUVIDOS / "ota_senha.py", env)
    assert env.anexado["UPLOAD_FLAGS"] == ["--auth=abc123"]

    # o diagnostico/ (subprojeto) acha a senha no include/ de cima
    diag = projeto / "diagnostico"
    (diag / "include").mkdir(parents=True)
    env = _EnvFalso(PROJECT_INCLUDE_DIR=str(diag / "include"), PROJECT_DIR=str(diag))
    _rodar_extra_script(OUVIDOS / "ota_senha.py", env)
    assert env.anexado["UPLOAD_FLAGS"] == ["--auth=abc123"]
