# EDR-0020 — Behavior Core: o núcleo de comportamento autônomo do Fofão

**Status:** Aprovado
**Data:** 2026-07-19
**Complementa:** EDR-0018, EDR-0019
**Relaciona:** Cap 6 (Kernel), Cap 7 (Mission), Cap 12 (Navegação), Cap 18 (Segurança)

## Contexto

Até aqui, todos os módulos do ORION existem e conversam pelo Event Bus,
mas o robô só age **quando alguém manda** — um comando de voz, uma missão
publicada do Notebook. Falta a peça que faz um robô multitarefa parecer
"vivo": algo que decida, sozinho e o tempo todo, **o que fazer agora**
entre várias coisas possíveis (patrulhar, vigiar, atender quem chama,
descansar) e que **troque de tarefa sob prioridade** quando o ambiente
muda ("estou patrulhando, ouvi um barulho → paro, olho, depois retomo").

O usuário resumiu o objetivo: o robô "tem que saber fazer tudo sozinho",
sem depender de comandos externos o tempo todo — "como se fosse uma
consciência viva".

Avaliou-se adotar **ROS 2** para isso. Decidiu-se **não** migrar agora: o
que dá a um robô a capacidade multitarefa não é o ROS, e sim a **camada de
coordenação de comportamento**. O ORION já possui as demais camadas
(Event Bus, concorrência async, percepção, voz, IA, tempo real no Arduino).
ROS fica como avaliação futura, restrita à navegação com mapa/SLAM, e
dependeria de um EDR próprio.

## Decisão

Criar o **Behavior Core** (`src/orion/behavior/`), um módulo do **Mission
Core (Notebook)** — a camada estratégica (Cap 7, Cap 18). Ele é o "maestro":
um laço de **arbitragem por prioridade** (estilo *subsumption*) sobre o
Event Bus, que decide qual comportamento está no controle a cada momento.

### Princípios

1. **Não substitui nada; coordena.** O Behavior Core não fala com hardware
   direto — emite pelas interfaces que já existem (`navigation.comando` ao
   Motion Core, síntese de voz, etc.). Continua valendo: Notebook nunca
   fala direto com o Arduino (EDR-0018).
2. **Segurança nunca é arbitrada.** A camada reativa do Arduino (Cap 18
   camada 1) permanece independente e sempre vence. O Behavior Core opera
   nas camadas tática/estratégica, acima dela.
3. **Um comportamento = uma responsabilidade** (regra 9 do ARQUITETURA.txt).
   Cada comportamento é uma unidade plugável com: nome, prioridade,
   gatilho (que evento o desperta) e uma corrotina de execução
   preemptível.
4. **Preempção com retomada.** Um comportamento de prioridade maior
   **interrompe** o menor; ao terminar, o interrompido **retoma** de onde
   parou quando possível.
5. **Tudo configurável (Cap 17).** A escada de prioridades e os parâmetros
   de cada comportamento vêm do `orion.yaml`, não fixos no código.
6. **Degrada com o sistema (Cap 6 s.8).** Sem Motion Core, os
   comportamentos de movimento se desativam; os de percepção/voz seguem.

### Escada de prioridades (inicial, ajustável no `orion.yaml`)

| Prio | Comportamento | Gatilho | Ação |
|---|---|---|---|
| 100 | **Segurança tática** | obstáculo/inclinação (além do reflexo do Arduino) | parar, publicar alerta |
| 80 | **Atender** | ouviu "Fofão" / interação direta | pausar tudo, escutar e responder |
| 60 | **Alerta Sentinela** | barulho estranho / rosto desconhecido | virar para a origem, fotografar, registrar, notificar |
| 40 | **Patrulha agendada** | horário/intervalo de ronda | percorrer rota verificando a casa |
| 10 | **Repouso** | nada acontecendo | ficar atento, avatar em espera |

Comportamento de maior prioridade ativo preempta os menores; ao concluir,
o maestro reavalia e retoma o próximo mais prioritário que ainda queira
rodar.

### Onde roda

No **Raspberry Pi (Motion Core)** — `motion_core/behavior/`. Decisão
revisada em 2026-07-19 (antes: Notebook): o Pi é o nó **sempre ligado e
estável** (roda como serviço systemd; não caiu quando a sessão gráfica do
Notebook morreu no mesmo dia). Pondo o maestro no Pi, a "consciência"
**sobrevive a falhas do Notebook** e continua com os reflexos do Arduino.
Os eventos de visão/voz (que nascem no Notebook) já chegam ao Pi pelo Event
Bus/TCP; os comandos do maestro voltam pela mesma cadeia.

### Guardião de RAM do Notebook

O Notebook tem RAM apertada (gemma3 ~3,6 GB; já travou uma vez em teste de
IA). Um comportamento/monitor do maestro **vigia a RAM livre do Notebook**
(publicada como evento de saúde, Cap 16) e age **antes** do crash: alertar,
pedir para descarregar o modelo de IA ocioso, ou reduzir carga. Assim o Pi
protege o Notebook em vez de só reagir à queda.

## Consequências

- **Habilita o "Modo Sentinela"** pedido pelo usuário (vigia por som +
  foto de estranho + patrulha periódica) como comportamentos plugados no
  maestro, sem reescrever os módulos existentes.
- **Não requer ROS** nem rasga a arquitetura atual; aproveita as 6 de 8
  camadas já prontas e validadas (2026-07-19: cadeia completa ponta a
  ponta).
- Introduz um novo módulo (`src/orion/behavior/`) e uma nova seção
  `behavior:` no `orion.yaml`. O roadmap (Cap 20) ganha a Fase de
  Autonomia de Comportamento.
- ROS 2 permanece em aberto **apenas** para navegação com mapa/SLAM
  (LiDAR), a ser decidido em EDR próprio quando essa necessidade chegar.

**Fim do EDR-0020**
