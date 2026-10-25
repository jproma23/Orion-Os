"""ORION API - FastAPI endpoint para receber comandos do JARVIS Mark 54.

Roda no Notebook (porta 8081) junto com conversar_jarvis.py.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("orion.api")

# Variáveis globais injetadas pelo conversar_jarvis.py
_mission_planner = None
_quem_fala = None


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


# Para rodar standalone (teste)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
