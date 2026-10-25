# EDR-0036 — O Notebook volta: visão e voz no Notebook, Mission Core no Pi

**Data:** 30/09/2026 (decisão) · registrada em 25/10/2026
**Status:** Decidido — execução **pela metade** (ver "Estado em 25/10")
**Módulos:** Vision Core, Voice Core, Mission Core, Communication Core
**Substitui:** a divisão de máquinas da EDR-0019 e a aposentadoria do Notebook de 09/09/2026
(que nunca virou EDR)
**Relacionado:** EDR-0019 (divisão original), EDR-0021 (IA remota), EDR-0029 (papel da IA local)

---

## Contexto

O robô já teve três divisões de máquinas, e só a primeira foi registrada:

| Quando | Divisão | Registro |
|---|---|---|
| 15/07 | Notebook = Mission Core inteiro (IA, visão, voz, avatar); Pi = Motion Core | EDR-0019 |
| 09/09 | **Notebook aposentado**: tudo migra para o Pi; a tela ESP32-S3 vira a cabeça | só no journal e no `orion.yaml` |
| 30/09 | **O Notebook volta**, numa divisão diferente da de julho | esta EDR |

O que tirou o Notebook em 09/09 foram dois problemas:
1. **o enlace TCP caía**: IP por DHCP, timeouts e nenhuma reconexão;
2. **o Pi ficava sem fôlego** para carregar tudo: Whisper, YOLO e Piper junto com SLAM e LiDAR, a 82 °C. Depois se descobriu que o ventilador estava desligado.

O primeiro problema foi resolvido em 20/09: o enlace Mission↔Motion passou a reconectar sozinho, insistindo 30 s no boot e voltando em 7 s quando cai. O segundo não tem solução no Pi: o trabalho pesado não cabe lá.

## Decisão

**Cada máquina faz o que ela faz melhor.** O Notebook faz o trabalho pesado que pode esperar. O Pi faz o que não pode falhar nem esperar, e guarda o estado.

| Máquina | Roda | Hardware ligado nela |
|---|---|---|
| **Notebook** | **Vision Core**: YOLO e reconhecimento de rosto | **webcam USB** |
| | **Voice Core**: palavra de ativação, Whisper, chamadas da IA (Gemini), Piper | **alto-falante USB** |
| **Raspberry Pi** | **Motion Core**: LiDAR, corpo (ESP32), segurança, webui | LiDAR, ESP32 do corpo |
| | **Mission Core**: memória, recados, GOAP, decisões | SSD (banco) |
| | Tela ESP32-S3: rosto e toque | tela |
| **ESP32 dos ouvidos** | 2 microfones I2S, 16 kHz | fala **direto com o Notebook** (TCP 9100, Wi-Fi) |

### As regras que acompanham a divisão

1. **O Notebook só processa e não guarda nada.** Memória, recados e decisões moram no Pi. Se o Notebook for formatado, o robô não esquece ninguém.
2. **Vídeo e áudio não passam pela rede.** A webcam e o microfone chegam direto ao Notebook. Para o Pi vão só **eventos**: pessoa vista, texto ouvido e o nível da fala (`fala_nivel`), que faz a boca do rosto mexer junto com o som.
3. **Se o Notebook sumir, o robô fica seguro, parado e avisando.** Ele nunca trava esperando o Notebook. Como o Piper saiu do Pi, o aviso "estou sem cérebro" precisa ser um **áudio gravado guardado no Pi**. Quando o Notebook volta, o robô volta junto, sem intervenção.
4. **IP fixo nas duas máquinas**, de preferência por reserva no roteador (pelo MAC). Até isso existir, usa-se o **nome** (`pi-os.local`, `Joao.local`), nunca o IP escrito à mão.

## Alternativas consideradas

- **Tudo no Pi** (a divisão de 09/09). Recusada pelo dono em 30/09: o Pi não aguenta visão, voz e navegação ao mesmo tempo, e tudo fica mais lento quando ele esquenta.
- **Tudo no Notebook, como em julho** (EDR-0019). Recusada: deixa a memória e as decisões na máquina que pode sumir da rede. A regra 1 existe por causa disso.
- **Voz saindo pela tela ESP32-S3.** Recusada pelo dono: a voz sai no alto-falante USB do Notebook, e a tela recebe só o nível da fala para a boca.

## Impacto

- **Cai a afirmação "o Notebook foi aposentado"**, que está no `config/orion.yaml` desde 16/09.
- O **enlace de dados Pi↔Notebook volta a existir** (TCP 5757). Os riscos dele já aconteceram antes e precisam de teste:
  - os rostos se perdem se o Notebook subir antes do Pi (boot race);
  - o erro acontece num lado e o sintoma aparece no outro, então é preciso medir nas duas pontas;
  - um evento publicado sem ninguém assinando não dá erro nenhum.
- **A suíte roda nas duas máquinas.** Cada lado coleta testes diferentes, e passar em um não prova nada sobre o outro.
- O `ARQUITETURA.txt`, o `README.md`, o `docs/sobre.md` e o `ESTADO_DOS_MODULOS.md` passam a apontar para esta EDR.

## Estado em 25/10/2026 (conferido nas duas máquinas)

A volta foi feita **pela metade**, em 12/10:

- O `orion-mission` foi **desligado e desabilitado no Pi**.
- O Notebook roda o `orion-avatar` (`tools/conversar_fofao.py`) com o **código de 19/08** (`c9c45a8` + 61 arquivos sem commit). Esse código inclui o **Mission Core inteiro**.
- A config do Notebook passou a achar o Pi por `pi-os.local` e o alto-falante virou o índice 5.
- O robô conversa: o Notebook está ligado ao Pi (5757) e aos ouvidos (9100).

O que **ainda contradiz** esta decisão:

1. **A memória está rodando no Notebook**, contra a regra 1.
2. **Os códigos divergiram**: o Pi tem 38 commits que o Notebook não tem, e o Notebook tem 9 commits e 61 arquivos que o Pi não tem, incluindo o identificador de voz (SpeechBrain).
3. **O IP fixo não foi feito**: o Pi está em 10.20.20.106, e o remote `pi` do git no Notebook ainda aponta para o .196.
4. **O áudio gravado de "sem cérebro" não existe.**

## Ordem de execução (o que falta)

1. Juntar os dois códigos: trazer o trabalho do Notebook para o Pi um arquivo por vez, depois atualizar o Notebook a partir do Pi (`git fetch` + `merge`, nunca `scp` de arquivo solto) e rodar a suíte nas duas máquinas.
2. Religar o Mission Core no Pi e deixar o Notebook só com visão e voz.
3. Gravar o aviso de queda e testar puxando o Wi-Fi do Notebook no meio de uma conversa.
4. Tirar do Pi o que migrou (Whisper, Piper, wake word, visão) **só depois** de provado no Notebook, medindo RAM e temperatura antes e depois.
