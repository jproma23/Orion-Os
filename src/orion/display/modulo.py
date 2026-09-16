"""Modulo do avatar/interface local do Mission Core (EDR-0023).

Envolve o AvatarServer, que ja tinha iniciar()/encerrar() proprios - aqui so
ganha `nome` e `esta_saudavel()` para o Boot Manager tratar todos os modulos
da mesma forma.

Dono do soquete HTTP do avatar (EDR-0023 s.3): ninguem mais abre essa porta.
"""
from __future__ import annotations

import logging
from typing import Any

from orion.kernel.event_bus import EventBus

logger = logging.getLogger("orion.display.modulo")


class ModuloDisplay:
    nome = "display"

    def __init__(
        self,
        event_bus: EventBus,
        conf_visao: dict[str, Any],
        servidor: Any | None = None,
        ponte_serial: Any | None = None,
    ) -> None:
        # `servidor`/`ponte_serial` injetaveis para os testes nao precisarem
        # subir HTTP nem porta serial real
        self._event_bus = event_bus
        self._conf_visao = conf_visao
        self._servidor = servidor
        self._ponte_serial = ponte_serial
        self._no_ar = False

    async def iniciar(self) -> None:
        if self._servidor is None:
            # import local: aiohttp so e exigido de quem realmente sobe o avatar
            from orion.display.avatar_server import AvatarServer

            self._servidor = AvatarServer(
                self._event_bus,
                config_frontend={
                    "pan_limits_degrees": self._conf_visao["pan_limits_degrees"],
                    "tilt_limits_degrees": self._conf_visao["tilt_limits_degrees"],
                },
                # 0.0.0.0: o kiosk local acessa por aqui - o rosto do
                # ESP32-S3 (orion_face) NAO usa mais este SSE, ver ponte_serial.
                host="0.0.0.0",
            )
        await self._servidor.iniciar()
        if self._ponte_serial is None:
            # import local: pyserial so e exigido de quem realmente tem a
            # placa do orion_face plugada
            from orion.display.ponte_serial import PonteSerial

            self._ponte_serial = PonteSerial(self._event_bus)
        await self._ponte_serial.iniciar()
        self._no_ar = True
        logger.info("Avatar no ar")

    async def encerrar(self) -> None:
        # contrato do EDR-0023: encerrar() nunca levanta. Se o avatar falhar
        # ao fechar, os proximos modulos ainda precisam soltar os recursos
        # deles - por isso a excecao morre aqui, so registrada.
        self._no_ar = False
        if self._ponte_serial is not None:
            try:
                await self._ponte_serial.encerrar()
            except Exception:  # noqa: BLE001
                logger.warning("Falha ao encerrar a ponte serial", exc_info=True)
        if self._servidor is None:
            return
        try:
            await self._servidor.encerrar()
        except Exception:  # noqa: BLE001
            logger.warning("Falha ao encerrar o avatar", exc_info=True)

    def esta_saudavel(self) -> bool:
        return self._no_ar
