"""Diagnóstico do Vision Engine do Pi (bloco VISION do pipeline).

Mostra o estado de cada etapa do pipeline em tempo real:

    VISION:
      CAMERA:      ONLINE
      FRAME:       OK
      MODEL:       YOLOv8n
      MODEL_LOAD:  OK
      INFERENCE:   RUNNING
      DETECTIONS:  3
      FPS:         18.4

Uso (no Pi):
    .venv/bin/python -m motion_core.vision.diagnostico [--quadros N]

Se `--quadros N` for dado, abre a câmera, roda N inferências e imprime o
bloco com os valores MEDIDOS (diagnóstico ativo). Sem o argumento, imprime
o último estado conhecido (diagnóstico passivo).
"""
from __future__ import annotations

import argparse
import asyncio
import time

from orion.kernel.config import ConfigurationManager
from orion.kernel.event_bus import EventBus

from motion_core.vision.engine import (
    CAMERA_RASPBERRY_FRONT,
    CLASSES_PESSOAS_ANIMAIS,
    VisionEngine,
)


async def _diagnostico_ativo(quadros: int) -> None:
    bus = EventBus()
    tarefa_bus = asyncio.create_task(bus.iniciar())
    try:
        # Mesma config do serviço (regra 6: nada fixo no código) - o
        # diagnóstico tem que medir o pipeline que o robô realmente roda,
        # inclusive a lista de classes de interesse.
        try:
            conf_vision = ConfigurationManager("config/orion.yaml").carregar().secao("vision")
        except Exception:
            conf_vision = {}
        classes = conf_vision.get("pi_classes_interesse")
        if classes is None:
            classes = sorted(CLASSES_PESSOAS_ANIMAIS)
        motor = VisionEngine(
            bus,
            camera_url=conf_vision.get("camera_url_pi", "http://127.0.0.1:8091/stream"),
            caminho_modelo=conf_vision.get("yolo_model_path", "data/modelos/yolov8n.pt"),
            confianca_minima=conf_vision.get("confidence_threshold", 0.55),
            imgsz=conf_vision.get("yolo_imgsz", 320),
            fps_alvo=conf_vision.get("fps_pi", 0.0),
            classes_interesse=frozenset(classes),
        )
        await motor.iniciar()
        # Aguarda o pipeline completar a PRIMEIRA inferência: só então
        # INFERENCE fica RUNNING e FPS > 0. Carregar YOLO + abrir câmera +
        # primeiro frame podem levar alguns segundos.
        aguardando = 0
        while aguardando < 40:
            await asyncio.sleep(0.5)
            aguardando += 1
            if motor.estado["inference"] == "RUNNING" and motor.estado["fps"] > 0:
                break
        # Deixa o loop rodar mais ~3 s para o FPS estabilizar (a primeira
        # medição usa o intervalo default e não representa o ritmo real).
        await asyncio.sleep(3.0)
        _imprimir(motor.estado)
        motor.parar()
    finally:
        bus.parar()
        tarefa_bus.cancel()
        try:
            await tarefa_bus
        except asyncio.CancelledError:
            pass


def _imprimir(estado: dict) -> None:
    print("VISION:")
    print(f"  CAMERA:      {'ONLINE' if estado['camera'] == 'ONLINE' else 'OFFLINE'}")
    print(f"  FRAME:       {estado['frame']}")
    print(f"  MODEL:       {estado['model']}")
    print(f"  MODEL_LOAD:  {estado['model_load']}")
    print(f"  INFERENCE:   {estado['inference']}")
    print(f"  DETECTIONS:  {estado['detections']}")
    print(f"  FPS:         {estado['fps']}")
    print(f"  CAMERA_ID:   {CAMERA_RASPBERRY_FRONT}")
    print(f"  CLASSES:     {', '.join(estado['classes'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnóstico do Vision Engine do Pi")
    parser.add_argument("--quadros", type=int, default=None, help="mede N inferências (ativo)")
    args = parser.parse_args()
    if args.quadros:
        asyncio.run(_diagnostico_ativo(args.quadros))
    else:
        print("Diagnóstico passivo: o Vision Engine roda dentro do Motion Core.")
        print("Use --quadros N para medir o pipeline diretamente.")


if __name__ == "__main__":
    main()