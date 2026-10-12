"""Publicador automático do @recortesdabiblia.oficial pela API oficial do Instagram (Instagram Login).

Roda no GitHub Actions a cada 15 minutos:
  - lê agenda.json (o que publicar e quando) e estado.json (o que já foi publicado);
  - publica o que já chegou na hora (carrossel, Reel ou Story);
  - renova a chave de acesso a cada 20 dias e salva a nova chave no segredo do GitHub.

Segredos (GitHub → Settings → Secrets and variables → Actions):
  IG_TOKEN    chave de acesso do Instagram (Instagram User access token, longa duração)
  IG_USER_ID  ID da conta do Instagram
  GH_PAT      (opcional, recomendado) token do GitHub com permissão "Secrets: read and write" neste repositório,
              usado só para guardar a chave renovada.

Uso local para teste:  python publicar.py --teste   (só confere a chave e mostra o que está na fila)
"""
import base64, json, os, sys, time
from datetime import datetime, timedelta, timezone
import requests

API = 'https://graph.instagram.com/v25.0'
AQUI = os.path.dirname(os.path.abspath(__file__))
AGENDA = os.path.join(AQUI, 'agenda.json')
ESTADO = os.path.join(AQUI, 'estado.json')
JANELA = timedelta(hours=12)        # item atrasado mais que isso não é publicado (evita publicar coisa velha de surpresa)
MAX_TENTATIVAS = 3


def log(*a): print(*a, flush=True)


