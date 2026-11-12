# Plano de implantação — auditoria de 09 a 12/11/2026

**O que vai para o robô:** o branch `auditoria-2026-11-09` INTEIRO (todos os commits sobre
`7eda681` — dono, 12/11: "toda a auditoria precisa ser atualizada"; nada é escolhido à parte). Tudo foi testado no Windows (1504 testes passam; 17 falham só por limitação do
Windows) e **nada rodou ainda no robô**. Os achados e o porquê de cada mudança estão em
`docs/ARQUITETURA_E_AUDITORIA.md` (seções 3 a 12).

**Regra do plano:** uma etapa por vez, e só se passa para a seguinte com a anterior conferida.
Cada etapa diz **o que fazer**, **como saber que deu certo** e **o que fazer se der errado**.
Nada novo é ligado sem medida (`peso_camera` continua 0 até medir o FOV).

Tempo estimado: ~2 h de software + ~1 h de bancada (rodas no ar) + ~30 min no chão.

---

## Etapa 0 — Antes de ligar (no Windows)

1. Conferir que o backup está como deixado:
   `cd ~/ORION_backup/auditoria && git status && git log --oneline -1` → árvore limpa,
   último commit da auditoria.
2. Rodar a suíte uma última vez aqui (referência para comparar depois): devem ser
   **1504 passando** e as mesmas 17 falhas de ambiente (`os.openpty`, `openai`, caminhos).
3. Publicar o branch no GitHub **como branch** (não no master):
   `git push origin auditoria-2026-11-09`.
   O master do GitHub não muda nesta etapa.

**Se der errado:** nada foi tocado no robô; parar aqui.

---

## Etapa 1 — Ligar e conferir o estado real (robô em MANUAL, rodas no ar)

1. Ligar Pi e Notebook. Acesso: `ssh -i ~/.ssh/orion_pi_ed25519 jproma23@100.112.10.2`
   (Tailscale) ou o IP da rede local.
2. **Pôr o robô em MANUAL** pela webui antes de qualquer coisa (ele sobe em AUTÔNOMO).
3. Conferir, nas DUAS máquinas, de onde o serviço roda e em que commit está:
   - Pi: `cd ~/orion-os && git status && git log --oneline -1` → esperado `7eda681`.
   - Notebook: `systemctl --user cat orion-avatar | grep -E "WorkingDirectory|ExecStart"`
     — em 01/11 a produção rodava de `~/orion-jarvis`, não de `~/orion-os`. **Atualizar o
     diretório que o serviço usa**, e anotar qual é.
4. Se houver mudança local sem commit em qualquer máquina: **commitar lá antes** (não apagar).
   Se o commit não for `7eda681`, o merge da Etapa 3 junta os dois; não usar `reset`.
5. Marcar o ponto de volta nas duas máquinas:
   `git tag antes-auditoria-2026-11 && git push origin antes-auditoria-2026-11` (só no Pi).
6. Copiar o banco: `cp /mnt/ssd/orion/orion.db /mnt/ssd/orion/orion.db.antes-auditoria`.

**Deu certo se:** as duas máquinas têm commit conhecido, tag criada e cópia do banco.

---

## Etapa 2 — Parar os serviços (nunca rodar a suíte com o robô no ar — travou o Notebook em 08/11)

- Pi: `systemctl --user stop orion-motion orion-mission`
- Notebook: `systemctl --user stop orion-avatar`

---

## Etapa 3 — Trazer o código (git, nunca `scp` de arquivo solto)

Nas duas máquinas, no diretório do serviço:

```bash
git fetch origin
git merge --no-ff origin/auditoria-2026-11-09
```

**Conflito no `config/orion.yaml`:** se a máquina tiver mudanças locais no yaml, manter as
locais E acrescentar as chaves novas da auditoria (lista no fim deste plano). Na dúvida,
parar e conferir com o dono — o yaml manda no robô.

Dependências mudaram (`aiohttp` entrou; `ollama` e `openwakeword` saíram):

```bash
.venv/bin/pip install -e .          # Pi
.venv/bin/pip install -e ".[dev]"   # Notebook (ou como foi instalado)
```

**Deu certo se:** `git log --oneline -1` mostra o merge e o `pip` termina sem erro.
**Se der errado:** `git merge --abort` (se ainda no merge) ou `git reset --hard antes-auditoria-2026-11`.

---

## Etapa 4 — Suíte nas DUAS máquinas (serviços parados)

```bash
.venv/bin/pytest -q
```

- **Pi:** é onde os testes de serial (`os.openpty`) finalmente rodam — os 7 que falham no
  Windows têm que PASSAR aqui.
- **Notebook:** visão, voz e planner com as bibliotecas de verdade (`ultralytics`,
  `face_recognition`), que no Windows foram simuladas.

**Deu certo se:** nenhuma falha nova em relação à lista do Windows. Qualquer falha nova:
**parar e anotar a saída inteira** — não seguir para o firmware.

---

## Etapa 5 — Firmware do corpo (ESP32), rodas no ar

