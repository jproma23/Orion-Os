# ORION OS — System Engineering Specification (SES)

## Capítulo 4 — Engenharia de Hardware e Integração Física

Versão 1.0 — Especificação Oficial

## 1. Objetivo

Definir oficialmente toda a arquitetura de hardware do ORION X, incluindo responsabilidades de cada equipamento, critérios de integração elétrica, comunicação, alimentação e expansão futura.

## 2. Arquitetura Física

O robô será composto por três unidades computacionais:
• Notebook (Mission Core)
• Raspberry Pi (Motion Core)
• Arduino Mega (Hardware Core)

Cada unidade executará apenas sua responsabilidade definida na arquitetura do ORION OS.

## 3. Notebook - Mission Core

Funções:
• IA (Ollama)
• Visão computacional (webcam USB principal, OpenCV, YOLO)
• Reconhecimento facial
• Whisper
• Piper
• Planejamento de missão
• Avatar e reprodução multimídia na tela integrada
• Reprodução multimídia
• Coordenação geral

Hardware utilizado:
• Tela integrada
• Webcam USB principal (visão) e webcam integrada (luminosidade)
• Microfone interno
• Comunicação Ethernet com o Raspberry (USB direto apenas para
  manutenção).

## 4. Raspberry Pi - Motion Core

Hardware: Raspberry Pi 4 (4 GB) + SSD de 500 GB via USB 3 (sistema e dados fora do cartão SD).

Funções:
• Navegação e planejamento de trajetória
• Fusão de sensores (encoders + IMU + ultrassons)
• Ponte de comunicação com o Arduino (USB Serial)
• Consolidação da telemetria (pacote Radar Inteligente)
• Banco SQLite, memória e aprendizado no SSD
• Servidor da interface web

O Raspberry envia eventos e informações de navegação ao Notebook via
Ethernet.

## 5. Arduino Mega - Hardware Core

Periféricos:
• 2 Drivers TB6600
• 2 Motores NEMA17
• Encoders de roda
• MPU6050
• Ultrassônico frontal
• Servo do radar (180°)
• Servos Pan/Tilt
• Ultrassônico traseiro
• Sensor de temperatura e umidade
• Lanterna LED

O Arduino executa comandos do Motion Core (Raspberry) e retorna
telemetria.

## 6. Alimentação

Cada módulo deverá possuir alimentação estável.
Motores e drivers serão alimentados por fonte dedicada.
Lógica (Notebook, Raspberry e Arduino) utilizará alimentação isolada conforme projeto elétrico.
Todos os GND deverão possuir referência comum quando necessário para comunicação.

## 7. Radar Frontal

O ultrassônico frontal será montado sobre um servo.
A varredura inicial ocorrerá entre 0° e 180°.
Os dados formarão um mapa simplificado de obstáculos utilizado pelo Motion Core (Raspberry) e sincronizado com o Notebook.

## 8. Iluminação Inteligente

A webcam do notebook estimará a luminosidade do ambiente.
Quando o brilho médio ficar abaixo do limite configurado, o Notebook enviará comando (via Raspberry) para acionar a lanterna LED no Arduino.
A decisão permanecerá configurável e futuramente poderá usar sensor BH1750 sem alterar a arquitetura.

## 9. Escalabilidade

Todos os sensores físicos deverão ser conectados ao Hardware Core (Arduino) sempre que possível.
Novos módulos poderão ser adicionados utilizando o barramento de eventos do ORION OS sem modificar o núcleo.

## Diagrama de Integração

                 NOTEBOOK
      IA • Visão • Voz • Avatar
                 │ Ethernet
                 ▼
       Raspberry Pi + SSD 500GB
  Navegação • Banco • Memória • Web UI
                 │ USB Serial
                 ▼
            Arduino Mega
     Motores • Servos • Sensores

## Conclusão

Esta especificação define a base física oficial do ORION X e será utilizada pelos próximos capítulos para protocolos, software e integração.