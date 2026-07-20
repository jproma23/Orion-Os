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
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger("orion.mission.conselheiro")

# Comportamentos que a IA NUNCA pode escolher: são acionados por condição
# física medida, não por opinião. Deixar a IA "decidir" entrar ou sair de
# segurança seria pôr um palpite no caminho do freio.
COMPORTAMENTOS_DE_SEGURANCA = frozenset({"vigilancia_obstaculo"})


@dataclass(frozen=True)
class Conselho:
    comportamento: str
    motivo: str
    aceito: bool
    # Preenchido quando o conselho foi recusado - vira log e vai para a
    # interface, para dar para auditar por que a IA foi ignorada.
    recusa: str = ""


def _schema(opcoes: list[str]) -> dict:
    """Schema que torna impossível inventar nome de comportamento.

    O `enum` é a garantia: o Ollama restringe a geração à gramática, então
    não é o modelo "tentando acertar" - valor fora da lista não tem como
    ser produzido.
    """
    return {
        "type": "object",
        "properties": {
            "comportamento": {"type": "string", "enum": opcoes},
            "motivo": {"type": "string"},
        },
        "required": ["comportamento", "motivo"],
    }


class ConselheiroComportamento:
    def __init__(
        self,
        modelo: str = "gemma3:1b",
        temperatura: float = 0.3,
        timeout_s: float = 20.0,
    ) -> None:
        self._modelo = modelo
        # Temperatura baixa: aqui não se quer criatividade, quer-se
        # consistência. Criatividade fica na conversa.
        self._temperatura = temperatura
        # A decisão tem prazo, mas o prazo precisa caber na realidade: a
        # inferência local do gemma3:1b leva 10-20s nesta máquina. Menor que
        # isso e o conselho NUNCA fica pronto (medido ao vivo em
        # 2026-07-19, quando 8s abortava toda consulta). Quem espera é uma
        # tarefa lateral - o maestro segue decidindo pela regra enquanto isso.
        self._timeout_s = timeout_s
        # Import preguicoso: a lib `ollama` so existe no Notebook. Assim o
        # modulo continua importavel no Raspberry (onde roda o maestro) e
        # nos testes, sem exigir a dependencia.
        import ollama

        self._cliente = ollama.Client()

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
        resposta = self._cliente.generate(
            model=self._modelo,
            prompt=(
                f"{contexto_texto}\n\n"
                f"Escolha o comportamento mais adequado agora e diga o motivo "
                f"em uma frase curta."
            ),
            format=_schema(opcoes),
            options={"temperature": self._temperatura, "num_predict": 100},
        )
        return resposta.get("response", "")

    @staticmethod
    def _validar(bruto: str, permitidas: list[str]) -> Conselho | None:
        """Nunca confia na saída: valida antes de deixar influenciar nada."""
        try:
            dados = json.loads(bruto)
        except (json.JSONDecodeError, TypeError):
            logger.warning("IA devolveu JSON inválido - descartado: %r", bruto[:80])
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
