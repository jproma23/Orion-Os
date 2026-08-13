"""AI Manager (Cap 7 secao 2-3) - IA remota (OpenAI) com fallback local (Ollama).

Prompt de sistema fixo (config/prompt_sistema.txt, Cap 17) + contexto vindo
da memoria (Cap 11, via MemoryClient/comm.request). Dois backends atras da
mesma interface (EDR-0021): OpenAI remoto (provider="openai", padrao) cai
automaticamente para o Ollama local se a chamada remota falhar - sem
internet, erro de API, timeout - nunca deixa a conversa muda (Cap 6 s.8,
mesma tolerancia ja usada para Arduino/SSD/Notebook ausentes).

Chave da API (OPENAI_API_KEY) nunca fixa no codigo/config (regra 6 do
ARQUITETURA.txt) - vem de variavel de ambiente (EDR-0021).

O contexto entregue ao modelo passa pelo grounding
(orion/mission/grounding.py), que diz explicitamente o que o robo NAO
sabe - sem isso os modelos inventam observacoes (medido em 2026-07-19).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from orion.mission.grounding import montar_contexto

logger = logging.getLogger("orion.mission.ai_manager")


class AiManager:
    def __init__(
        self,
        modelo: str = "gemma3:1b",
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
        # Clientes nascem sob demanda (ver _responder_ollama/_responder_openai),
        # com os imports DENTRO dos metodos: a lib `ollama` so existe no
        # Notebook, e sem isso o mission_planner - que importa esta classe -
        # nao era importavel no Raspberry nem nos testes.
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
            "Voce e o Fofao, um robo cuidador do lar e da familia. Decida o "
            "proximo comportamento pensando no bem-estar e na seguranca de "
            "quem mora na casa. Escolha APENAS UMA destas acoes e responda so "
            f"com a palavra exata, sem mais nada: {', '.join(acoes_validas)}.\n\n"
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

        notas = contexto.get("notas_relevantes") or []
        if notas:
            resumo = "\n".join(f"- {n['titulo']}: {n['trecho']}" for n in notas[:5])
            partes.append(f"Notas relevantes da sua memoria de longo prazo:\n{resumo}")

        # `partes` ja comeca com self._prompt_sistema (acima) - concatenar de
        # novo aqui duplicaria o prompt inteiro no pedido ao modelo.
        return "\n\n".join(partes)
