# EDR-0026 — As atribuições do Fofão: o que ele entrega

**Status:** Proposto (2026-07-28) — precisa da confirmação do usuário
**Data:** 2026-07-28
**Complementa:** EDR-0020 (Behavior Core), EDR-0022 (IA estratégica no maestro)
**Relaciona:** Cap 12 (Navegação), Cap 13 (Interface), Cap 18 (Segurança)

## Contexto

Em 2026-07-28 foi construído um **seletor manual de modo de navegação** no
dashboard, partindo da leitura de que o robô "ficava preso em MANUAL porque
ninguém nunca pedia outro modo". O usuário rejeitou a premissa inteira, com
estas palavras:

> "não tem esses modos, o sistema é autônomo, tem que seguir o maestro e
> cumprir as missões, junto com o mentor"

E completou dizendo o que quer ver ligado: o mentor com acesso à **odometria e
a tudo que o robô pode fazer**, **segurança do lar por divisões**,
**companheiro**, e "tudo que o Fofão pode entregar".

O seletor foi revertido. Este documento existe porque a rejeição expôs um
buraco mais fundo: **nunca foi escrito o que o Fofão deve entregar.** Sem
isso, cada sessão inventa um alvo diferente, e é exatamente assim que se
constrói um seletor manual num robô que deveria ser autônomo.

O objetivo aqui não é adicionar funcionalidade. É **fixar o alvo**, para que
o vocabulário do maestro e do mentor seja derivado dele, e não do que for
fácil de implementar na semana.

## Inventário: o que o Fofão JÁ pode fazer

Conferido no código em 2026-07-28, não copiado de documento anterior.

### Corpo (Arduino Mega — `firmware/hardware_core/`)

Vocabulário completo do firmware, 21 comandos:

| Grupo | Comandos |
|---|---|
| Rodas | `MOVE_FORWARD` `MOVE_CONTINUOUS` `MOVE_DISTANCE` `TURN_LEFT` `TURN_RIGHT` `STOP` |
| Cabeça | `SET_PAN_TILT` `PAN_TILT_SOLTAR` |
| Radar | `SCAN_FRONT` `RADAR_SET_ANGLE` |
| Casa | `LIGHT_ON` `LIGHT_OFF` `FAN_ON` `FAN_OFF` |
| Manutenção | `DOCK` `CALIBRATE_IMU` `CALIBRATE_COMPASS` `SET_IMPACT_THRESHOLD` |
| Protocolo | `WHO_ARE_YOU` `RETURN_STATUS` `COMMAND` |

Nota: **`DOCK` existe no firmware e nunca foi usado por ninguém.** Se há
intenção de recarga autônoma, ela começa aqui.

### Sentidos

- Ultrassom **frontal e traseiro** (o traseiro sem segurança reativa — Cap 18).
- IMU MPU6050: inclinação, aceleração, detecção de impacto.
- Bússola QMC6310: rumo e intensidade de campo.
- Temperatura e umidade (DHT) — **chegando `null` na telemetria hoje**.
- Câmera DV20 no pan/tilt: detecção de pessoa (YOLO), reconhecimento facial da
  família, rastreamento, gestos, sentinela visual (rosto desconhecido).
- Microfone: wake word, VAD, transcrição, síntese de voz.

### Memória

Banco no SSD do Pi: pessoas cadastradas (João Paulo, Marah, Kamal), histórico
de conversas, diário de observações, vault de notas.

### O maestro hoje (`motion_core/behavior/comportamentos.py`)

| Prioridade | Comportamento | Move rodas? |
|---|---|---|
| 100 | `VigilanciaObstaculo` | para |
| 95 | `ControleManual` | sim (você dirigindo) |
| 80 | `Atender` | não |
| 60 | `Vigilia` (sentinela) | não |
| 45 | `AcompanharPessoa` | não (só a cabeça) |
| 40 | `IaEstrategica` | **sim — 30 cm em linha reta** |
| 35 | `Mentor` | não |
| 20 | `Ronda` | não (só olha) |
| 10 | `Repouso` | não |

