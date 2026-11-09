"""ORION API - FastAPI endpoint para receber comandos do JARVIS Mark 54.

Roda no Notebook (porta 8081) junto com conversar_jarvis.py.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("orion.api")

# Variáveis globais injetadas pelo conversar_jarvis.py
_mission_planner = None
_quem_fala = None
#: Quem entrega o JPEG do momento (ModuloVisao.foto_agora). None = sem visao.
_foto_camera = None


class ExecuteRequest(BaseModel):
    command: str
    pessoa_id: int | None = None


class ExecuteResponse(BaseModel):
    success: bool
    response: str | None = None
    error: str | None = None
    metadata: dict = {}


def set_mission_planner(planner, quem_fala):
    "Injetado pelo conversar_jarvis.py na inicialização."
    global _mission_planner, _quem_fala
    _mission_planner = planner
    _quem_fala = quem_fala


def set_camera(foto_camera):
    "Injetado pelo conversar_jarvis.py depois que a visao sobe."
    global _foto_camera
    _foto_camera = foto_camera


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("ORION API iniciada na porta 8081")
    yield
    # Shutdown
    logger.info("ORION API encerrada")


app = FastAPI(title="ORION API", version="1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "orion-api"}


@app.post("/api/orion/execute", response_model=ExecuteResponse)
async def execute_command(req: ExecuteRequest):
    "Executa comando no MissionPlanner (resposta única)."
    if _mission_planner is None:
        raise HTTPException(503, "MissionPlanner não inicializado")

    try:
        pessoa_id = req.pessoa_id
        if pessoa_id is None and _quem_fala:
            pessoa_id = _quem_fala.pessoa_id()

        resposta = await _mission_planner.processar(req.command, pessoa_id)
        return ExecuteResponse(success=True, response=resposta)
    except Exception as e:
        logger.exception("Erro ao executar comando")
        return ExecuteResponse(success=False, error=str(e))


@app.post("/api/orion/execute_stream")
async def execute_command_stream(req: ExecuteRequest):
    "Executa comando no MissionPlanner com streaming (frase a frase)."
    if _mission_planner is None:
        raise HTTPException(503, "MissionPlanner não inicializado")

    async def generate():
        try:
            pessoa_id = req.pessoa_id
            if pessoa_id is None and _quem_fala:
                pessoa_id = _quem_fala.pessoa_id()

            async for frase in _mission_planner.responder_em_frases(req.command, pessoa_id):
                yield f"data: {frase}\n\n"
        except Exception as e:
            logger.exception("Erro no streaming")
            yield f"data: Erro: {e}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/camera/snapshot.jpg")
async def camera_snapshot():
    """O ultimo quadro que o Vision Core leu (2026-11-01).

    503 quando nao ha imagem: visao fora, pausada pelo guardiao de RAM ou
    camera recem-aberta. Sem cache, senao o navegador mostra foto velha.
    """
    foto = _foto_camera() if _foto_camera else None
    if foto is None:
        raise HTTPException(503, "Sem imagem da camera agora")
    return Response(foto, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/camera", response_class=HTMLResponse)
async def camera_pagina():
    "Pagina simples que recarrega o snapshot a cada segundo."
    return """<!doctype html><meta charset="utf-8"><title>Camera do robo</title>
<body style="margin:0;background:#111;color:#ccc;font-family:sans-serif;text-align:center">
<img id="f" style="max-width:100%" alt="camera"><p id="s"></p>
<script>
const f = document.getElementById("f"), s = document.getElementById("s");
f.onload = () => { s.textContent = new Date().toLocaleTimeString(); };
f.onerror = () => { s.textContent = "sem imagem (visao fora?)"; };
setInterval(() => { f.src = "/camera/snapshot.jpg?t=" + Date.now(); }, 1000);
f.src = "/camera/snapshot.jpg";
</script>"""


# Para rodar standalone (teste)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
