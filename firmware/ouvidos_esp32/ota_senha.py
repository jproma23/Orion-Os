# Passa a senha do OTA ao espota: `--auth=<OTA_SENHA de include/segredos.h>`.
#
# POR QUE (2026-11-10, auditoria C4-08): o OTA ganhou senha, e a senha tem
# que estar em DOIS lugares - no firmware (que exige) e no comando de upload
# (que manda). Escrever duas vezes e pedir para as duas divergirem; este
# script le a mesma linha do segredos.h que o main.cpp compila.
#
# Sem segredos.h nao passa nada: o upload vai sem senha, como antes, e o
# firmware compilado tambem fica sem senha (main.cpp avisa no log).
import os
import re

Import("env")  # noqa: F821 - injetado pelo PlatformIO/SCons

# O include/ do proprio projeto e, para o diagnostico/ (subprojeto), o
# include/ dos ouvidos logo acima - a mesma senha nos dois firmwares.
_CANDIDATOS = [
    os.path.join(env.subst("$PROJECT_INCLUDE_DIR"), "segredos.h"),  # noqa: F821
    os.path.join(env.subst("$PROJECT_DIR"), "..", "include", "segredos.h"),  # noqa: F821
]
_CAMINHO = next((c for c in _CANDIDATOS if os.path.isfile(c)), None)

if _CAMINHO:
    with open(_CAMINHO, encoding="utf-8") as arquivo:
        casado = re.search(r'#define\s+OTA_SENHA\s+"([^"]*)"', arquivo.read())
    if casado:
        env.Append(UPLOAD_FLAGS=[f"--auth={casado.group(1)}"])  # noqa: F821
    else:
        print("ota_senha: segredos.h sem OTA_SENHA - upload SEM senha")
else:
    print("ota_senha: include/segredos.h ausente - upload SEM senha")