Mudou: NACK explícito, homem-morto estrito, bússola segura, relé no GPIO 17, versão no
WHO_ARE_YOU, giro de calibração que para sem o Pi, leitura do encoder sem perder pulsos.

1. `systemctl --user stop orion-motion` (o serviço segura a serial).
2. **Guardar o firmware atual** antes de gravar o novo:
   `~/.venv-pio/bin/esptool.py --port /dev/ttyUSB0 read_flash 0 0x400000 ~/firmware_corpo_antes_auditoria.bin`
3. Gravar:
   `cd firmware/hardware_core && ~/.venv-pio/bin/pio run -t upload --upload-port /dev/ttyUSB0`
4. Se a placa não voltar sozinha depois de gravar: tirar e pôr a energia do ESP32
   (aconteceu antes de 10/11 por causa do GPIO 2; o relé saiu de lá, então não deve repetir).
5. `systemctl --user start orion-motion` e conferir no log:
   `Hardware Core (ESP32) conectado e confirmado` e a versão nova (hash do git).

**Deu certo se:** o painel mostra o corpo vivo e **nenhum** "REGRAVAR o firmware" nos erros.
**Se der errado:** `esptool.py --port /dev/ttyUSB0 write_flash 0 ~/firmware_corpo_antes_auditoria.bin`.

---

## Etapa 6 — Firmware dos ouvidos (OTA com senha nova)

1. Na máquina que compila, criar `firmware/ouvidos_esp32/include/segredos.h` a partir do
   `segredos.exemplo.h`, com a `OTA_SENHA` escolhida pelo dono. **Não escrever a senha no
   chat nem commitar o arquivo** (está no `.gitignore`).
2. Gravar por OTA (`pio run -t upload`, porta = IP dos ouvidos). O firmware antigo não tem
   senha, então esta primeira gravação passa; as próximas pedem a senha.
3. Conferir no log do Notebook que o áudio dos ouvidos voltou (TCP 9100).

**Opcional nesta sessão:** os ouvidos só ganharam senha no OTA e reinício sem Wi-Fi. Pode
ficar para depois sem prejuízo para o resto.

---

## Etapa 7 — Subir tudo

- Pi: `systemctl --user start orion-motion orion-mission`
- Notebook: `systemctl --user start orion-avatar`
- Nav2: só quando for navegar. As pontes são montadas no container, então basta
  `docker compose up -d --force-recreate` em `nav2/`; não precisa reconstruir a imagem.

**Deu certo se:** webui abre, painel sem erro novo, corpo vivo, visão e voz respondendo.

---

## Etapa 7b — YOLO funcionando (PRIORIDADE do dono)

Dono, 12/11: *"o YOLO é importante, não funciona, precisa funcionar"* — *"tem que informar a IA
sobre os objetos ao seu redor"* — *"o problema é que ele não detecta"*.

O que a auditoria já mudou no caminho (chega com o merge):
- câmera parada deixava de virar "sem sinal" (V1) — antes o YOLO **parava de rodar** em sala parada;
- o modelo é procurado em `data/modelos/` (V5);
- **objetos informam a IA a partir de 0,35** (antes só acima de 0,55 — a IA estratégica quase nunca
  sabia de cadeira, copo ou TV); pessoa continua em 0,55.

Passos, no Notebook, com o `orion-avatar` no ar:

1. `ls data/modelos/yolov8n.pt` no diretório do serviço. Se não existir: copiar do Pi
   (`scp jproma23@<pi>:orion-os/data/modelos/yolov8n.pt data/modelos/`) — é dado, não código.
2. `.venv/bin/python tools/diagnostico_yolo.py --salvar /tmp/quadro.jpg` — ele para no primeiro
   elo quebrado e diz o conserto:

   | Resultado | O que fazer |
   |---|---|
   | 1. MODELO falha | copiar o `yolov8n.pt` (passo 1) |
   | 2. CÂMERA: snapshot não vem (503) | visão fora/pausada: `journalctl --user -u orion-avatar \| grep -i vis` |
   | 2. CÂMERA: imagem escura/lisa | abrir `/tmp/quadro.jpg`: lente tampada? apontada para o teto? |
   | 3. YOLO: nada em 320, algo em 640 | alvo pequeno/longe: subir `vision.yolo_imgsz` para 480 ou 640 e **medir** o ms/quadro que a ferramenta mostra |
   | 3. YOLO: achados só "ABAIXO dos dois limiares" | a cena tem pouco que o modelo conhece; NÃO baixar limiar às cegas — anotar e decidir |
   | 4. limiar de congelado antigo | o merge não entrou (voltar à Etapa 3) |

3. Com a ferramenta passando: perguntar **"o que você está vendo?"** com objetos comuns na frente
   (cadeira, garrafa, copo, celular) — tem que listar.
4. No Pi, conferir que a IA recebe: no log do `orion-motion` (ou `/estado` da webui, retrato do
   mundo), `objetos_vistos` com as classes da cena.

**Deu certo se:** os objetos da cena aparecem na resposta falada E no retrato que vai para a IA.

