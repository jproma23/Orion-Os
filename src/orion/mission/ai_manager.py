"""AI Manager (Cap 7 secao 2-3) - IA remota (OpenAI) com fallback local (Ollama).

Prompt de sistema fixo (config/prompt_sistema.txt, Cap 17) + contexto vindo
da memoria (Cap 11, via MemoryClient/comm.request). Dois backends atras da
mesma interface (EDR-0021): OpenAI remoto (provider="openai", padrao) cai
automaticamente para o Ollama local se a chamada remota falhar - sem
internet, erro de API, timeout - nunca deixa a conversa muda (Cap 6 s.8,
mesma tolerancia ja usada para Arduino/SSD/Notebook ausentes).

Chave da API (OPENAI_API_KEY) nunca fixa no codigo/config (regra 6 do
CLAUDE.md) - vem de variavel de ambiente (EDR-0021).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger("orion.mission.ai_manager")


class AiManager:
    def __init__(
        self,
        modelo: str = "llama3.2:3b",
        temperatura: float = 0.6,
        caminho_prompt_sistema: str | Path = "config/prompt_sistema.txt",
        max_tokens_resposta: int | None = None,
        keep_alive_minutes: int | None = None,
        provider: str = "ollama",
        openai_model: str = "gpt-4o-mini",
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
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
        self._provider = provider
        self._openai_model = openai_model
        self._openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        # base_url alternativo (ex.: OpenRouter, "https://openrouter.ai/api/v1")
        # - a API e compativel com a da OpenAI, so muda o servidor (EDR-0021).
        self._openai_base_url = openai_base_url
        self._cliente_ollama = None
        self._cliente_openai = None

    async def responder(self, texto_usuario: str, contexto: dict | None = None) -> str:
        prompt_sistema = self._montar_prompt_sistema(contexto)

        if self._provider == "openai" and self._openai_api_key:
            try:
                return await asyncio.to_thread(self._chamar_openai, prompt_sistema, texto_usuario)
            except Exception:
                logger.warning(
                    "OpenAI indisponivel (sem internet ou erro de API) - "
                    "caindo para o Ollama local",
                    exc_info=True,
                )

        return await asyncio.to_thread(self._chamar_ollama, prompt_sistema, texto_usuario)

    def _chamar_ollama(self, prompt_sistema: str, texto_usuario: str) -> str:
        import ollama

        if self._cliente_ollama is None:
            self._cliente_ollama = ollama.Client()

        opcoes: dict = {"temperature": self._temperatura}
        if self._max_tokens_resposta:
            opcoes["num_predict"] = self._max_tokens_resposta

        resposta = self._cliente_ollama.chat(
            model=self._modelo,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto_usuario},
            ],
            options=opcoes,
            keep_alive=self._keep_alive,
        )
        return resposta["message"]["content"]

    def _chamar_openai(self, prompt_sistema: str, texto_usuario: str) -> str:
        from openai import OpenAI

        if self._cliente_openai is None:
            self._cliente_openai = OpenAI(
                api_key=self._openai_api_key, base_url=self._openai_base_url
            )

        resposta = self._cliente_openai.chat.completions.create(
            model=self._openai_model,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto_usuario},
            ],
            temperature=self._temperatura,
            max_tokens=self._max_tokens_resposta,
        )
        return resposta.choices[0].message.content

    async def decidir(self, estado: dict, acoes_validas: list[str]) -> str | None:
        """Pede uma decisao de acao ao maestro a partir de um vocabulario
        FECHADO (EDR-0022) - a IA nunca aciona hardware direto, so escolhe
        entre opcoes que o robo ja sabe executar com seguranca. Retorna
        None se a resposta vier fora do vocabulario ou a chamada falhar
        (Cap 6 s.8 - quem chama trata None como "sem decisao agora")."""
        prompt = (
            "Voce esta decidindo o proximo comportamento de um robo domestico "
            "chamado Fofao. Escolha APENAS UMA destas acoes e responda so com "
            f"a palavra exata, sem mais nada: {', '.join(acoes_validas)}.\n\n"
            f"Estado atual do robo: {estado}"
        )
        try:
            resposta = await self.responder(prompt, contexto=None)
        except Exception:
            logger.warning("Falha ao consultar IA para decisao estrategica", exc_info=True)
            return None

        resposta_normalizada = resposta.strip().lower()
        for acao in acoes_validas:
            if acao in resposta_normalizada:
                return acao
        logger.warning("IA sugeriu acao fora do vocabulario: %r", resposta)
        return None

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

        notas = contexto.get("notas_relevantes") or []
        if notas:
            resumo = "\n".join(f"- {n['titulo']}: {n['trecho']}" for n in notas[:5])
            partes.append(f"Notas relevantes da sua memoria de longo prazo:\n{resumo}")

        if not partes:
            return self._prompt_sistema
        return self._prompt_sistema + "\n\n" + "\n\n".join(partes)
