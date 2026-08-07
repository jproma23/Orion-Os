#!/bin/sh
# Relanca o kiosk do avatar depois de uma mudanca de tela.
#
# Existe porque reiniciar o orion-avatar.service NAO recarrega o navegador: o
# servidor volta com o HTML novo e o Firefox continua mostrando a pagina que
# carregou quando abriu - foi assim que uma aula rodou inteira no servidor
# sem aparecer nada na tela, em 2026-08-07.
export DISPLAY=:0.0
export XAUTHORITY="$HOME/.Xauthority"
PERFIL="$HOME/.mozilla/firefox/orion-avatar-kiosk"

pkill -x firefox-esr 2>/dev/null   # o binario e firefox-esr; `-x firefox` nao casa
sleep 3

# CACHE EM DISCO. Nao basta reabrir o navegador: o perfil guarda a pagina em
# ~/.cache e a devolve mesmo numa abertura nova. O `no-store` que o servidor
# manda hoje so vale para o que for buscado DEPOIS - o que ja estava gravado
# antes dele continua sendo servido do disco. Foi isso que fez a tela mostrar
# o cabecalho de duas versoes atras com o servidor entregando a pagina nova.
rm -rf "$HOME/.cache/mozilla/firefox/orion-avatar-kiosk" 2>/dev/null
# O lock fica para tras quando o processo e morto, e o Firefox seguinte sai
# calado por causa dele.
rm -f "$PERFIL/lock" "$PERFIL/.parentlock" 2>/dev/null

setsid firefox --profile "$PERFIL" --no-remote --kiosk http://127.0.0.1:8090 \
  </dev/null >/tmp/kiosk.log 2>&1 &

sleep 20
if pgrep -x firefox-esr >/dev/null; then
  echo "KIOSK NO AR"
else
  echo "KIOSK NAO SUBIU - veja /tmp/kiosk.log"
  tail -5 /tmp/kiosk.log
  exit 1
fi
