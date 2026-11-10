# Injeta o commit do git no firmware: -D ORION_GIT_SHA="<sha>[-dirty]".
#
# POR QUE (2026-11-10, auditoria C4-07): nenhum .bin gravado no corpo dizia de
# que commit tinha saido. A placa responde isto em WHO_ARE_YOU e na
# telemetria completa (protocolo.h, main.cpp), e "o que esta rodando?" passa a
# ter resposta sem regravar nada.
#
# "-dirty" olha SO esta pasta (firmware/hardware_core): mudanca no Python nao
# muda o binario, entao nao deve sujar a versao dele.
#
# Sem git (copia solta da pasta, maquina sem git no PATH) o build NAO quebra:
# o define nao e passado e o protocolo.h cai em "desconhecido".
import subprocess

Import("env")  # noqa: F821 - injetado pelo PlatformIO/SCons


def _git(*args):
    return subprocess.run(
        ["git", *args],
        cwd=env.subst("$PROJECT_DIR"),  # noqa: F821
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()


try:
    sha = _git("rev-parse", "--short", "HEAD")
    if _git("status", "--porcelain", "--", "."):
        sha += "-dirty"
except Exception as erro:  # git ausente, pasta fora de repositorio, timeout
    print(f"versao_git: sem commit ({erro.__class__.__name__}) - firmware fica 'desconhecido'")
else:
    print(f"versao_git: ORION_GIT_SHA={sha}")
    env.Append(CPPDEFINES=[("ORION_GIT_SHA", env.StringifyMacro(sha))])  # noqa: F821
