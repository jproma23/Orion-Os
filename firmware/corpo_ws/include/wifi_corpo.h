// WiFi do corpo que NAO exige regravar o firmware para trocar de rede.
//
// POR QUE EXISTE (2026-09-20, pergunta do dono: *"e quando mudar de rede?"*).
//
// A primeira versao deste firmware levava SSID e senha compilados dentro do
// binario - igual ao firmware antigo, sem fonte, que tinha as credenciais
// legiveis com `strings`. O problema nao e so o vazamento: e que trocar de
// rede passaria a exigir CABO E COMPUTADOR. Num robo que anda, e na casa de
// quem eventualmente troca de roteador ou leva o robo para outro lugar,
// isso transforma uma mudanca trivial numa operacao de bancada.
//
// Aqui as credenciais moram na NVS (Preferences), que SOBREVIVE a regravacao
// do firmware, e ha tres formas de mudar - nenhuma delas com cabo:
//
//   1. pelo WebSocket, com o robo ainda conectado na rede velha:
//        {"cmd":"wifi","ssid":"NovaRede","senha":"..."}
//   2. pelo PORTAL: sem conseguir conectar, o ESP32 sobe uma rede propria
//      (`orion-corpo`) e serve uma pagina onde se digita a rede nova - dá
//      para fazer do celular, com o robo no meio da sala.
//   3. de fabrica: o `segredos_wifi.h` so vale quando a NVS esta vazia, ou
//      seja, no primeiro boot depois de gravar.
//
// A ORDEM IMPORTA: NVS primeiro, `segredos_wifi.h` depois. Ao contrario, uma
// regravacao jogaria fora a rede configurada pelo portal e o robo voltaria
// para a rede antiga - que e exatamente o problema que isto resolve.
#pragma once

#include <Arduino.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>

#include "segredos_wifi.h"

namespace corpo {

//: Quanto esperar a rede conhecida antes de desistir e abrir o portal.
//: 20 s cobre um roteador lento; mais que isso e so atraso no boot.
constexpr unsigned long TIMEOUT_CONEXAO_MS = 20000;

//: A rede que o proprio robo cria quando nao acha nenhuma conhecida.
//: A senha e fixa e esta no repositorio DE PROPOSITO: ela nao protege nada
//: que ja nao esteja exposto (quem alcanca este AP esta a poucos metros do
//: robo), e uma senha secreta aqui so serviria para trancar o dono do lado
//: de fora no dia em que ele precisar.
//: Nome no mDNS: o corpo atende em `orion-corpo.local` alem do IP.
constexpr const char* NOME_MDNS = "orion-corpo";

constexpr const char* AP_SSID = "orion-corpo";
constexpr const char* AP_SENHA = "fofao1234";

class WifiCorpo {
 public:
  //: Conecta na rede guardada; se nao der, abre o portal. Devolve true se
  //: entrou numa rede de verdade (modo estacao).
  bool iniciar() {
    _prefs.begin("wifi", /*readOnly=*/false);
    String ssid = _prefs.getString("ssid", "");
    String senha = _prefs.getString("senha", "");

    if (ssid.isEmpty()) {
      // Primeiro boot depois de gravar: cai no padrao de fabrica.
      ssid = WIFI_SSID;
      senha = WIFI_SENHA;
      Serial.println("[WIFI] NVS vazia - usando a rede de fabrica");
    }

    if (_conectar(ssid, senha)) return true;

    Serial.println("[WIFI] nao conectou - abrindo o portal");
    _abrirPortal();
    return false;
  }

  //: Guarda uma rede nova e tenta usar AGORA. Se a nova falhar, volta para a
  //: anterior - senao um erro de digitacao deixaria o robo inalcancavel, e
  //: so o portal (ou um cabo) o traria de volta.
  bool trocarRede(const String& ssid, const String& senha) {
    if (ssid.isEmpty()) return false;

    const String ssidAntigo = _prefs.getString("ssid", WIFI_SSID);
    const String senhaAntiga = _prefs.getString("senha", WIFI_SENHA);

    if (_conectar(ssid, senha)) {
      _prefs.putString("ssid", ssid);
      _prefs.putString("senha", senha);
      Serial.printf("[WIFI] rede trocada para %s\n", ssid.c_str());
      return true;
    }

    Serial.printf("[WIFI] %s nao respondeu - voltando para %s\n",
                  ssid.c_str(), ssidAntigo.c_str());
    _conectar(ssidAntigo, senhaAntiga);
    return false;
  }

