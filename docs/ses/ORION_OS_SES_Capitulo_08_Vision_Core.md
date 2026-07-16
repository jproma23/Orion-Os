# ORION OS — System Engineering Specification (SES)

## Capítulo 8 — Vision Core (Notebook)

Versão 1.1 — alinhada ao TCC (EDR-0018)

## 1. Objetivo

O Vision Core é o subsistema de percepção visual do ORION OS. Ele executa no Notebook (Mission Core) e transforma imagens em informações estruturadas: detecção de pessoas e objetos, reconhecimento facial e reconhecimento de ambientes.

## 2. Hardware

Computador: Notebook (mesmo hardware do Mission Core).
Periféricos:
• Webcam USB principal conectada ao Notebook.
• Câmera integrada do notebook (estimativa de luminosidade e apoio).
• Servos Pan/Tilt físicos controlados pelo Hardware Core (Arduino); o Vision Core apenas calcula as correções e as envia como comandos via Motion Core.

## 3. Pipeline de Visão

1. Captura do frame.
2. Pré-processamento (resolução, correções).
3. Inferência YOLO.
4. Reconhecimento facial (pessoas autorizadas).
5. Rastreamento de objetos.
6. Cálculo das correções Pan/Tilt.
7. Geração de eventos no Event Bus.

## 4. Responsabilidades

• Detectar pessoas e objetos.
• Reconhecer rostos de usuários autorizados.
• Estimar posição do alvo na imagem.
• Rastrear automaticamente um alvo.
• Calcular correções Pan/Tilt (execução física no Arduino).
• Estimar luminosidade do ambiente.
• Publicar eventos no Event Bus.

## 5. Eventos Publicados

vision.person_detected
vision.person_recognized
vision.person_lost
vision.object_detected
vision.target_centered
vision.environment_changed
vision.camera_error

## 6. Integração com a Navegação

O Vision Core nunca toma decisões estratégicas. Ele publica informações estruturadas (classe, confiança, coordenadas, timestamp, estado do rastreamento). O Mission Core combina essas informações com o mapa e envia missões ao Motion Core. Na autocalibração (Cap 12), o Vision Core mede o deslocamento real do robô para cálculo do fator de correção.

## 7. Reconhecimento de Ambientes

O módulo poderá identificar ambientes utilizando características visuais e objetos predominantes (cozinha, sala, corredor, garagem, área externa). O resultado alimenta o aprendizado contínuo na memória.

## 8. Controle Pan/Tilt

O alvo detectado será mantido no centro da imagem. O Vision Core calcula os ângulos; os comandos seguem Notebook → Raspberry → Arduino, com limites de velocidade, aceleração e ângulo para evitar movimentos bruscos.

## 9. Recuperação de Falhas

Caso a câmera seja desconectada ou ocorra erro de processamento:
• Publicar evento vision.camera_error.
• Reiniciar o pipeline.
• Manter o restante do ORION OS operacional (modo SEM_VISÃO).

## 10. EDR-0018 (substitui EDR-0005)

Decisão: executar a visão computacional no Notebook.
Motivação:
• O Notebook possui o maior poder computacional do sistema.
• Libera o Raspberry para navegação em tempo quase-real.
• Elimina o tráfego de metadados de visão pela rede.

## Conclusão

O Vision Core fornece ao ORION OS uma percepção visual modular e desacoplada, permitindo substituir câmeras ou algoritmos de visão sem alterar o restante da arquitetura.
