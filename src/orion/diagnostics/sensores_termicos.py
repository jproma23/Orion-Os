"""Leitura dos sensores de temperatura da propria maquina (Cap 16).

Le /sys/class/thermal, que existe tanto no Raspberry quanto no Notebook e
nao exige biblioteca nenhuma - e so arquivo de texto no sysfs.

O que ISTO mede e o que NAO mede:

  - MEDE a temperatura dos chips (CPU/SoC/chipset). E o que o Cap 16 pede
    em "Temperatura da CPU" e "temperatura do SoC", e serve para a regra
    de reduzir a frequencia do laco de navegacao quando o Raspberry
    esquenta demais.

  - NAO MEDE temperatura nem umidade AMBIENTE. Esses sensores estao dentro
    da carcaca e sobem junto com a carga da maquina. O item do Cap 16
    "Temperatura/umidade ambiente" continua dependendo do DHT no Arduino -
    os dois se complementam, um nao substitui o outro.

Valores tipicos medidos em 2026-07-19:
  Raspberry: cpu-thermal 66,7 C
  Notebook : x86_pkg_temp 50,0 C | pch_cannonlake 46,0 C | acpitz 27,8 C
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("orion.diagnostics.sensores_termicos")

RAIZ_THERMAL = Path("/sys/class/thermal")

# Tipos de zona que representam melhor "a temperatura do processador" em
# cada maquina, em ordem de preferencia. O primeiro que existir e usado
# como a leitura principal.
PREFERENCIA_CPU = ("cpu-thermal", "x86_pkg_temp", "coretemp", "acpitz")


@dataclass(frozen=True)
class LeituraTermica:
    """Uma zona termica lida do sysfs."""

    nome: str
    temperatura_c: float


@dataclass(frozen=True)
class EstadoTermico:
    """Retrato termico da maquina num instante."""

    zonas: tuple[LeituraTermica, ...]
    cpu_c: float | None  # zona principal, escolhida por PREFERENCIA_CPU
    nivel: str  # "ok" | "warning" | "critical" | "desconhecido"

    def como_dict(self) -> dict[str, object]:
        return {
            "cpu_c": self.cpu_c,
            "nivel": self.nivel,
            "zonas": {z.nome: z.temperatura_c for z in self.zonas},
        }


def ler_zonas(raiz: Path = RAIZ_THERMAL) -> tuple[LeituraTermica, ...]:
    """Le todas as zonas termicas disponiveis.

    Devolve tupla vazia se a maquina nao expuser /sys/class/thermal (nao e
    erro - alguns ambientes simplesmente nao tem).
    """
    if not raiz.is_dir():
        return ()

    leituras: list[LeituraTermica] = []
    for zona in sorted(raiz.glob("thermal_zone*")):
        try:
            nome = (zona / "type").read_text().strip()
            # O sysfs reporta em milesimos de grau Celsius.
            milesimos = int((zona / "temp").read_text().strip())
        except (OSError, ValueError) as erro:
            # Zona que sumiu ou veio ilegivel nao invalida as outras.
            logger.debug("zona termica %s ilegivel: %s", zona.name, erro)
            continue
        leituras.append(LeituraTermica(nome=nome, temperatura_c=milesimos / 1000.0))

    return tuple(leituras)


def _escolher_cpu(zonas: tuple[LeituraTermica, ...]) -> float | None:
    """Escolhe qual zona representa a CPU, na ordem de PREFERENCIA_CPU."""
    por_nome = {z.nome: z.temperatura_c for z in zonas}
    for preferida in PREFERENCIA_CPU:
        if preferida in por_nome:
            return por_nome[preferida]
    # Nenhuma conhecida: usa a mais quente, que quase sempre e o processador.
    return max((z.temperatura_c for z in zonas), default=None)


def classificar(
    cpu_c: float | None, limiar_warning_c: float, limiar_critical_c: float
) -> str:
    """Traduz a temperatura da CPU para o nivel do Cap 16."""
    if cpu_c is None:
        return "desconhecido"
    if cpu_c >= limiar_critical_c:
        return "critical"
    if cpu_c >= limiar_warning_c:
        return "warning"
    return "ok"


def ler_estado(
    limiar_warning_c: float = 80.0,
    limiar_critical_c: float = 90.0,
    raiz: Path = RAIZ_THERMAL,
) -> EstadoTermico:
    """Retrato termico completo, pronto para virar evento no Event Bus.

    Os limiares default espelham `diagnostics.thresholds` do orion.yaml
    (cpu_temp_warning_c / cpu_temp_critical_c); quem chama deve passar os
    valores da config em vez de confiar no default (regra #6 do ARQUITETURA.md).
    """
    zonas = ler_zonas(raiz)
    cpu_c = _escolher_cpu(zonas)
    return EstadoTermico(
        zonas=zonas,
        cpu_c=cpu_c,
        nivel=classificar(cpu_c, limiar_warning_c, limiar_critical_c),
    )
