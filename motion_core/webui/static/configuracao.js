// Fofão — Configuração (Cap 13 s.4: "parâmetros do sistema, acesso
// restrito"). Somente leitura por ora - editar configuração ao vivo fica
// para uma iteração futura (precisa de validação/confirmação de reinício).

const $ = (id) => document.getElementById(id);
const conteudoEl = $('config-conteudo');

// Formulario de senha, montado so quando a API recusa (403). A pagina em si
// e servida para todo mundo; o que e protegido sao os DADOS - antes o 403
// vinha antes do HTML e nao havia onde pedir a senha.
function pedirSenha(mensagem) {
  conteudoEl.textContent = '';
  const form = document.createElement('form');
  form.className = 'form-senha';
  form.innerHTML = `
    <p></p>
    <input type="password" id="campo-senha" autocomplete="current-password"
           placeholder="senha" required />
    <button type="submit">Entrar</button>
    <p class="erro-senha" id="erro-senha"></p>`;
  form.querySelector('p').textContent = mensagem;

  form.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const erroEl = $('erro-senha');
    erroEl.textContent = '';
    const resposta = await fetch('/api/configuracao/entrar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ senha: $('campo-senha').value }),
    });
    if (resposta.ok) {
      carregar();  // o cookie de sessao ja veio na resposta
      return;
    }
    const corpo = await resposta.json().catch(() => ({}));
    erroEl.textContent = corpo.erro || 'não foi possível entrar';
  });

  conteudoEl.appendChild(form);
}

async function carregar() {
  const resposta = await fetch('/api/configuracao');
  if (resposta.status === 403) {
    pedirSenha('Página restrita. Informe a senha para ver a configuração.');
    return;
  }
  const corpo = await resposta.json();
  $('robot-name').textContent = corpo.parametros?.system?.robot_name
    ? `— ${corpo.parametros.system.robot_name}`
    : '';
  conteudoEl.textContent = corpo.aviso || JSON.stringify(corpo.parametros, null, 2);
}

carregar();
