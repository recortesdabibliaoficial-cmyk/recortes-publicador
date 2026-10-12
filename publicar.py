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
METRICAS = os.path.join(AQUI, 'metricas.json')
METRICAS_CADA = timedelta(hours=1)   # intervalo de leitura das métricas (folga no limite da API)
COMENTARIOS = os.path.join(AQUI, 'comentarios.json')  # de quanto em quanto tempo lê as métricas no Instagram
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

    def publicar(self, cid):
        """Publica o container; se o Instagram disser que a mídia ainda não está pronta, espera e tenta de novo."""
        for tentativa in range(5):
            try:
                return self._req('POST', f'{self.u}/media_publish', creation_id=cid)['id']
            except RuntimeError as e:
                if 'not ready' not in str(e) and '2207027' not in str(e) or tentativa == 4: raise
                time.sleep(20)

    def link(self, mid):
        try: return self._req('GET', mid, fields='permalink').get('permalink')
        except Exception: return None

    def recentes(self):
        campos = 'id,timestamp,media_type,media_product_type,permalink'
        itens = self._req('GET', f'{self.u}/media', fields=campos, limit=50).get('data', [])
        try: itens += [dict(s, media_product_type='STORY') for s in self._req('GET', f'{self.u}/stories', fields=campos).get('data', [])]
        except Exception: pass
        return itens


def conferir_externos(ig, agenda, estado):
    """Itens agendados fora do publicador (Metricool): confere no Instagram se já saíram e marca como publicados."""
    agora = datetime.now(timezone.utc)
    faltam = [i for i in agenda if i.get('via') == 'metricool' and estado.get(i['id'], {}).get('status') != 'publicado'
              and agora - timedelta(days=3) <= datetime.fromisoformat(i['quando']) <= agora]
    if not faltam: return
    try: midias = ig.recentes()
    except Exception as e: log('AVISO: não consegui conferir as publicações do Metricool:', e); return
    tipo_ig = {'CARROSSEL': lambda m: m.get('media_type') == 'CAROUSEL_ALBUM', 'REEL': lambda m: m.get('media_product_type') == 'REELS',
               'STORY': lambda m: m.get('media_product_type') == 'STORY'}
    usados = {v.get('media_id') for v in estado.values() if isinstance(v, dict)}
    for item in faltam:
        q = datetime.fromisoformat(item['quando'])
        for m in midias:
            ts = datetime.fromisoformat(m['timestamp'])
            if m['id'] not in usados and tipo_ig[item['tipo']](m) and q - timedelta(minutes=10) <= ts <= q + timedelta(hours=2):
                estado[item['id']] = {'status': 'publicado', 'media_id': m['id'], 'em': ts.isoformat(), 'link': m.get('permalink'), 'via': 'metricool'}
                usados.add(m['id']); log('CONFERIDO (Metricool):', item['id'], m.get('permalink')); break


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


# métricas pedidas ao Instagram por tipo de publicação (as que não existirem para um item são ignoradas)
CAMPOS = {'CARROSSEL': ['reach', 'views', 'likes', 'comments', 'saved', 'shares', 'total_interactions'],
          'REEL': ['reach', 'views', 'likes', 'comments', 'saved', 'shares', 'total_interactions', 'ig_reels_avg_watch_time'],
          'STORY': ['reach', 'views', 'replies', 'shares', 'total_interactions']}


def _insights(ig, mid, campos):
    try:
        dados = ig._req('GET', f'{mid}/insights', metric=','.join(campos)).get('data', [])
    except Exception:
        if len(campos) == 1: return {}
        out = {}                                                        # algum campo não existe para este item: tenta um a um
        for c in campos: out.update(_insights(ig, mid, [c]))
        return out
    out = {}
    for d in dados:
        v = d.get('total_value', {}).get('value') if 'total_value' in d else (d.get('values') or [{}])[-1].get('value')
        if isinstance(v, (int, float)): out[d['name']] = v
    return out


