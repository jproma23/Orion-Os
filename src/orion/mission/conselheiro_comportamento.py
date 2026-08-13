"""Conselheiro de comportamento (camada 3 da integração cognitiva).

A IA OPINA, AS REGRAS MANDAM
----------------------------
Medido em 2026-07-19 com o gemma3:1b e saída estruturada: das 4 situações
de teste, ele escolheu `repouso` em 3 - inclusive com obstáculo a 34 cm à
frente, e ignorou um chamado direto da Ana. O schema garantiu resposta
VÁLIDA (nenhum nome inventado), mas não resposta CERTA.

Por isso este módulo é um CONSELHEIRO e não um decisor:

  - Enquanto um comportamento de segurança quiser o controle (obstáculo,
    inclinação, impacto), nem se pergunta à IA. Segurança é determinística
    e não se negocia (Cap 18, camada tática).
  - A IA só é consultada quando as regras estão empatadas ou nenhuma tem
    opinião forte - ou seja, na ambiguidade real.
  - A resposta dela passa por validação. Comportamento fora da lista, ou
    fora do conjunto permitido naquele instante, é DESCARTADO e o maestro
    segue pela regra.

Assim, no pior caso (IA burra, lenta ou fora do ar) o robô se comporta
exatamente como se comportava sem ela.

Backend (2026-07-24): usa IA remota (OpenAI/OpenRouter, saída JSON
estruturada com enum - mesma garantia de "nunca inventa nome de
comportamento" que o Ollama local dava via `format=schema`) em vez de
Ollama local - decisão do usuário: carregar um modelo no Ollama local
trava o Notebook (mesmo achado do EDR-0021 para a IA de conversa). Chave
da API nunca fixa no código (regra 6 do ARQUITETURA.txt) - vem de
OPENAI_API_KEY.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("orion.mission.conselheiro")

# Comportamentos que a IA NUNCA pode escolher: são acionados por condição
# física medida, não por opinião. Deixar a IA "decidir" entrar ou sair de
# segurança seria pôr um palpite no caminho do freio.
COMPORTAMENTOS_DE_SEGURANCA = frozenset({"vigilancia_obstaculo"})

_PROMPT_SISTEMA = (
    "Você é o conselheiro de comportamento do robô Fofão, um robô cuidador "
    "do lar e da família. Sua única função é escolher, entre as opções "
    "dadas, o comportamento mais adequado ao momento e explicar o motivo "
    "em uma frase curta. Você nunca aciona hardware diretamente."
)


@dataclass(frozen=True)
class Conselho:
    comportamento: str
    motivo: str
    aceito: bool
    # Preenchido quando o conselho foi recusado - vira log e vai para a
    # interface, para dar para auditar por que a IA foi ignorada.
    recusa: str = ""


def _schema(opcoes: list[str]) -> dict:
    """response_format estruturado (OpenAI/OpenRouter) que torna impossível
    inventar nome de comportamento - o `enum` restringe a geração, mesma
    garantia que `format=schema` dava no Ollama local."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "conselho_comportamento",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "comportamento": {"type": "string", "enum": opcoes},
                    "motivo": {"type": "string"},
                },
                "required": ["comportamento", "motivo"],
                "additionalProperties": False,
            },
        },
    }


class ConselheiroComportamento:
    def __init__(
        self,
        modelo: str = "openai/gpt-4o-mini",
        temperatura: float = 0.3,
        timeout_s: float = 20.0,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._modelo = modelo
        # Temperatura baixa: aqui não se quer criatividade, quer-se
        # consistência. Criatividade fica na conversa.
        self._temperatura = temperatura
        self._timeout_s = timeout_s
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        # base_url alternativo (ex.: OpenRouter, "https://openrouter.ai/api/v1")
        # - a API e compativel com a da OpenAI, so muda o servidor (mesmo
        # padrao do AiManager, EDR-0021).
        self._base_url = base_url
        # Cliente criado sob demanda (import preguicoso do pacote `openai`)
        # dentro de _chamar(), nao aqui - mantem este modulo importavel em
        # qualquer lugar mesmo sem a lib instalada, so falha se de fato
        # tentar aconselhar sem ela.
        self._cliente = None

    async def aconselhar(
        self,
        contexto_texto: str,
        opcoes: list[str],
        seguranca_ativa: bool = False,
    ) -> Conselho | None:
        """Pede um conselho. Devolve None quando não se deve nem perguntar.

        `seguranca_ativa` = algum comportamento de segurança já quer o
        controle. Nesse caso não há o que aconselhar: a regra vence e a IA
        nem é chamada (economiza tempo e remove qualquer chance de a
        opinião dela atrasar uma parada).
        """
        if seguranca_ativa:
            logger.debug("segurança ativa - IA não é consultada")
            return None

        permitidas = [o for o in opcoes if o not in COMPORTAMENTOS_DE_SEGURANCA]
        if not permitidas:
            return None

        try:
            bruto = await asyncio.wait_for(
                asyncio.to_thread(self._chamar, contexto_texto, permitidas),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("IA demorou mais de %.1fs - seguindo pela regra", self._timeout_s)
            return None
        except Exception:
            logger.exception("IA indisponível - seguindo pela regra")
            return None

        return self._validar(bruto, permitidas)

    def _chamar(self, contexto_texto: str, opcoes: list[str]) -> str:
        from openai import OpenAI

        if self._cliente is None:
            self._cliente = OpenAI(api_key=self._api_key, base_url=self._base_url)

        resposta = self._cliente.chat.completions.create(
            model=self._modelo,
            messages=[
                {"role": "system", "content": _PROMPT_SISTEMA},
                {
                    "role": "user",
                    "content": (
                        f"{contexto_texto}\n\n"
                        f"Escolha o comportamento mais adequado agora e diga o motivo "
                        f"em uma frase curta."
                    ),
                },
            ],
            temperature=self._temperatura,
            max_tokens=150,
            response_format=_schema(opcoes),
        )
        return resposta.choices[0].message.content or ""

    @staticmethod
    def _validar(bruto: str, permitidas: list[str]) -> Conselho | None:
        """Nunca confia na saída: valida antes de deixar influenciar nada."""
        try:
            dados = json.loads(bruto)
        except (json.JSONDecodeError, TypeError):
            logger.warning("IA devolveu JSON inválido - descartado: %r", bruto[:80])
            return None

        if not isinstance(dados, dict):
            # JSON valido mas nao um objeto (ex.: a IA responde `null`/`[]`/
            # um numero) - o schema nao garante 100% um objeto em todo
            # modelo/versao. Sem essa checagem, dados.get(...) abaixo
            # quebrava com AttributeError sem tratamento - justamente no
            # modulo cujo proposito e nunca travar nada (achado real da
            # vistoria de codigo de 2026-07-24).
            logger.warning("IA devolveu JSON que nao e um objeto - descartado: %r", bruto[:80])
            return None

        comportamento = dados.get("comportamento")
        motivo = str(dados.get("motivo", "")).strip()

        if comportamento not in permitidas:
            # O schema deveria impedir isso; se acontecer, é bug do
            # servidor ou versão sem structured output - descarta e loga.
            logger.warning(
                "IA sugeriu comportamento não permitido %r - descartado", comportamento
            )
            return Conselho(
                comportamento="", motivo=motivo, aceito=False,
                recusa=f"comportamento invalido: {comportamento!r}",
            )

        return Conselho(comportamento=comportamento, motivo=motivo, aceito=True)
