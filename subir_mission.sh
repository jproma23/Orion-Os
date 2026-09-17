#!/bin/bash
# Sobe o Mission Core no Pi (ele nao e servico systemd - ver docs/journal.md).
#
# Roda como script, e nao como comando solto por SSH, por um motivo concreto:
# `pkill -f "python -m orion"` casa com a PROPRIA linha de comando do bash que
# o SSH abriu e derruba a sessao (exit 255). Aqui matamos por PID, achado com
# um padrao que nao pega o script.
cd ~/orion-os || exit 1

antigos=$(pgrep -f "python -u -m orion$")
if [ -n "$antigos" ]; then
  echo "derrubando Mission Core anterior: $antigos"
  kill $antigos 2>/dev/null
  sleep 5
  ainda=$(pgrep -f "python -u -m orion$")
  [ -n "$ainda" ] && kill -9 $ainda 2>/dev/null && sleep 2
fi

# sem isto ele sobe SEM a chave da IA (que apesar do nome e a do Gemini)
set -a
. config/secrets.env
set +a

nohup .venv/bin/python -u -m orion > /tmp/mission_core.log 2>&1 &
echo "Mission Core subindo, PID=$!"
