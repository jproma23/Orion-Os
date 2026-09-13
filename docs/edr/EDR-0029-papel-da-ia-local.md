# EDR-0029 — Papel da IA local de decisão

**Data:** 13/09/2026
**Status:** Decidido
**Supersede parcialmente:** a decisão anterior de que a API de nuvem seria o "cérebro" completo

---

## Contexto

A decisão anterior entregava visão, voz e cognição à API de nuvem. A cognição volta para
local, como ferramenta que aprende e navega por coordenadas. Resta definir em que escala
temporal cada camada opera.

A tentação natural é mandar o mapa (ou o scan) para a camada de decisão e receber de volta
comandos de movimento. Esse desenho colide.

## Conta que motiva a decisão

Round-trip de API de nuvem: 500 ms a 2 s.
A 0,5 m/s, isso é **25 cm a 1 m percorridos** entre "vi o obstáculo" e "recebi a ordem de
desviar". Colide.

Mesmo com a IA local, o ciclo de decisão cognitiva é de segundos, não de dezenas de
milissegundos. Um LiDAR que gira a 10–15 Hz já impõe um piso de 66–100 ms por volta.

Além disso: 400 pontos a 10 Hz enviados a uma API é insustentável em custo e banda.

## Decisão

**A IA de decisão emite objetivo, nunca velocidade.**

| Camada | O que decide | Escala |
|---|---|---|
| IA local de decisão | Para onde ir, o que investigar | segundos |
| Nav2 | Caminho e desvio | 20–50 ms |
| ESP32 de motor | Velocidade por roda (PID) | malha fechada |

A IA local recebe: o mapa (occupancy grid já construído), a pose atual, o status do Nav2 e
as estatísticas históricas de rota.

A IA local **não** recebe: o scan cru.

A IA local emite: um `NavigateToPose`. Quem resolve caminho e desvio é o Nav2.

**Reflexo e desvio nunca atravessam a nuvem, e nunca atravessam a IA de decisão.**

## Onde a nuvem continua

| Função | Onde | Justificativa |
|---|---|---|
| Desvio de obstáculo | Nav2, local | Segurança, latência |
| Planejamento de caminho | Nav2, local | Idem |
| Escolha de objetivo | IA local | Sobrevive à queda de internet |
| Visão (identificar objetos) | API | Sem urgência, frames sob demanda |
| Voz (ouvir/falar) | API + ESP32-S3 | Sem urgência |

## Consequências

- Sem internet, o Fofão continua navegando, patrulhando e desviando. Fica sem iniciativa,
  não sem reflexo. É isto que preserva o offline-first do projeto agora que parte da
  cognição saiu para a nuvem.
- A IA de decisão pode travar ou demorar sem colocar ninguém em risco.
- Perde-se a possibilidade de manobras que exijam raciocínio em tempo real. Aceito: não há
  caso de uso no escopo do TCC que precise disso.
