# ORION OS --- System Engineering Specification (SES)

# Capítulo 2 --- Filosofia, Requisitos de Engenharia e Arquitetura Conceitual

**Versão:** 1.0 (Rascunho Oficial)

## 1. Filosofia do ORION OS

O ORION OS foi concebido como uma plataforma robótica distribuída. Cada
equipamento possui uma responsabilidade exclusiva e nunca deve executar
funções pertencentes a outro módulo sem justificativa técnica.

A arquitetura privilegia:

-   Robustez
-   Escalabilidade
-   Modularidade
-   Manutenção simples
-   Processamento distribuído
-   Baixo acoplamento
-   Alta disponibilidade

------------------------------------------------------------------------

## 2. Arquitetura Conceitual

### Mission Core (Notebook)

É o cérebro do robô.

Responsabilidades:

-   Inteligência Artificial (Ollama)
-   Visão computacional (OpenCV, YOLO) e reconhecimento facial
-   Planejamento
-   Tomada de decisão
-   Voz: Whisper (escuta) e Piper (fala)
-   Avatar na tela integrada e reprodução multimídia
-   Coordenação de todos os módulos
-   Comunicação com o Raspberry Pi via Ethernet

Nunca deverá acessar diretamente sensores de movimento nem falar com o
Arduino: toda ordem física passa pelo Motion Core (Raspberry).

------------------------------------------------------------------------

### Motion Core (Raspberry Pi)

Especializado em navegação.

Responsabilidades:

-   Navegação (patrulha, seguimento, desvio)
-   Fusão de sensores (encoders + IMU + ultrassons)
-   Planejamento de trajetória
-   Ponte de comunicação com o Arduino (USB Serial)
-   Conversão dos dados físicos em informações de navegação
-   Memória, aprendizado e banco de dados SQLite no SSD de 500 GB
-   Interface web (dashboard, mapa, diagnóstico, configuração)

O Raspberry enviará eventos e resultados ao Notebook, evitando
transmitir dados brutos desnecessários.

------------------------------------------------------------------------

### Hardware Core (Arduino Mega)

Especializado em tempo real.

Responsabilidades:

-   Controle dos motores NEMA17
-   Geração dos pulsos STEP/DIR para TB6600
-   Leitura do MPU6050 e dos encoders
-   Leitura dos sensores ultrassônicos
-   Controle dos servos (radar frontal e Pan/Tilt)
-   Sensor de temperatura/umidade
-   Telemetria (pacote Radar Inteligente ao Raspberry)

------------------------------------------------------------------------

## 3. Requisitos Funcionais

O sistema deverá:

-   Conversar por voz.
-   Reconhecer pessoas.
-   Detectar objetos.
-   Reconhecer ambientes.
-   Patrulhar automaticamente.
-   Seguir pessoas autorizadas.
-   Acionar lanterna automaticamente quando a luminosidade estimada pela
    câmera do notebook for insuficiente.
-   Registrar todos os eventos importantes.

------------------------------------------------------------------------

## 4. Requisitos Não Funcionais

-   Inicialização automática.
-   Recuperação automática após falhas.
-   Operação offline.
-   Código modular.
-   Atualizações independentes por módulo.
-   Comunicação confiável entre dispositivos.

------------------------------------------------------------------------

## 5. Fluxo Geral

1.  O Notebook analisa o vídeo (Vision Core) e gera eventos.
2.  O Mission Core consulta memória e IA.
3.  A IA define uma missão e a envia ao Raspberry (Ethernet).
4.  O Raspberry planeja a trajetória e envia comandos ao Arduino
    (USB Serial).
5.  O Arduino executa o movimento e retorna telemetria ao Raspberry.
6.  O Raspberry consolida a telemetria; o Notebook atualiza o estado
    do sistema e a interface.

------------------------------------------------------------------------

## 6. Convenções de Projeto

Todos os módulos deverão possuir:

-   README próprio
-   Documentação técnica
-   Interface pública definida
-   Testes
-   Logs estruturados
-   Versionamento semântico

------------------------------------------------------------------------

## 7. Decisões Arquiteturais

-   O Notebook é o coordenador estratégico do sistema.
-   O Raspberry Pi é o coprocessador de navegação e a única ponte com
    o Arduino.
-   O Arduino Mega é um controlador de tempo real.
-   A evolução de qualquer módulo não deverá exigir alterações profundas
    nos demais.

**Fim do Capítulo 2**