---

## Etapa 8 — Bancada com as rodas no ar (cada item prova um conserto)

| # | Fazer | Esperado | Conserto que prova |
|---|---|---|---|
| 1 | Painel → diagnóstico | Sem `protocol_mismatch`; banco sem `database.rebuilt` | A6, publicar no vazio |
| 2 | Mão/caixa a 20 cm ATRÁS, mandar ré no MANUAL | Ré recusada | C1-04 (piso 25 cm) |
| 3 | Objeto a 25 cm do LADO, mandar girar | Giro recusado | raio de giro 37,5 cm |
| 4 | Mesmo objeto, pedir "calibrar a bússola" | Recusado | calibrar girando na trava |
| 5 | Calibrar a bússola (sem obstáculo) e, no meio, `systemctl --user stop orion-motion` | Rodas param em ~2,5 s | giro de calibração sem o Pi |
| 6 | Rodas girando 3–5 min (`CMD_VEL` baixo), olhando `motion.position` | Pose sobe liso, **sem salto** de metros | encoder PCNT |
| 7 | "o que você está vendo?" de perto, de longe e com a lente tampada | Nome de perto; "uma pessoa" de longe; "não consigo enxergar…" tampada | V1–V5 |
| 8 | "deixa um recado para a Marisani" | "Guardei… você quis dizer Marisane?" | recados |
| 9 | "quero parar de fumar" | Para as rodas E responde o assunto | C5-04 |
| 10 | Botões do painel (MANUAL/AUTÔNOMO, cabeça, desligar NÃO) | Funcionam como antes | CSRF não quebrou o painel |
| 11 | De outro PC: `curl -X POST -H "Origin: http://x.com" http://<pi>:8080/controle` | **403** | CSRF fechado |

Qualquer item falhando: **anotar e não ir para o chão.**

---

## Etapa 9 — No chão, com espaço livre (dono olhando)

| # | Fazer | Esperado |
|---|---|---|
| 1 | "me siga", andar na frente | Segue, para a ~80 cm, cabeça acompanha |
| 2 | Durante o siga, virar de costas | Continua seguindo (o alvo guarda o nome) |
| 3 | Outra pessoa conhecida passa perto | O alvo NÃO troca de pessoa |
| 4 | "Jarvis, para" no meio do siga | Para e **não volta** a andar |
| 5 | Ré devagar até perto de uma parede | Para antes de encostar |
| 6 | **Medida do FOV da câmera:** marcar 90° no chão, cabeça parada, girar o robô; somar os `delta_graus` de `vision.giro_visual` no log | `fov_certo = 60 × 90 ÷ soma` |

Com o item 6 medido: pôr o FOV em `vision.odometria_visual.fov_horizontal_graus` e **só então**
`motion.odometria_visual.peso_camera: 0.5`. Repetir o giro de 90° e conferir a orientação.

---

## Etapa 10 — Fechar

1. Se tudo passou: merge do branch no `master` do GitHub, e `git pull` nas duas máquinas.
2. No Windows: `~/ORION_backup/atualizar_backup.sh`.
3. Registrar no `docs/journal.md` o que foi testado, com os números (pose sem salto,
   distância do siga, FOV medido).

---

## Como voltar atrás (qualquer etapa)

```bash
# código (Pi e Notebook)
git reset --hard antes-auditoria-2026-11 && .venv/bin/pip install -e .
# firmware do corpo
esptool.py --port /dev/ttyUSB0 write_flash 0 ~/firmware_corpo_antes_auditoria.bin
# banco
cp /mnt/ssd/orion/orion.db.antes-auditoria /mnt/ssd/orion/orion.db
```

---

## Pendências que NÃO são deste plano

- **Hardware:** resistor de 10k de cada IN1..IN4 do L298N para o GND (tranco no reset do ESP32).
- **Dado:** o recado #6 ("Marisani", 06/08) continua preso no banco — entregar ou corrigir à mão.
- **Decisões do dono já registradas:** webui sem senha; 25 chaves de config sem leitor
  mantidas como intenção (`security` marcada como não implementada).

## Chaves de config que a auditoria mexeu (para conferir no merge do yaml)

| Chave | Valor | Por quê |
|---|---|---|
| `chassi.raio_seguranca_m` | 0.25 | raio único de segurança |
| `chassi.lidar_ate_eixo_motriz_m` | 0.10 | giro em volta do eixo dianteiro |
| `motion.trava_lidar.piso_manual_cm` | 25 | ré manual protegida |
| `motion.trava_lidar.raio_giro_cm` | 37.5 | canto traseiro no giro |
| `motion.odometria_visual.*` | `peso_camera: 0.0` | câmera só testemunha até medir |
| `vision.odometria_visual.*` | `fov_horizontal_graus: 60` | **estimativa** — medir na Etapa 9 |
| `navigation.follow_distance_m` | 0.8 | era 1.2 e ninguém lia |
| `behavior.ia_estrategica.intervalo_s` | só o 600 | o 300 duplicado saiu |
