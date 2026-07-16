# ORION OS — System Engineering Specification (SES)
## Capítulo 13 — Display Core
Versão 1.1 — alinhada ao EDR-0019

## 1. Objetivo
O Display Core é o subsistema de interface do ORION OS. Ele é dividido em duas partes: o **Avatar**, exibido na tela do notebook embarcado (o "rosto" do ORION X), e a **Interface Web**, servida pelo Raspberry Pi e acessível de qualquer dispositivo (celular, notebook, TV) pela rede local.

## 2. Princípios
- A interface reflete o estado real do sistema, alimentada exclusivamente por eventos do Event Bus.
- Nenhuma lógica de decisão reside no Display Core.
- Offline-first: na rede local, a interface web é acessada diretamente pelo IP do Raspberry, sem depender de Internet.
- Acesso remoto externo (fora de casa) é opcional, via Raspberry Pi Connect, e exige Internet — nunca é requisito para operação.

## 3. Avatar (tela do notebook)
O avatar comunica o estado emocional-operacional do robô, em tela cheia com inicialização automática:
- IDLE — expressão neutra com animação sutil.
- LISTENING — indicação visual de escuta ativa.
- THINKING — animação de processamento.
- SPEAKING — sincronização básica com o áudio do Piper.
- ALERT — expressão de atenção (obstáculo, erro).
- SLEEP — tela escurecida em inatividade prolongada.
A reprodução multimídia (vídeos, músicas) também ocorre na tela e nos alto-falantes do notebook; em evento crítico é pausada automaticamente e o modo ALERT assume.

## 4. Interface Web (servida pelo Raspberry)
Servidor web leve no Raspberry, ao lado do banco de dados — consultas de histórico e telemetria são locais ao SSD. Modos (páginas):
- DASHBOARD — visão geral do sistema.
- CONVERSA — transcrição da interação por voz.
- MAPA — radar polar (0°–180°), posição e orientação estimadas, obstáculos e rota planejada.
- DIAGNÓSTICO — saúde dos módulos, heartbeats, últimos erros, acesso ao log (somente leitura).
- CONFIGURAÇÃO — parâmetros do sistema (acesso restrito).
Vários dispositivos podem acessar simultaneamente.

## 5. Dashboard
Painéis mínimos:
- Estado geral do sistema e modo atual.
- Bateria/energia (quando disponível).
- Telemetria do Hardware Core (distâncias, temperatura, umidade, IMU).
- Última detecção do Vision Core.
- Missão em execução e progresso.
- Últimos eventos do Event Bus.

## 6. Acesso Remoto (opcional, com Internet)
- Raspberry Pi Connect para acesso externo à interface web e manutenção.
- Notificações ao celular em eventos críticos (quando houver Internet).
- Nenhuma função essencial pode depender destes recursos (Cap 1, princípio offline-first).

## 7. Eventos Consumidos e Publicados
Consome: system.*, motion.*, vision.*, voice.*, navigation.*, diagnostic.*.
Publica:
display.mode_changed
display.user_input
display.media_started
display.media_finished

## 8. Requisitos
- Inicialização automática com o ORION OS (avatar no notebook, servidor web no Raspberry).
- Consumo de CPU limitado para não competir com a IA (notebook) nem com a navegação (Raspberry).
- Atualização da interface em até 500 ms após o evento.
- Operação contínua sem intervenção.

## 9. EDR-0010 (atualizado por EDR-0019)
Decisão: interface como consumidor puro do Event Bus, dividida em avatar (notebook) e interface web (Raspberry).
Motivação:
- Interface acessível de qualquer dispositivo da rede local, sem monitor dedicado no Raspberry.
- Dashboard ao lado do banco de dados: consultas de histórico locais ao SSD.
- Estado sempre consistente com o sistema real; núcleo inalterado se a interface for substituída.

## Conclusão
O Display Core humaniza o ORION X (avatar) e oferece transparência operacional total de qualquer dispositivo (interface web), permanecendo desacoplado da inteligência do sistema conforme os princípios do ORION OS.
