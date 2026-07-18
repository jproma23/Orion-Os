"""AI Manager (Cap 7 secao 2-3) - integra o Ollama.

Prompt de sistema fixo (config/prompt_sistema.txt, Cap 17) + contexto vindo
da memoria (Cap 11, via MemoryClient/comm.request). Usa o cliente HTTP
oficial da lib `ollama` contra o servidor local ja instalado nesta
montagem (Cap 17: ai.ollama_model = llama3.2:3b).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import ollama


class AiManager:
    def __init__(
        self,
        modelo: str = "llama3.2:3b",
        temperatura: float = 0.6,
        caminho_prompt_sistema: str | Path = "config/prompt_sistema.txt",
        max_tokens_resposta: int | None = None,
        keep_alive_minutes: int | None = None,
    ) -> None:
        self._modelo = modelo
        self._temperatura = temperatura
        self._prompt_sistema = Path(caminho_prompt_sistema).read_text(encoding="utf-8")
        # Numa conversa falada, resposta longa = espera longa duas vezes
        # (gerar + sintetizar/falar). Limitar os tokens mantem o dialogo agil.
        self._max_tokens_resposta = max_tokens_resposta
        # Sem keep_alive o Ollama descarrega o modelo apos ~5min ocioso e a
        # proxima resposta paga o recarregamento inteiro (dezenas de segundos).
        self._keep_alive = f"{keep_alive_minutes}m" if keep_alive_minutes else None
        self._cliente = ollama.Client()

    async def responder(self, texto_usuario: str, contexto: dict | None = None) -> str:
        mensagens = [
            {"role": "system", "content": self._montar_prompt_sistema(contexto)},
            {"role": "user", "content": texto_usuario},
        ]

        def _chamar() -> str:
            opcoes: dict = {"temperature": self._temperatura}
            if self._max_tokens_resposta:
                opcoes["num_predict"] = self._max_tokens_resposta
            resposta = self._cliente.chat(
                model=self._modelo,
                messages=mensagens,
                options=opcoes,
                keep_alive=self._keep_alive,
            )
            return resposta["message"]["content"]

        return await asyncio.to_thread(_chamar)

    def _montar_prompt_sistema(self, contexto: dict | None) -> str:
        if not contexto:
            return self._prompt_sistema

        partes = []
        pessoa = contexto.get("pessoa")
        if pessoa:
            partes.append(f"Voce esta falando com {pessoa.get('nome', 'alguem')}.")

        conversas = contexto.get("conversas_recentes") or []
        if conversas:
            historico = "\n".join(f"{c['papel']}: {c['texto']}" for c in conversas[-5:])
            partes.append(f"Historico recente da conversa:\n{historico}")

        conhecimento = contexto.get("conhecimento_relevante") or []
        if conhecimento:
            fatos = "\n".join(f"- {c['chave']}: {c['valor']}" for c in conhecimento[:5])
            partes.append(f"Fatos que voce ja sabe:\n{fatos}")

        if not partes:
            return self._prompt_sistema
        return self._prompt_sistema + "\n\n" + "\n\n".join(partes)