## O buraco entre o que ele é e o que deveria entregar

Quatro fatos, todos verificados:

1. **O mentor não move nada.** `acoes_validas: ["descansar",
   "observar_ambiente"]`. Ele aconselha sobre duas coisas que não têm
   consequência física.
2. **A IA estratégica tem um único verbo de movimento:** `avancar`, 30 cm
   retos, mandado direto ao Arduino — sem passar pela navegação.
3. **O maestro nunca usa a navegação para dirigir.** Publica só `SCAN_FRONT`
   e `HOLD`. `GOTO`, `PATROL`, `FOLLOW` e `EXPLORE` existem, foram testados, e
   **nenhum comportamento autônomo os alcança**.
4. **O robô não sabe onde está.** Sem encoders; a posição em `/estado` é
   estimativa por dead reckoning. Não existe nenhuma noção de cômodo.

Resumindo: **o Fofão pensa sozinho, mas o pensamento não chega nas rodas.** A
autonomia hoje termina no pescoço.

## Proposta: as cinco atribuições

Ordem de importância, não de implementação.

### 1. Guardião do lar

Vigiar a casa por **divisões** (cômodos nomeados), não como um espaço só.

- Rondar as divisões numa cadência que o mentor decide (não por horário fixo).
- Reagir a rosto desconhecido e a barulho estranho — o gatilho visual já
  existe (`sentinela_visao.py` publica `sentinela.alerta`); **o de som não**.
- Registrar o que viu, com foto e divisão de origem.
- **Nunca** é arbitrado por IA: a segurança reativa do Arduino continua acima
  de tudo (regra 2 do EDR-0020).

### 2. Companheiro

Estar junto, não só responder quando chamado.

- Reconhecer quem chegou e cumprimentar por nome (já reconhece).
- Acompanhar a pessoa pela casa — hoje só com a cabeça; deveria ser com as
  rodas, que é o `FOLLOW` que ninguém alcança.
- Puxar assunto a partir do que observou, não só reagir a pergunta. O diário
  de observações já guarda a matéria-prima.

### 3. Zelador

Cuidar da casa e de si mesmo.

- Luz e ventilador já são comandos (`LIGHT_ON`, `FAN_ON`) e nunca foram usados
  por decisão autônoma.
- Avisar sobre temperatura e umidade — **precisa consertar o DHT primeiro**,
  que hoje chega `null`.
- Voltar para a base quando a bateria cair (`DOCK` existe, nunca foi usado).

### 4. Observador

Saber o que é normal na casa, para poder estranhar o anormal.

- É o que dá conteúdo para o mentor aconselhar com base em algo.
- Depende de posição confiável: "estranho ter movimento aqui a esta hora" exige
  saber o que é "aqui".

### 5. Aprendiz

Melhorar sozinho — a ideia de auto-atualização que o usuário já levantou.

Fica explicitamente **fora de escopo** até as quatro acima estarem de pé. Está
listada para não ser esquecida, não para ser feita agora.

## Decisões já tomadas pelo usuário (2026-07-28)

### "Descansar" sai do diretório do maestro

> "não deve existir descansar no diretório do maestro"

Hoje `descansar` está no `acoes_validas` da IA estratégica **e** do mentor, e
no código vira `acao = None` — ou seja, é a forma de a IA **se abster** de
disputar o controle, deixando `Repouso` (prio 10) assumir.

A objeção é justa: descansar não é uma entrega. Um robô cuidador que tem
"não fazer nada" no seu catálogo de decisões está oferecendo à IA uma saída
fácil no lugar de trabalho útil.

**A abstenção não precisa da palavra.** `AiManager.decidir()` já devolve
`None` quando a resposta cai fora do vocabulário ou a chamada falha, e quem
chama já trata `None` como "sem decisão agora" (Cap 6 s.8). O mecanismo existe
sozinho.

