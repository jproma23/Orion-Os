"""AI Manager (Cap 7 secao 2-3) - integra o Ollama.

Prompt de sistema fixo (config/prompt_sistema.txt, Cap 17) + contexto vindo
da memoria (Cap 11, via MemoryClient/comm.request). Usa o cliente HTTP
oficial da lib `ollama` contra o servidor local ja instalado nesta
montagem (Cap 17: ai.ollama_model).

O contexto entregue ao modelo passa pelo grounding
(orion/mission/grounding.py), que diz explicitamente o que o robo NAO
sabe - sem isso os modelos inventam observacoes (medido em 2026-07-19).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import ollama

from orion.mission.grounding import montar_contexto


class AiManager:
    def __init__(
        self,
        modelo: str = "gemma3:1b",
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

    async def descarregar(self) -> None:
        """Tira o modelo da RAM do Ollama (keep_alive=0).

        E o alivio de memoria mais direto que o Notebook tem: o gemma3:1b
        ocupa ~880MB parado por causa do keep_alive de 30min. Nao quebra
        nada - a proxima pergunta recarrega o modelo sozinha, pagando so o
        tempo de carga uma vez.
        """

        def _chamar() -> None:
            self._cliente.generate(model=self._modelo, prompt="", keep_alive=0)

        await asyncio.to_thread(_chamar)

    def _montar_prompt_sistema(self, contexto: dict | None) -> str:
        """Prompt fixo + bloco de fatos do grounding.

        O bloco entra SEMPRE, mesmo sem contexto nenhum: um contexto vazio
        vira "não tenho registro de nada hoje", que e justamente a
        informacao que impede a IA de inventar. Omitir o bloco quando nao ha
        dados seria repetir o erro medido em 2026-07-19, quando o silencio
        sobre um fato levou os modelos a afirmarem coisas que nunca viram.
        """
        contexto = contexto or {}

        pessoa = contexto.get("pessoa") or {}
        familia = contexto.get("familia")
        # Quem esta falando conosco tambem e alguem que conhecemos.
        if pessoa.get("nome") and not familia:
            familia = [pessoa["nome"]]

        bloco = montar_contexto(
            retrato=contexto.get("retrato"),
            familia=familia,
            observacoes=contexto.get("observacoes"),
            conversas_recentes=contexto.get("conversas_recentes"),
        )

        partes = [self._prompt_sistema]
        if pessoa.get("nome"):
            partes.append(f"Voce esta falando com {pessoa['nome']}.")
        partes.append(bloco)

        conhecimento = contexto.get("conhecimento_relevante") or []
        if conhecimento:
            fatos = "\n".join(f"- {c['chave']}: {c['valor']}" for c in conhecimento[:5])
            partes.append(f"FATOS QUE EU JA SEI:\n{fatos}")

        return "\n\n".join(partes)
