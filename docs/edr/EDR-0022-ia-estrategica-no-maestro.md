# EDR-0022 — IA estratégica no Behavior Core (o maestro)

**Status:** Aprovado (2026-07-23)
**Data:** 2026-07-23
**Complementa:** EDR-0020 (Behavior Core), EDR-0021 (IA remota + memória híbrida)
**Relaciona:** Cap 7 (Mission), Cap 18 (Segurança), Cap 12 (Navegação)

## Contexto

O usuário quer a IA (OpenAI, EDR-0021) com influência real sobre as
decisões autônomas do robô, não só respondendo perguntas em conversa. Nas
palavras dele: **"o maestro é a alma, a IA é o espírito"** — o maestro
(`BehaviorCore`, EDR-0020) continua sendo o mecanismo sempre-ligado, rápido
e confiável que faz o robô agir; a IA entra como o "espírito" que informa
**o que** o maestro deveria priorizar fazer nos momentos em que a decisão
não é óbvia por regra fixa (ex.: "devo continuar patrulhando ou ir
cumprimentar essa pessoa?").

Hoje a escada de prioridades do EDR-0020 tem uma posição **livre e nunca
implementada**: "Patrulha agendada" (prioridade 40) — só existem
`VigilanciaObstaculo` (100), `Atender` (80), `Vigilia` (60) e `Repouso`
(10). Essa vaga é o encaixe natural para a IA estratégica, no lugar de uma
patrulha puramente por horário.

### Restrições que não podem ser quebradas

1. **Segurança nunca é arbitrada** (regra 2 do EDR-0020; Cap 18). A camada
   reativa do Arduino continua 100% independente da IA — nunca espera,
   nunca consulta, nunca pode ser \"convencida\" a ignorar um obstáculo.
   `VigilanciaObstaculo` (prio 100) continua acima de tudo.
2. **O maestro roda a cada 200ms** (`INTERVALO_TICK_S` em
   `behavior_core.py`). Uma chamada de API (OpenAI) leva de centenas de ms
   a alguns segundos — **não pode** entrar no laço de arbitragem em si,
   só decidir em ritmo próprio, mais lento, em background.
3. **Degrada com o sistema** (Cap 6 s.8) — sem internet/API, o robô
   continua funcionando com os comportamentos existentes, só sem a
   iniciativa estratégica extra.

## Decisão

### Novo comportamento plugável: `IaEstrategica`

- Mais um `Comportamento` normal (mesma classe base, `motion_core/behavior/`),
  registrado no maestro como qualquer outro — **não reescreve** o
  `BehaviorCore` nem a arbitragem por prioridade existente.
- **Prioridade 40** (a vaga livre da "Patrulha agendada" original) —
  configurável em `orion.yaml` (`behavior.ia_estrategica.prioridade`).
  Fica abaixo de `VigilanciaObstaculo` (100), `Atender` (80) e `Vigilia`
  (60): qualquer um desses sempre preempta a IA sem ela poder fazer nada
  a respeito — exatamente a garantia de segurança do contexto acima.
- **Consulta a IA em ritmo próprio**, numa tarefa de fundo separada do
  tick do maestro (ex.: a cada `intervalo_s` configurável, tipo 30-60s,
  ou quando nenhum comportamento de prioridade mais alta esteve ativo por
  um tempo) — nunca dentro do laço de 200ms.
- **Vocabulário fechado de ações**, nunca texto livre virando comando
  direto: a IA recebe o estado atual (o que o maestro está fazendo, hora
  do dia, eventos recentes, notas relevantes do vault - EDR-0021) e
  responde escolhendo entre uma lista fixa e validada, ex.:
  `descansar`, `patrulhar`, `observar_ambiente`, `aproximar_de(pessoa_id)`.
  Qualquer resposta fora dessa lista é descartada (loga um aviso) - a IA
  nunca aciona hardware diretamente, só escolhe entre opções que o robô já
  sabe executar com segurança.
- Cada ação da lista mapeia para comandos que já existem
  (`navigation.comando`, síntese de voz, etc.) — nenhum novo comando de
  hardware é criado só para isso.
- **Falha/timeout da IA** (sem internet, erro, resposta fora do
  vocabulário): `quer_rodar()` simplesmente retorna `False` nesse ciclo —
  o maestro escolhe o próximo da escada (`Repouso`, hoje) normalmente.
  Nunca trava esperando a IA responder.

### Onde roda

No Raspberry (Motion Core), junto dos outros comportamentos — mas a
chamada de API em si roda no `AiManager` (Notebook, Mission Core, EDR-0021)
como já é hoje. O comportamento no Pi manda o pedido de decisão via
`comm.request` ao Notebook (mesmo canal que já existe para `memory.*`) e
recebe a ação escolhida de volta — não duplica a integração com a OpenAI
no lado do Pi.

## Consequências

- Adiciona latência de rede como parte **opcional e de baixa frequência**
  da decisão estratégica — nunca do loop de segurança/tático.
- Novo custo recorrente pequeno (chamadas esporádicas, não por tick).
- Preenche a vaga de "Patrulha agendada" do EDR-0020 com algo mais rico
  (decisão informada) em vez de só horário fixo.
- Precisa definir, na implementação: o payload exato do "pedido de
  decisão" (comando novo `mission.decidir` ou reaproveitar `memory.*`?) e
  a lista fechada de ações inicial (curta para começar, cresce com uso).
- Não muda a escada de segurança do EDR-0020 nem o `BehaviorCore` em si —
  só adiciona um participante novo, prioridade 40.

**Fim do EDR-0022 (proposto)**
