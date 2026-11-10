// MODELO dos segredos dos ouvidos (2026-11-10, auditoria C4-08).
//
// Copie para include/segredos.h (mesma pasta) e troque a senha. O
// segredos.h NAO vai para o git (firmware/ouvidos_esp32/.gitignore) - este
// modelo vai, para a proxima pessoa saber o que criar.
//
//   cp include/segredos.exemplo.h include/segredos.h
//
// O MESMO arquivo serve aos dois lados do OTA:
//   - src/main.cpp le OTA_SENHA e manda o ESP32 exigir a senha;
//   - ota_senha.py le OTA_SENHA daqui e passa `--auth` ao espota no
//     `pio run -e ota -t upload`. Uma senha so, num lugar so.
//
// PRIMEIRA GRAVACAO COM SENHA: o firmware que esta na placa hoje nao tem
// senha, entao o primeiro upload com --auth passa normalmente (o espota so
// usa a senha se a placa pedir). Dali em diante, sem a senha certa o upload
// falha com "Authentication Failed".
//
// Sem segredos.h o firmware compila SEM senha e avisa no log do boot.
#pragma once

#define OTA_SENHA "troque-esta-senha"
