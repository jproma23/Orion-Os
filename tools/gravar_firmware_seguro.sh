#!/usr/bin/env bash
# Grava o firmware do Arduino com seguranca - roda SEMPRE no Raspberry
# (e onde o Mega esta ligado por USB, nunca no Notebook - CLAUDE.md regra 2).
#
# Por que existir (achado real, 2026-07-24): orion-motion.service fica com
# a porta serial do Arduino aberta o tempo todo, mandando heartbeat/comando.
# Gravar firmware por cima disso (`pio run -t upload`) faz dois processos
# brigarem pela mesma porta ao mesmo tempo - visto na pratica: heartbeat
# falhando repetido depois da gravacao, e o servo do radar reagindo de
# forma imprevisivel durante a janela de conflito. O reset do proprio chip
# ao ser reprogramado (pino DTR) tambem faz qualquer servo anexado dar um
# tranco - isso e inerente a reprogramar um AVR e nenhum firmware evita (o
# chip nao esta rodando codigo nenhum durante a gravacao) - mas parar o
# servico antes elimina a segunda causa (comandos incoerentes chegando
# durante/logo apos o reset) e deixa so o tranco inevitavel do reset em si.
#
# Sempre reinicia o servico no final, mesmo se a gravacao falhar (trap) -
# nunca deixar o Motion Core fora do ar por um upload que deu errado.
set -u

DIR_FIRMWARE="$HOME/orion-os/firmware/hardware_core"
PIO="$HOME/.venv-pio/bin/pio"
SERVICO="orion-motion.service"

if [ "$(hostname)" != "pi-os" ]; then
    echo "ERRO: rode este script no Raspberry (pi-os), nao aqui - e onde o" \
         "Arduino esta ligado por USB (CLAUDE.md regra 2)." >&2
    exit 1
fi

_reiniciar_servico() {
    echo "Religando $SERVICO..."
    systemctl --user start "$SERVICO"
}
trap _reiniciar_servico EXIT

echo "Parando $SERVICO antes de gravar (libera a porta serial de verdade)..."
systemctl --user stop "$SERVICO"
sleep 1  # da tempo do kernel liberar /dev/ttyUSB* de fato antes do avrdude abrir

echo "Compilando e gravando..."
cd "$DIR_FIRMWARE" || exit 1
"$PIO" run -t upload
resultado=$?

if [ $resultado -eq 0 ]; then
    echo "Gravacao concluida - o servo pode ter dado um tranco no reset do" \
         "chip, isso e esperado e nao e bug. $SERVICO volta a subir agora" \
         "(trap no final do script)."
else
    echo "AVISO: gravacao falhou (codigo $resultado) - $SERVICO volta a" \
         "subir mesmo assim, com o firmware anterior ainda no chip." >&2
fi

exit $resultado
