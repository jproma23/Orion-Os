# ORION OS — System Engineering Specification (SES)

## Capítulo 9 — Voice Core

Versão 1.0 — Especificação Oficial

## 1. Objetivo

O Voice Core é responsável por toda a interação por voz do ORION OS. Ele captura áudio, detecta a palavra de ativação, converte fala em texto, encaminha ao Mission Core e reproduz respostas sintetizadas.

## 2. Hardware

Entradas:
• Microfone interno do notebook (principal).
• Microfone da webcam USB conectada ao Notebook (secundário).
Saídas:
• Alto-falantes do notebook (padrão).

## 3. Pipeline de Voz

1. Captura simultânea dos microfones.
2. Avaliação da qualidade do sinal.
3. Seleção automática do melhor canal.
4. Detecção da palavra de ativação 'Fofão'.
5. Reconhecimento de fala (Whisper).
6. Envio do texto ao Mission Core.
7. Resposta da IA.
8. Síntese de voz (Piper).
9. Reprodução do áudio.

## 4. Estados

IDLE
LISTENING
WAKE_DETECTED
TRANSCRIBING
THINKING
SPEAKING
ERROR
Cada transição deverá gerar eventos para o Event Bus.

## 5. Eventos

voice.wake_detected
voice.command_received
voice.transcription_ready
voice.response_started
voice.response_finished
voice.audio_error

## 6. Seleção Inteligente de Microfone

O sistema deverá medir nível de ruído, intensidade do sinal e estabilidade do áudio para escolher automaticamente o melhor microfone. Futuramente poderá utilizar fusão de áudio (beamforming) caso o hardware permita.

## 7. Cancelamento de Eco

Enquanto o Piper estiver reproduzindo áudio, o Voice Core deverá reduzir o risco de reconhecer a própria fala do robô utilizando supressão de eco e controle de estados.

## 8. Requisitos de Desempenho

• Inicialização automática.
• Funcionamento offline.
• Latência reduzida.
• Recuperação automática após falhas.
• Integração transparente com o Mission Core.

## 9. EDR-0006

Decisão: utilizar Whisper para reconhecimento de fala e Piper para síntese de voz local.
Motivação:
• Funcionamento sem internet.
• Privacidade.
• Baixa latência.
• Controle total da plataforma.

## Conclusão

O Voice Core fornece uma interface natural entre usuários e o ORION OS, permitindo comandos por voz e respostas faladas de forma totalmente local e integrada ao restante da arquitetura.