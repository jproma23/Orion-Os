# O que é o ORION OS

Um robô que percebe o ambiente, decide sozinho o que fazer e conversa com
quem mora na casa. Tudo rodando **no hardware que está na bancada** — sem
nuvem, sem API paga, sem depender de internet para funcionar.

O primeiro robô da plataforma se chama **Fofão**.

## Por que offline

A decisão de não usar nuvem não é ideologia, é engenharia. Um robô que
depende de internet para desviar de um obstáculo tem um ponto único de
falha do lado de fora da casa. Se a conexão cair, ele para.

Então visão, voz e o modelo de linguagem rodam localmente. Custa mais
trabalho — o hardware é modesto, e cada recurso precisa caber em 4 GB de
RAM no Raspberry — mas o robô continua sendo robô com o roteador
desligado.

## A arquitetura: três computadores em corrente

Cada máquina faz o que sabe fazer melhor, e **só conversa com a vizinha**.
O Notebook nunca acessa o Arduino direto.

| Unidade | Máquina | Do que cuida |
|---|---|---|
| **Mission Core** | Notebook 8 GB | Reconhecimento facial, conversa por voz, o rosto do robô, planejamento |
| **Motion Core** | Raspberry Pi 4 + SSD | Kernel, decisão, navegação, memória, interface web |
| **Hardware Core** | Arduino Mega 2560 | Tempo real: motores, servos, ultrassom, IMU, parada de emergência |

Essa separação resolve um problema prático: tarefa de tempo real e tarefa
pesada não convivem bem na mesma máquina. O Arduino garante que o motor
para quando o ultrassom vê parede, em milissegundos, sem esperar decisão
de ninguém. O Raspberry decide para onde ir. O Notebook pensa devagar,
onde pensar devagar não faz mal.

## O que já funciona

- **Kernel próprio** — event bus assíncrono com fila de prioridades,
  service registry, watchdog que escalona reconexão → reinício → alerta
- **Behavior Core** — a "consciência": arbitra prioridade entre repouso,
  atender alguém, vigília e desvio de obstáculo
- **Protocolo binário** entre Pi e Arduino, com CRC, ACK, heartbeat e
  reconexão automática
- **Visão** — sabe quem é da casa, e dispara alerta para rosto desconhecido
- **Voz** — palavra de ativação, detecção de fala e resposta falada
- **Memória** — diário de observações em banco, com consulta determinística
  separada da conversa livre do modelo
- **Interface web** — mapa polar do radar, telemetria ao vivo, diagnóstico

## Como este diário funciona

Cada entrada foi escrita **no dia em que aconteceu**, não em resumo de fim
de projeto. Isso inclui os erros: o brownout que derrubava o Arduino, o
ultrassom que media certo mas relatava errado, o modelo de linguagem que
inventava resposta, o serviço que caía por causa de um HDMI ruim.

A regra existe por um motivo prático — o projeto é grande demais para
caber na memória entre uma sessão e outra. Mas o efeito colateral é que
ficou um registro honesto de como um sistema desses realmente nasce: não
em linha reta.

O código está todo em
[github.com/jproma23/Orion-Os](https://github.com/jproma23/Orion-Os).