def coletar_metricas(ig, agenda, estado):
    """Lê no Instagram as métricas dos itens publicados nos últimos 60 dias e o número de seguidores. Nunca derruba a execução."""
    met = carregar(METRICAS, {})
    agora = datetime.now(timezone.utc)
    if met.get('_em') and datetime.fromisoformat(met['_em']) > agora - METRICAS_CADA: return
    tipos = {i['id']: i['tipo'] for i in agenda}
    try:
        conta = ig._req('GET', 'me', fields='followers_count,media_count')
        hist = met.setdefault('_seguidores', [])
        dia = agora.date().isoformat()
        if hist and hist[-1]['dia'] == dia: hist[-1]['n'] = conta.get('followers_count')
        else: hist.append({'dia': dia, 'n': conta.get('followers_count')})
    except Exception as e:
        log('AVISO métricas (conta):', str(e)[:200])
    lidos = 0
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id') or iid not in tipos: continue
        quando = datetime.fromisoformat(reg.get('em', agora.isoformat()))
        if quando < agora - timedelta(days=60): continue
        if tipos[iid] == 'STORY' and quando < agora - timedelta(hours=24) and iid in met: continue   # Story some em 24 h: guarda a última leitura
        m = _insights(ig, reg['media_id'], CAMPOS.get(tipos[iid], ['reach']))
        if m: met[iid] = dict(m, lido_em=agora.isoformat()); lidos += 1
    met['_em'] = agora.isoformat()
    salvar(METRICAS, met); log(f'Métricas lidas: {lidos} itens.')


def coletar_comentarios(ig, estado, conta):
    """Comentários dos últimos 14 dias que ainda não têm resposta da própria conta (para o checklist da página)."""
    agora = datetime.now(timezone.utc); eu = (conta or {}).get('username', '')
    if estado.get('_comentarios_em') and datetime.fromisoformat(estado['_comentarios_em']) > agora - timedelta(minutes=30): return
    estado['_comentarios_em'] = agora.isoformat()
    pend = []
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id'): continue
        if datetime.fromisoformat(reg.get('em', agora.isoformat())) < agora - timedelta(days=14): continue
        try:
            cs = ig._req('GET', f"{reg['media_id']}/comments", fields='id,text,username,timestamp,replies{username}', limit=50).get('data', [])
        except Exception as e:
            if not iid.startswith(('story', 'comeceaqui', 'oracao', 'mc-oracao')): estado['_comentarios_erro'] = str(e)[:300]
            continue                                                  # Story não tem comentário; outros erros: tenta na próxima
        for c in cs:
            if c.get('username') == eu: continue
            if any(r.get('username') == eu for r in (c.get('replies') or {}).get('data', [])): continue
            pend.append({'id': c['id'], 'item': iid, 'usuario': c.get('username'), 'texto': c.get('text', '')[:300],
                         'quando': c.get('timestamp'), 'link': reg.get('link')})
    if pend or '_comentarios_erro' not in estado: pass
    salvar(COMENTARIOS, sorted(pend, key=lambda c: c.get('quando') or '')); log(f'Comentários sem resposta: {len(pend)}.')


def main():
    teste = '--teste' in sys.argv
    token, user = os.environ.get('IG_TOKEN'), os.environ.get('IG_USER_ID')
    if not token or not user:
        log('ERRO: faltam os segredos IG_TOKEN e/ou IG_USER_ID.'); sys.exit(1)
    agenda = carregar(AGENDA, [])
    estado = carregar(ESTADO, {})
    ig = IG(token, user)
    conta = ig.conta(); log('Conta:', conta)
    agora = datetime.now(timezone.utc)
    conferir_externos(ig, agenda, estado); salvar(ESTADO, estado)
    try: coletar_metricas(ig, agenda, estado)
    except Exception as e: log('AVISO: não consegui ler as métricas:', str(e)[:300])
    try: coletar_comentarios(ig, estado, conta)
    except Exception as e: log('AVISO: não consegui ler os comentários:', str(e)[:300])
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
            reg.update(status='publicado', media_id=mid, em=datetime.now(timezone.utc).isoformat(), link=ig.link(mid))
            reg.pop('ultimo_erro', None)
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
