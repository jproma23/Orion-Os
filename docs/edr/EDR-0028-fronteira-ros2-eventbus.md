# EDR-0028 — Fronteira entre ROS2 e o EventBus

**Data:** 13/09/2026
**Status:** Decidido
**Contexto de origem:** migração do ORION OS para ROS2/Nav2 no Raspberry Pi

---

## Contexto

A consolidação no Raspberry Pi traz o Nav2 para dentro do robô. O ROS2 carrega o próprio
barramento (DDS), com ciclo de vida, TF e tópicos. O ORION OS já tem um barramento
estruturante — o EventBus assíncrono — cuja regra é que nenhum módulo importa outro
diretamente.

Passam a existir dois barramentos no mesmo processo físico. Sem uma fronteira explícita, o
estado do robô fragmenta e deixa de haver resposta para "quem manda no robô agora".

## Decisão

**O ROS2 é dono de tudo abaixo da linha de movimento e percepção geométrica:**

- `/cmd_vel`, `/odom`, `/scan`, `/map`
- Toda a árvore TF
- Costmaps, planners, controllers, recovery behaviors

**O EventBus é dono de tudo acima:**

- Comportamento e subsunção
- Modelo emocional de oito estados
- Voz, visão, missão, memória
- Persistência em SQLite

**Um único nó-ponte** liga os dois. Nenhum outro ponto de contato é permitido. A direção de
autoridade é única: o EventBus emite intenção, o ROS2 executa e reporta.

O que a ponte publica no EventBus: pose, status de navegação, resumo do scan.
O que a ponte assina no EventBus: comandos de missão.

**A ponte nunca repassa o scan cru** (400 pontos a 10 Hz) para o EventBus.

Como tudo roda num único Pi: **`ROS_LOCALHOST_ONLY=1`**. Isso elimina a descoberta DDS
em rede, que é a maior fonte de instabilidade do ROS2 em WiFi.

## Consequências

**Positivas**
- A regra do EventBus continua verdadeira e verificável: nenhum módulo de comportamento
  conhece o ROS2.
- Trocar o Nav2 por outra stack de navegação no futuro toca um arquivo.
- `ROS_LOCALHOST_ONLY=1` é um ganho direto da consolidação num Pi só.

**Negativas**
- O nó-ponte é um ponto único de falha para todo o movimento. Precisa de tratamento de erro
  desproporcional ao seu tamanho.
- Há custo de tradução: eventos viram tópicos e vice-versa.

**Violações a tratar como bug, não como estilo**
- Qualquer módulo de comportamento que importe `rclpy`.
- Qualquer nó ROS2 que publique diretamente no EventBus sem passar pela ponte.
- Missão longa executada inline em handler do EventBus em vez de `asyncio.Task`.
