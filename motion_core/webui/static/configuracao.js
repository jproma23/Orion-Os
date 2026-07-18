// Fofão — Configuração (Cap 13 s.4: "parâmetros do sistema, acesso
// restrito"). Somente leitura por ora - editar configuração ao vivo fica
// para uma iteração futura (precisa de validação/confirmação de reinício).

const $ = (id) => document.getElementById(id);
const conteudoEl = $('config-conteudo');

async function carregar() {
  const resposta = await fetch('/api/configuracao');
  if (resposta.status === 403) {
    conteudoEl.textContent = 'Acesso negado: esta página só responde a pedidos feitos a partir do próprio Raspberry.';
    return;
  }
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.parametros?.system?.robot_name
    ? `— ${corpo.parametros.system.robot_name}`
    : '';
  conteudoEl.textContent = corpo.aviso || JSON.stringify(corpo.parametros, null, 2);
}

carregar();
