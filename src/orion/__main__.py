"""Ponto de entrada do ORION OS.

Fase 0: apenas confirma que o pacote esta instalado.
A sequencia de boot real (Cap 6, secao 4) sera implementada na Fase 1.
"""
import sys

VERSAO = "0.1.0"


def main() -> int:
    sim = "--sim" in sys.argv
    modo = "SIMULADO" if sim else "REAL"
    print(f"ORION OS v{VERSAO} - modo {modo}")
    print("Boot ainda nao implementado. Consulte PLANO_IMPLEMENTACAO.md (Fase 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
