# Configurar o publicador automático (uma vez só, uns 40 minutos)

O publicador roda de graça no GitHub, a cada 15 minutos, e publica sozinho os posts, Reels e Stories da `agenda.json` pela API oficial do Instagram. Você só faz as partes que exigem login e senha; o resto é com o Claude.

## Parte 1 · Conta e repositório no GitHub (10 min)

1. Entre em **github.com** → **Sign up**. Use o Gmail do canal (recortesdabiblia.oficial@gmail.com). Escolha um nome de usuário (ex.: `recortesdabiblia`).
2. No canto superior direito: **+** → **New repository**.
   - Repository name: `recortes-publicador`
   - Marque **Public** (precisa ser público para o Instagram conseguir baixar as imagens; só tem slides e o programa, nada secreto).
   - **Não** marque "Add a README".
   - **Create repository**.
3. Avise o Claude o seu nome de usuário. Ele envia os arquivos (vai abrir uma janela do navegador pedindo para você entrar no GitHub: entre e autorize).

## Parte 2 · Ligar o "site" das imagens (2 min, depois que o Claude enviar os arquivos)

No repositório: **Settings** → **Pages** → em "Branch" escolha **main** e **/(root)** → **Save**. Em 1 a 2 minutos aparece o endereço `https://SEU-USUARIO.github.io/recortes-publicador`.

## Parte 3 · Chave do Instagram (20 min)

1. Entre em **developers.facebook.com** (com o seu Facebook pessoal; é só para ser o "desenvolvedor"). Se for a primeira vez, aceite virar desenvolvedor.
2. **Meus apps** → **Criar app** → caso de uso: **"Gerenciar mensagens e conteúdo no Instagram"** → dê um nome (ex.: `Recortes Publicador`) → criar.
3. No painel do app, abra **"Configuração da API com login do Instagram"** (API setup with Instagram login).
4. Em **"Gerar tokens de acesso"** → **Adicionar conta** → entre com o Instagram **@recortesdabiblia.oficial** e autorize.
   - Se pedir, aceite o convite de testador no app do Instagram: **Configurações → Apps e sites → Convites de testador**.
5. Clique em **Gerar token** ao lado da conta. Copie:
   - o **token** (texto comprido) e
   - o **ID da conta do Instagram** (número que aparece junto da conta).

## Parte 4 · Guardar as chaves no GitHub (5 min)

No repositório: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Crie:

| Nome | Valor |
|---|---|
| `IG_TOKEN` | o token do Instagram |
| `IG_USER_ID` | o ID da conta do Instagram |
| `GH_PAT` | (recomendado) token do GitHub para renovar a chave sozinho, veja abaixo |

**Como criar o GH_PAT:** foto do perfil → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token** → Repository access: **Only select repositories** → `recortes-publicador` → Permissions → Repository permissions → **Secrets: Read and write** → gerar → copie e salve como o segredo `GH_PAT`.

Sem o GH_PAT o publicador funciona, mas a chave do Instagram vence em 60 dias e você teria que gerar outra.

## Parte 5 · Testar (1 min)

No repositório: **Actions** → **Publicar no Instagram** → **Run workflow**. Em 1 minuto deve aparecer, no registro, `Conta: ... recortesdabiblia.oficial` e quantos itens estão na fila. Pronto: a partir daí ele publica sozinho nos horários.

Se algo falhar, o GitHub manda um e-mail e a execução fica vermelha. É só avisar o Claude.

**Nunca cole o token ou senhas no chat.** Eles vão só nos "Secrets" do GitHub.
