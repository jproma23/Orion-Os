#!/usr/bin/env python3
"""Ferramenta de bancada: mostra o que o robo esta enxergando pelo LiDAR AGORA.

Le o estado publicado pelo Motion Core (nao abre a serial - o servico ja e
dono dela), entao pode rodar com o robo em pe, sem disputar a porta.

Uso:
    python tools/medir_lidar.py            # uma medida
    python tools/medir_lidar.py --repetir  # repete a cada 2s ate Ctrl+C

Para comparar dois cenarios (antes/depois de mover um movel), rode uma vez em
cada e olhe os setores que mudaram: e o teste mais barato de que o sensor esta
medindo o mundo e nao repetindo lixo.
"""
import argparse
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8080/estado"
#: 0° = esquerda, 90° = frente, 180° = direita (convencao do mapa da web).
ROSA = {0: "esquerda", 90: "FRENTE", 180: "direita", 270: "tras"}


def ler_estado(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def mostrar(url: str) -> bool:
    """Devolve True se havia varredura valida."""
    try:
        estado = ler_estado(url).get("estado", {})
    except Exception as exc:
        print(f"nao consegui falar com a webui ({exc})")
        return False

    lidar = estado.get("lidar")
    if not lidar:
        print("LiDAR: sem varredura no estado.")
        print("  (o servico publica so quando TODOS os setores tem eco -")
        print("   sensor encostado ou virado pro vazio deixa buraco)")
        return False

    setores = lidar.get("setores") or []
    print(f"pontos: {lidar.get('pontos')}   rpm: {lidar.get('rpm')} "
          f"(0.0 e esperado: campo de velocidade nao decifrado)")
    print()
    for s in setores:
        ini = s.get("inicio_graus")
        cm = s.get("distancia_cm")
        marca = ROSA.get(int(ini) if ini is not None else -1, "")
        barra = ""
        if cm is not None:
            barra = "#" * min(40, max(1, int(cm / 10)))
        texto = f"{cm:7.1f} cm" if cm is not None else "   sem eco"
        print(f"  {ini:5.0f}-{s.get('fim_graus'):3.0f}  {texto}  {barra} {marca}")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repetir", action="store_true", help="repete a cada 2s")
    p.add_argument("--url", default=URL)
    args = p.parse_args()

    if not args.repetir:
        return 0 if mostrar(args.url) else 1

    try:
        while True:
            print("\033[2J\033[H", end="")  # limpa a tela
            print(time.strftime("%H:%M:%S"))
            mostrar(args.url)
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