  //: Chamar no loop(): serve o portal quando ele esta aberto. Fora do
  //: portal nao custa nada.
  void atualizar() {
    if (_portalAberto) _servidorWeb.handleClient();
  }

  bool portalAberto() const { return _portalAberto; }
  String ssidAtual() const { return WiFi.isConnected() ? WiFi.SSID() : String(AP_SSID); }

 private:
  Preferences _prefs;
  WebServer _servidorWeb{80};
  bool _portalAberto = false;

  bool _conectar(const String& ssid, const String& senha) {
    if (ssid.isEmpty()) return false;
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), senha.c_str());
    Serial.printf("[WIFI] tentando %s", ssid.c_str());

    const unsigned long limite = millis() + TIMEOUT_CONEXAO_MS;
    while (WiFi.status() != WL_CONNECTED && millis() < limite) {
      delay(250);
      Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() != WL_CONNECTED) return false;
    WiFi.setAutoReconnect(true);
    Serial.printf("[WIFI] ok! IP: %s\n", WiFi.localIP().toString().c_str());

    // NOME, e nao so IP. O IP vem do DHCP e JA MUDOU uma vez (.112 -> .102
    // na primeira regravacao), derrubando o enlace com o Pi ate alguem
    // editar o orion.yaml a mao. Com isto o Pi pode procurar
    // `orion-corpo.local` e nao se importar com o numero.
    if (MDNS.begin(NOME_MDNS)) {
      MDNS.addService("ws", "tcp", WS_PORTA);
      Serial.printf("[WIFI] tambem atende em %s.local\n", NOME_MDNS);
    } else {
      Serial.println("[WIFI] mDNS falhou - so o IP vale");
    }
    return true;
  }

  void _abrirPortal() {
    WiFi.mode(WIFI_AP);
    WiFi.softAP(AP_SSID, AP_SENHA);
    Serial.printf("[PORTAL] rede '%s' (senha %s) - abra http://%s\n",
                  AP_SSID, AP_SENHA, WiFi.softAPIP().toString().c_str());

    _servidorWeb.on("/", [this]() { _servirFormulario(); });
    _servidorWeb.on("/salvar", [this]() { _salvarDoFormulario(); });
    _servidorWeb.begin();
    _portalAberto = true;
  }

  void _servirFormulario() {
    // Lista o que o robo esta VENDO: digitar o nome da rede a mao erra
    // acento, maiuscula e espaco - e o erro so aparece no reboot seguinte.
    const int achadas = WiFi.scanNetworks();
    String html =
        "<!doctype html><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Fofao - WiFi</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:30rem}"
        "select,input,button{width:100%;padding:.6rem;margin:.3rem 0;"
        "font-size:1rem}button{background:#2F7357;color:#fff;border:0}</style>"
        "<h2>Rede do Fofao</h2><form action=/salvar>"
        "<label>Rede</label><select name=ssid>";
    for (int i = 0; i < achadas; i++) {
      html += "<option>" + WiFi.SSID(i) + "</option>";
    }
    html +=
        "</select><label>Senha</label>"
        "<input name=senha type=password>"
        "<button type=submit>Conectar</button></form>";
    _servidorWeb.send(200, "text/html; charset=utf-8", html);
  }

  void _salvarDoFormulario() {
    const String ssid = _servidorWeb.arg("ssid");
    const String senha = _servidorWeb.arg("senha");
    _prefs.putString("ssid", ssid);
    _prefs.putString("senha", senha);
    _servidorWeb.send(200, "text/html; charset=utf-8",
                      "<!doctype html><meta charset=utf-8>"
                      "<p>Guardado. Reiniciando para entrar na rede...</p>");
    delay(800);
    ESP.restart();  // caminho mais simples e confiavel que trocar de modo a quente
  }
};

}  // namespace corpo
