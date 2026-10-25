# O que é o ORION OS

Um robô que percebe o ambiente, decide sozinho o que fazer e conversa com
quem mora na casa. Tudo o que mantém o robô seguro roda **no hardware que
está na bancada**.

O primeiro robô da plataforma se chama **Fofão**.

## Offline-first, e onde a regra para

Um robô que depende de internet para desviar de um obstáculo tem um ponto
único de falha do lado de fora da casa. Se a conexão cair, ele para. Por
isso **visão, escuta, navegação, memória e segurança rodam localmente**, e
o robô continua sendo robô com o roteador desligado.

A conversa é a exceção, e assumida. O modelo de linguagem local (Ollama, no
Notebook) travava com frequência em uso real, e uma máquina capaz de rodar um
modelo bom sai cara. Por isso, desde julho, a IA da conversa roda por API (o
Gemini). Sem internet, o Fofão fica seguro, reconhece quem é
da casa e guarda o que viu, mas conversa pouco.

## A arquitetura: duas máquinas e três ESP32

Cada máquina faz o que sabe fazer melhor. Essa divisão mudou duas vezes ao
longo do projeto: em setembro, o Notebook chegou a ser aposentado e tudo
migrou para o Raspberry, que não aguentou o peso. A divisão de hoje é de
30/09/2026.

| Unidade | Máquina | Do que cuida |
|---|---|---|
| **Visão e voz** | Notebook 8 GB | Reconhecimento facial, YOLO, palavra de ativação, transcrição, voz falada. Só processa e não guarda nada |
| **Mission + Motion Core** | Raspberry Pi 4 + SSD | Memória, decisões, navegação, LiDAR, interface web |
| **Corpo** | ESP32 | Tempo real: motores, servos, bússola, parada de emergência |
| **Ouvidos** | ESP32 com 2 microfones | Manda o som direto para o Notebook |
| **Rosto** | ESP32-S3 com tela de 3,5" | Olhos, boca que mexe com a fala, toque |

Essa separação resolve um problema prático: tarefa de tempo real e tarefa
pesada não convivem bem na mesma máquina. O corpo garante que o motor para
em milissegundos, sem esperar decisão de ninguém. O Raspberry decide para
onde ir e lembra de tudo. O Notebook pensa devagar, onde pensar devagar não
faz mal, e **se ele sumir da rede, o robô fica parado, seguro e avisando**.

## O que já funciona

- **Kernel próprio** — event bus assíncrono com fila de prioridades,
  service registry, watchdog que escalona reconexão → reinício → alerta
- **Behavior Core** — a "consciência": arbitra prioridade entre repouso,
  atender alguém, vigília e desvio de obstáculo
- **Protocolo** entre o Pi e o corpo, com CRC, ACK, heartbeat e
  reconexão automática
- **Visão** — sabe quem é da casa, e dispara alerta para rosto desconhecido
- **Voz** — palavra de ativação, detecção de fala e resposta falada
- **Memória** — diário de observações em banco, com consulta determinística
  separada da conversa livre do modelo
- **LiDAR** — 360 graus em volta do robô, no lugar dos antigos ultrassons
- **Interface web** — mapa do LiDAR, telemetria ao vivo, diagnóstico

## Como este diário funciona

Cada entrada foi escrita **no dia em que aconteceu**, não em resumo de fim
de projeto. Isso inclui os erros: o brownout que derrubava o Arduino (que
acabou morrendo e virou um ESP32), o ultrassom que media certo mas relatava
errado, o modelo de linguagem que inventava resposta, o serviço que caía por
causa de um HDMI ruim.

A regra existe por um motivo prático — o projeto é grande demais para
caber na memória entre uma sessão e outra. Mas o efeito colateral é que
ficou um registro honesto de como um sistema desses realmente nasce: não
em linha reta.

O código está todo em
[github.com/jproma23/Orion-Os](https://github.com/jproma23/Orion-Os).