def carregar(caminho, padrao):
    try:
        with open(caminho, encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        return padrao


def salvar(caminho, dados):
    with open(caminho, 'w', encoding='utf-8') as f: json.dump(dados, f, ensure_ascii=False, indent=1)


def base_midia():
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if os.environ.get('BASE_MIDIA'): return os.environ['BASE_MIDIA'].rstrip('/')
    dono, nome = repo.split('/')
    return f'https://{dono.lower()}.github.io/{nome}'


class IG:
    def __init__(self, token, user_id):
        self.t, self.u = token, user_id

    def _req(self, metodo, caminho, **params):
        params['access_token'] = self.t
        r = requests.request(metodo, f'{API}/{caminho}', data=params if metodo == 'POST' else None,
                             params=params if metodo == 'GET' else None, timeout=60)
        dados = r.json()
        if r.status_code != 200 or 'error' in dados:
            raise RuntimeError(f'{caminho}: {dados.get("error", dados)}')
        return dados

    def conta(self): return self._req('GET', 'me', fields='user_id,username')

    def container(self, **p): return self._req('POST', f'{self.u}/media', **p)['id']

    def esperar(self, cid, limite=600):
        fim = time.time() + limite
        while time.time() < fim:
            s = self._req('GET', cid, fields='status_code,status')
            if s.get('status_code') == 'FINISHED': return
            if s.get('status_code') in ('ERROR', 'EXPIRED'): raise RuntimeError(f'container {cid}: {s}')
            time.sleep(15)
        raise RuntimeError(f'container {cid}: demorou demais para processar')

    def publicar(self, cid): return self._req('POST', f'{self.u}/media_publish', creation_id=cid)['id']


def publicar_item(ig, item, base):
    url = lambda arq: f'{base}/{arq}'
    tipo = item['tipo']
    if tipo == 'CARROSSEL':
        filhos = [ig.container(image_url=url(a), is_carousel_item='true') for a in item['midia']]
        for f in filhos: ig.esperar(f, 120)
        cid = ig.container(media_type='CAROUSEL', children=','.join(filhos), caption=item['legenda'])
    elif tipo == 'REEL':
        cid = ig.container(media_type='REELS', video_url=url(item['midia'][0]), caption=item['legenda'],
                           share_to_feed='true' if item.get('na_grade') else 'false', thumb_offset=str(item.get('capa_ms', 800)))
    elif tipo == 'STORY':
        cid = ig.container(media_type='STORIES', image_url=url(item['midia'][0]))
    else:
        raise ValueError(f'tipo desconhecido: {tipo}')
    ig.esperar(cid)
    return ig.publicar(cid)


def renovar_token(token, estado):
    """Renova a chave a cada 20 dias (ela vale 60). Guarda a nova no segredo IG_TOKEN se houver GH_PAT."""
    ultima = estado.get('_token_renovado_em')
    if ultima and datetime.fromisoformat(ultima) > datetime.now(timezone.utc) - timedelta(days=20):
        return token
    r = requests.get('https://graph.instagram.com/refresh_access_token',
                     params={'grant_type': 'ig_refresh_token', 'access_token': token}, timeout=60).json()
    if 'access_token' not in r:
        log('AVISO: não consegui renovar a chave:', r); return token
    novo = r['access_token']
    pat, repo = os.environ.get('GH_PAT'), os.environ.get('GITHUB_REPOSITORY')
    if pat and repo:
        from nacl import encoding, public
        h = {'Authorization': f'Bearer {pat}', 'Accept': 'application/vnd.github+json'}
        k = requests.get(f'https://api.github.com/repos/{repo}/actions/secrets/public-key', headers=h, timeout=60).json()
        caixa = public.SealedBox(public.PublicKey(k['key'].encode(), encoding.Base64Encoder()))
        cifrado = base64.b64encode(caixa.encrypt(novo.encode())).decode()
        s = requests.put(f'https://api.github.com/repos/{repo}/actions/secrets/IG_TOKEN', headers=h, timeout=60,
                         json={'encrypted_value': cifrado, 'key_id': k['key_id']})
        if s.status_code in (201, 204):
            log('Chave renovada e salva no segredo IG_TOKEN.')
            estado['_token_renovado_em'] = datetime.now(timezone.utc).isoformat()
        else:
            log('AVISO: chave renovada, mas não consegui salvar no GitHub:', s.status_code, s.text[:200])
    else:
        log('AVISO: chave renovada só nesta execução (falta o segredo GH_PAT para guardar a nova).')
        estado['_token_renovado_em'] = datetime.now(timezone.utc).isoformat()
    return novo


def main():
    teste = '--teste' in sys.argv
    token, user = os.environ.get('IG_TOKEN'), os.environ.get('IG_USER_ID')
    if not token or not user:
        log('ERRO: faltam os segredos IG_TOKEN e/ou IG_USER_ID.'); sys.exit(1)
    agenda = carregar(AGENDA, [])
    estado = carregar(ESTADO, {})
    ig = IG(token, user)
    log('Conta:', ig.conta())
    agora = datetime.now(timezone.utc)
    agenda = [i for i in agenda if i.get('via', 'api') == 'api']          # itens do Metricool só aparecem na página
    pendentes = [i for i in agenda if estado.get(i['id'], {}).get('status') != 'publicado']
    devidos = [i for i in pendentes if agora - JANELA <= datetime.fromisoformat(i['quando']) <= agora]
    log(f'{len(pendentes)} pendentes, {len(devidos)} na hora de publicar.')
    if teste:
        for i in sorted(pendentes, key=lambda i: i['quando'])[:10]: log(' ', i['quando'], i['tipo'], i['id'])
        return
    ig.t = renovar_token(token, estado)
    falhou = False
    for item in sorted(devidos, key=lambda i: i['quando']):
        reg = estado.setdefault(item['id'], {'tentativas': 0})
        if reg.get('tentativas', 0) >= MAX_TENTATIVAS:
            log('PULADO (tentativas esgotadas):', item['id']); continue
        try:
            mid = publicar_item(ig, item, base_midia())
            reg.update(status='publicado', media_id=mid, em=datetime.now(timezone.utc).isoformat())
            log('PUBLICADO:', item['id'], mid)
        except Exception as e:
            reg['tentativas'] = reg.get('tentativas', 0) + 1
            reg['ultimo_erro'] = str(e)[:500]
            log('ERRO ao publicar', item['id'], '->', e); falhou = True
        salvar(ESTADO, estado)
    salvar(ESTADO, estado)
    if falhou: sys.exit(1)       # deixa a execução vermelha no GitHub (e manda e-mail de aviso)


if __name__ == '__main__':
    main()
