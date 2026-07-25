#!/usr/bin/env bash
# Desliga o Raspberry e o Notebook juntos, com seguranca (Cap 18: em duvida,
# parar - inclui parar com desligamento limpo, nunca corte de energia, que
# foi o que corrompeu o OS da primeira vez).
#
# Roda em qualquer uma das duas maquinas: agenda o desligamento limpo da
# OUTRA via SSH primeiro (systemd-run, pra nao depender da sessao SSH
# continuar viva ate o poweroff terminar do outro lado), depois desliga a
# maquina local. Tolera a outra maquina estar desligada/inacessivel - so
# avisa e segue desligando a local mesmo assim (Cap 6 s.8).
set -u

PI_HOST="pi-os.local"
NOTEBOOK_HOST="Joao.local"
CHAVE="$HOME/.ssh/id_ed25519"

aqui="$(hostname)"
if [ "$aqui" = "pi-os" ]; then
    outra="$NOTEBOOK_HOST"
    nome_outra="Notebook"
else
    outra="$PI_HOST"
    nome_outra="Raspberry"
fi

echo "Desligando $nome_outra ($outra)..."
if ssh -o BatchMode=yes -o ConnectTimeout=5 -i "$CHAVE" "jproma23@$outra" \
        "sudo -n systemctl poweroff" 2>/dev/null; then
    echo "$nome_outra desligando."
else
    echo "AVISO: nao consegui alcancar $nome_outra (ja desligado ou fora da rede?)" \
         "- seguindo so com esta maquina."
fi

echo "Desligando esta maquina ($aqui) em 3s... (Ctrl+C para cancelar)"
sleep 3
sudo -n systemctl poweroff