Então: `descansar` sai da lista de ações. Para a IA não ficar **obrigada** a
mandar o robô fazer algo a cada ciclo — o que o deixaria em atividade
permanente, gastando bateria e chamadas de API —, o prompt passa a admitir
explicitamente uma resposta de "nada relevante agora", que **não é um
comportamento** e nunca aparece no diretório: cai em `None` e a escada de
prioridades resolve sozinha, com `Ronda` (20) ou `Repouso` (10) assumindo.

Distinção que vale reter: **`Repouso` continua existindo como piso do
sistema** — é o que roda quando ninguém mais quer o controle. O que deixa de
existir é `descansar` como *escolha* oferecida à IA.

### A vigília precisa de comandos reais e úteis

> "deve existir comandos reais úteis de vigília"

Hoje `Vigilia` (prio 60) dispara com `sentinela.alerta`, publica
`sentinela.capturar_foto`, notifica e **segura o controle sem fazer mais
nada** por `duracao_alerta_s`. Ela ocupa a terceira maior prioridade do robô
para tirar uma foto e esperar.

Vocabulário proposto para a vigília, todo apoiado em capacidade que **já
existe** no firmware ou na navegação:

| Comando | Apoia-se em | Existe? |
|---|---|---|
| `varrer_area` | `SCAN_FRONT` + pan/tilt | sim (é o que a `Ronda` faz) |
| `investigar` | `GOTO` na direção do alerta | navegação sim, direção não |
| `fotografar` | `sentinela.capturar_foto` | sim |
| `acender_luz` / `apagar_luz` | `LIGHT_ON` / `LIGHT_OFF` | sim, nunca usado autonomamente |
| `seguir_pessoa` | `FOLLOW` | sim, inalcançável pelo maestro |
| `aproximar_de_pessoa` | `GOTO` + visão | sim |
| `patrulhar` | `PATROL` | sim, inalcançável pelo maestro |
| `voltar_para_base` | `DOCK` | comando existe, nunca foi usado |
| `verificar_divisao(x)` | navegação + mapa de divisões | **depende do passo 3** |

Só o último depende de coisa que não existe. Os outros oito são ligações entre
peças prontas que nunca foram conectadas — que é exatamente o passo 2 da ordem
combinada.

## Consequências para o vocabulário do maestro e do mentor

Se as atribuições acima forem aceitas, seguem-se três mudanças:

1. **O mentor precisa de verbos com consequência.** Continuar com um
   vocabulário fechado (EDR-0022 — a IA nunca emite texto livre virando
   comando), mas o vocabulário passa a incluir navegar, ir a uma divisão,
   acompanhar, verificar. Ampliar a lista, não abrir a porteira.
2. **O maestro dirige pela navegação, não por atalho.** O `avancar` de 30 cm
   direto ao Arduino é um atalho que ignora a camada tática (checagem de
   obstáculo, retentativa por segmento). Movimento autônomo deve passar por
   `NavigationCore`, como o controle manual já passa.
3. **"Divisão" precisa existir como conceito** antes de qualquer ronda que
   valha o nome — e isso depende de posição confiável, que é o passo 3 do
   plano combinado com o usuário.

## Ordem combinada (2026-07-28)

1. **Este documento** — fixar o que ele entrega.
2. **Ligar o cérebro às rodas** — dar ao maestro e ao mentor o vocabulário de
   navegação que já existe e funciona.
3. **Odometria e posição confiável** — a base de "divisões do lar".

## Pontos que dependem da decisão do usuário

Marcados por honestidade: foram **derivados** das frases dele, não ditos por
ele com todas as letras.

- As cinco atribuições e seus nomes.
- Se a `Vigilia` deve **andar** até a origem do alerta ou continuar investigando
  parada. Andar de noite, numa casa com gente dormindo e sem sensor traseiro
  na segurança reativa, é uma decisão de risco que não tomo sozinho.
- Se "missão" é tarefa falada ("vai na cozinha"), rotina agendada, ou algo que
  o mentor propõe sozinho — os três levam a implementações diferentes.
- Se as divisões são cadastradas por você ou descobertas pelo robô.
- Se o mentor **manda** ou **aconselha** o maestro. Hoje aconselha (prio 35,
  abaixo da IA estratégica em 40).
