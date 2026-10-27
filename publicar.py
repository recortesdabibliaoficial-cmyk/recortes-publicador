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
METRICAS_CADA = timedelta(hours=12)   # Felipe, 20/10: leitura geral a cada 12 h; Stories a cada 2 h e curvas dos Reels de hora em hora continuam
COMENTARIOS = os.path.join(AQUI, 'comentarios.json')  # de quanto em quanto tempo lê as métricas no Instagram
HISTORICO = os.path.join(AQUI, 'historico.json')    # um retrato dos números por dia (base dos relatórios)
BRT = timezone(timedelta(hours=-3))                 # horário de Brasília (sem horário de verão)
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
              and agora - timedelta(hours=6) <= datetime.fromisoformat(i['quando']) <= agora]
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


def ja_publicado(ig, item, desde, estado=None):
    """Procura no Instagram se este item já está no ar (evita post repetido quando uma resposta de erro chega mas o post sai)."""
    try:
        if item['tipo'] == 'STORY':
            ms = ig._req('GET', f'{ig.u}/stories', fields='id,timestamp').get('data', [])
            ok = lambda m: True
        else:
            ms = ig._req('GET', f'{ig.u}/media', fields='id,timestamp,caption,media_product_type', limit=10).get('data', [])
            leg = (item.get('legenda') or '')[:80]
            ok = lambda m: (m.get('caption') or '')[:80] == leg and leg != ''
        usados = {v.get('media_id') for v in (estado or {}).values() if isinstance(v, dict)}
        if item['tipo'] == 'STORY': desde = max(desde, datetime.fromisoformat(item['quando']) - timedelta(minutes=2))
        for m in ms:
            if m['id'] not in usados and datetime.fromisoformat(m['timestamp']) >= desde and ok(m): return m['id']
    except Exception as e:
        log('AVISO: não consegui conferir se já saiu:', str(e)[:200])
    return None


def publicar_item(ig, item, base):
    url = lambda arq: f'{base}/{arq}'
    tipo = item['tipo']
    if tipo == 'CARROSSEL':
        alts = item.get('alt') or []

        def filho(i, a):
            pars = dict(image_url=url(a), is_carousel_item='true')
            if i < len(alts) and alts[i]:                     # texto alternativo (acessibilidade e busca); se o Instagram recusar, segue sem
                try: return ig.container(**pars, alt_text=alts[i][:900])
                except RuntimeError as e: log('AVISO: texto alternativo recusado, publico sem ele:', str(e)[:150])
            return ig.container(**pars)
        filhos = [filho(i, a) for i, a in enumerate(item['midia'])]
        for f in filhos: ig.esperar(f, 120)
        cid = ig.container(media_type='CAROUSEL', children=','.join(filhos), caption=item['legenda'])
    elif tipo == 'REEL':
        base_p = dict(media_type='REELS', video_url=url(item['midia'][0]), caption=item['legenda'],
                      share_to_feed='true' if item.get('na_grade') else 'false', thumb_offset=str(item.get('capa_ms', 800)))
        if item.get('capa'):                  # capa própria (imagem 9:16) em vez de um quadro do vídeo
            base_p.pop('thumb_offset', None); base_p['cover_url'] = url(item['capa'])
        cid = None
        if item.get('teste'):   # Reel de teste: vai só para quem não segue; o Instagram libera para todos se for bem (estratégia de 15/10/2026)
            try:
                cid = ig.container(**base_p, trial_params=json.dumps({'graduation_strategy': 'SS_PERFORMANCE'}))
                log('Reel de teste (só para quem não segue):', item['id'])
            except RuntimeError as e:
                if '2207081' not in str(e): raise
                log('Conta ainda sem seguidores para Reel de teste (Instagram pede cerca de 1.000): publico como Reel normal.')
        if cid is None: cid = ig.container(**base_p)
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
# 27/10: 'reposts' saiu (nunca veio em 150 itens e derrubava a consulta inteira, que virava 10 consultas, uma por campo)
CAMPOS = {'CARROSSEL': ['reach', 'views', 'likes', 'comments', 'saved', 'shares', 'total_interactions', 'follows', 'profile_visits'],
          'REEL': ['reach', 'views', 'likes', 'comments', 'saved', 'shares', 'total_interactions', 'ig_reels_avg_watch_time', 'reels_skip_rate', 'ig_reels_video_view_total_time'],
          'STORY': ['reach', 'views', 'replies', 'shares', 'total_interactions', 'follows', 'profile_visits']}
_RECUSADOS = set()      # campos que o Instagram disse que não existem (nesta execução): não pergunta de novo
FORA = ('does not exist', 'Unsupported get request', 'cannot be loaded')   # resposta do Instagram para post arquivado ou apagado


class ForaDoAr(Exception): pass


def fora_do_ar(reg, agora):
    """27/10: post arquivado/apagado não é consultado de novo por 7 dias (cada tentativa gastava ~11 consultas). Depois de 7 dias, confere de novo."""
    f = reg.get('fora_do_ar_em')
    return bool(f) and datetime.fromisoformat(f) > agora - timedelta(days=7)


def _ler(ig, reg, campos, agora):
    """Lê as métricas de um post; se ele saiu do ar, marca no estado (fora_do_ar_em) e devolve {}."""
    try:
        m = _insights(ig, reg['media_id'], campos)
    except ForaDoAr:
        reg['fora_do_ar_em'] = agora.isoformat(); log('Post fora do ar (arquivado ou apagado):', reg.get('link')); return {}
    reg.pop('fora_do_ar_em', None); return m


def _insights(ig, mid, campos):
    campos = [c for c in campos if c not in _RECUSADOS]
    if not campos: return {}
    try:
        dados = ig._req('GET', f'{mid}/insights', metric=','.join(campos)).get('data', [])
    except Exception as e:
        if any(x in str(e) for x in FORA): raise ForaDoAr(str(e)[:200])
        if len(campos) == 1:
            if 'must be one of' in str(e) or 'not supported' in str(e).lower(): log('AVISO campo recusado:', campos[0])
            return {}
        out = {}                                                        # algum campo não existe para este item: tenta um a um
        for c in campos: out.update(_insights(ig, mid, [c]))
        return out
    out = {}
    for d in dados:
        v = d.get('total_value', {}).get('value') if 'total_value' in d else (d.get('values') or [{}])[-1].get('value')
        if isinstance(v, (int, float)): out[d['name']] = v
    return out


def _nav(ig, mid):
    """Navegação do Story (toque p/ avançar, voltar, sair, deslizar). Uma consulta; nunca falha."""
    try:
        d = ig._req('GET', f'{mid}/insights', metric='navigation', metric_type='total_value',
                    breakdown='story_navigation_action_type').get('data', [])
        bks = ((d[0].get('total_value') or {}).get('breakdowns') or []) if d else []
        return {'/'.join(x['dimension_values']): x['value'] for b in bks for x in b.get('results', [])}
    except Exception as e:
        log('AVISO nav Story', str(e)[:120]); return {}


def coletar_stories(ig, agenda, estado):
    """Stories somem em 24 h: relê as métricas (e a navegação) dos Stories das últimas 24 h, no máximo a cada 2 h por item."""
    tipos = {i['id']: i['tipo'] for i in agenda}
    agora = datetime.now(timezone.utc)
    met = carregar(METRICAS, {}); lidos = 0
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id') or tipos.get(iid) != 'STORY': continue
        quando = datetime.fromisoformat(reg.get('em', agora.isoformat()))
        if quando.tzinfo is None: quando = quando.replace(tzinfo=timezone.utc)
        if quando < agora - timedelta(hours=24): continue
        ult = (met.get(iid) or {}).get('lido_em')
        if ult and datetime.fromisoformat(ult) > agora - timedelta(hours=2): continue
        if fora_do_ar(reg, agora): continue
        m = _ler(ig, reg, CAMPOS['STORY'], agora)
        if m:
            n = _nav(ig, reg['media_id'])
            if n: m['nav'] = n
            met[iid] = dict(m, lido_em=agora.isoformat()); lidos += 1
    if lidos: salvar(METRICAS, met); log(f'Stories relidos: {lidos}.')


def registrar_seg_hora(ig, met, agora, n=None):
    """Seguidores por hora (Felipe, 23/10): se a última leitura horária tem mais de 55 min, grava {t (hora do Rio), n} em met['_seg_hora']
    (últimos 14 dias). Usa n já lido, se houver; senão faz UMA chamada me?fields=followers_count. Nunca derruba a execução. Devolve True se gravou."""
    try:
        lista = met.setdefault('_seg_hora', [])
        if lista and datetime.fromisoformat(lista[-1]['t']) > agora - timedelta(minutes=55): return False
        if n is None: n = ig._req('GET', 'me', fields='followers_count').get('followers_count')
        if not isinstance(n, int): return False
        lista.append({'t': agora.astimezone(BRT).isoformat(timespec='minutes'), 'n': n})
        corte = agora - timedelta(days=14)
        met['_seg_hora'] = [x for x in lista if datetime.fromisoformat(x['t']) > corte]
        return True
    except Exception as e:
        log('AVISO seguidores por hora:', str(e)[:200]); return False


def coletar_metricas(ig, agenda, estado):
    """Lê no Instagram as métricas dos itens publicados nos últimos 60 dias e o número de seguidores. Nunca derruba a execução."""
    met = carregar(METRICAS, {})
    agora = datetime.now(timezone.utc)
    local = agora.astimezone(BRT); dia_local = local.date().isoformat()
    fechamento = local.hour == 23 and local.minute >= 30 and met.get('_fechado') != dia_local   # leitura final do dia (relatório)
    forcado = os.environ.get('FORCAR_METRICAS') == 'sim' and not (            # botão "Atualizar agora" do painel (Felipe, 20/10)
        met.get('_forcado_em') and datetime.fromisoformat(met['_forcado_em']) > agora - timedelta(minutes=30))   # no máximo 1 vez a cada 30 min
    if forcado: met['_forcado_em'] = agora.isoformat(); log('Leitura forçada pelo botão Atualizar.')
    if not forcado and not fechamento and met.get('_em') and datetime.fromisoformat(met['_em']) > agora - METRICAS_CADA:
        if registrar_seg_hora(ig, met, agora): salvar(METRICAS, met)
        return
    tipos = {i['id']: i['tipo'] for i in agenda}
    conta = {}
    try:
        conta = ig._req('GET', 'me', fields='followers_count,media_count')
        hist = met.setdefault('_seguidores', [])
        dia = agora.date().isoformat()
        if hist and hist[-1]['dia'] == dia: hist[-1]['n'] = conta.get('followers_count')
        else: hist.append({'dia': dia, 'n': conta.get('followers_count')})
    except Exception as e:
        log('AVISO métricas (conta):', str(e)[:200])
    registrar_seg_hora(ig, met, agora, conta.get('followers_count'))
    lidos = 0
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id') or iid not in tipos: continue
        quando = datetime.fromisoformat(reg.get('em', agora.isoformat()))
        if quando < agora - timedelta(days=14):     # 27/10: de 14 a 60 dias, relê Reels e carrosséis 1 vez por dia (no fechamento)
            if not fechamento or tipos[iid] == 'STORY' or quando < agora - timedelta(days=60): continue
        if tipos[iid] == 'STORY' and quando < agora - timedelta(hours=24) and iid in met: continue   # Story some em 24 h: guarda a última leitura
        if fora_do_ar(reg, agora): continue
        m = _ler(ig, reg, CAMPOS.get(tipos[iid], ['reach']), agora)
        if m:
            if tipos[iid] == 'STORY':
                n = _nav(ig, reg['media_id'])
                if n: m['nav'] = n
            met[iid] = dict(m, lido_em=agora.isoformat()); lidos += 1
    met['_em'] = agora.isoformat(); met.pop('_erro_leitura', None)
    salvar(METRICAS, met); log(f'Métricas lidas: {lidos} itens.')
    guardar_historico(ig, met, conta, local, fechamento)
    if fechamento: met['_fechado'] = dia_local; salvar(METRICAS, met)


CURVAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'curvas.json')
CAMPOS_CURVA = ['reach', 'views', 'likes', 'shares', 'saved', 'reels_skip_rate', 'ig_reels_avg_watch_time']


def coletar_curvas(ig, agenda, estado):
    """Felipe, 20/10/2026: curva de cada Reel nas primeiras 24 h, lida de HORA EM HORA (para achar o horário de pico).
    Só Reels, só nas primeiras 24 h, no máximo 1 leitura por Reel por hora (~25 consultas por Reel). Grava em curvas.json."""
    tipos = {i['id']: i['tipo'] for i in agenda}
    agora = datetime.now(timezone.utc)
    cur = carregar(CURVAS, {}); lidos = 0
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id') or tipos.get(iid) != 'REEL': continue
        quando = datetime.fromisoformat(reg.get('em', agora.isoformat()))
        if quando.tzinfo is None: quando = quando.replace(tzinfo=timezone.utc)
        idade = agora - quando                                     # Felipe, 21/10: curva por 3 dias (Reel segue sendo entregue)
        if not (timedelta(0) <= idade <= timedelta(hours=72, minutes=30)): continue
        pts = cur.setdefault(iid, {'publicado': quando.isoformat(), 'pontos': []})['pontos']
        passo = timedelta(minutes=55) if idade <= timedelta(hours=24, minutes=30) else timedelta(minutes=175)   # 1º dia de hora em hora; depois a cada 3 h
        if pts and datetime.fromisoformat(pts[-1]['t']) > agora - passo: continue
        if fora_do_ar(reg, agora): continue
        m = _ler(ig, reg, CAMPOS_CURVA, agora)
        if m:
            pts.append(dict(m, t=agora.isoformat(timespec='minutes'), h=round((agora - quando).total_seconds() / 3600, 2))); lidos += 1
    if lidos: salvar(CURVAS, cur); log(f'Curvas: {lidos} Reels lidos (hora em hora).')


CONTA_DIA = ['reach', 'views', 'accounts_engaged', 'total_interactions', 'profile_views',
             'likes', 'comments', 'saves', 'shares', 'replies', 'reposts', 'profile_links_taps']


def guardar_historico(ig, met, conta, local, fechamento):
    """Guarda (ou atualiza) o retrato do dia em historico.json; na leitura final do dia lê também os números da conta."""
    try:
        hist = carregar(HISTORICO, {})
        dia = local.date().isoformat()
        reg = hist.setdefault(dia, {})
        reg['seguidores'] = conta.get('followers_count', reg.get('seguidores'))
        reg['publicacoes'] = conta.get('media_count', reg.get('publicacoes'))
        reg['itens'] = {k: {c: v for c, v in m.items() if c != 'lido_em'} for k, m in met.items()
                        if not k.startswith('_') and isinstance(m, dict)}
        reg['em'] = local.isoformat(timespec='minutes')
        if fechamento:
            ini = int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            dados = {}
            for c in CONTA_DIA:                     # um a um: se algum não existir para a conta, os outros continuam
                try:
                    d = ig._req('GET', f'{ig.u}/insights', metric=c, period='day', metric_type='total_value',
                                since=ini, until=int(local.timestamp())).get('data', [])
                    v = (d[0].get('total_value') or {}).get('value') if d else None
                    if isinstance(v, (int, float)): dados[c] = v
                except Exception as e:
                    log('AVISO conta/insights', c, str(e)[:150])
            reg['conta'] = dados
            quebras = {}                    # sonda de 17/10/2026: a conta aceita quebra por seguidor, tipo de conteúdo, idade, gênero e cidade
            seg100 = (conta or {}).get('followers_count', 0) >= 100      # idade/gênero/cidade só existem com 100+ seguidores
            lista = [('reach', 'follow_type'), ('views', 'follow_type'), ('views', 'follower_type'), ('views', 'media_product_type'),
                     ('total_interactions', 'media_product_type'), ('likes', 'media_product_type'), ('saves', 'media_product_type'),
                     ('shares', 'media_product_type'), ('comments', 'media_product_type'), ('reach', 'media_product_type'),
                     ('views', 'surface_type'), ('reach', 'surface_type')]          # surface_type = de onde veio (aba Reels, Explorar, feed...)
            if seg100: lista += [('reach', 'age'), ('reach', 'gender'), ('reach', 'city'), ('follows_and_unfollows', 'follow_type')]
            for metrica, quebra in lista:
                try:
                    d = ig._req('GET', f'{ig.u}/insights', metric=metrica, period='day', metric_type='total_value', breakdown=quebra,
                                since=ini, until=int(local.timestamp())).get('data', [])
                    bks = ((d[0].get('total_value') or {}).get('breakdowns') or []) if d else []
                    quebras[f'{metrica}:{quebra}'] = {'/'.join(x['dimension_values']): x['value'] for b in bks for x in b.get('results', [])}
                except Exception as e:
                    log('AVISO conta/quebra', metrica, quebra, str(e)[:120])
            reg['quebras'] = quebras
            if seg100:                      # públicos (período do mês) e horários dos seguidores: poucas consultas, só no fechamento
                demo = {}
                for metrica in ('follower_demographics', 'engaged_audience_demographics'):
                    for quebra in ('age', 'gender', 'city'):
                        try:
                            d = ig._req('GET', f'{ig.u}/insights', metric=metrica, period='lifetime', metric_type='total_value',
                                        timeframe='this_month', breakdown=quebra).get('data', [])
                            bks = ((d[0].get('total_value') or {}).get('breakdowns') or []) if d else []
                            demo[f'{metrica}:{quebra}'] = {'/'.join(x['dimension_values']): x['value'] for b in bks for x in b.get('results', [])}
                        except Exception as e:
                            log('AVISO conta/demografia', metrica, quebra, str(e)[:120])
                reg['demografia'] = demo
                try:
                    d = ig._req('GET', f'{ig.u}/insights', metric='online_followers', period='lifetime').get('data', [])
                    reg['online'] = (d[0].get('values') or [{}])[-1].get('value') if d else None
                except Exception as e:
                    log('AVISO conta/online_followers', str(e)[:120])
        salvar(HISTORICO, hist)
    except Exception as e:
        log('AVISO histórico:', str(e)[:200])


def coletar_comentarios(ig, estado, conta, agenda=()):
    """Comentários dos últimos 14 dias que ainda não têm resposta da própria conta (para o checklist da página)."""
    agora = datetime.now(timezone.utc); eu = (conta or {}).get('username', '')
    if estado.get('_comentarios_em') and datetime.fromisoformat(estado['_comentarios_em']) > agora - timedelta(hours=2): return
    estado['_comentarios_em'] = agora.isoformat()
    pend = []; stories = {i['id'] for i in agenda if i.get('tipo') == 'STORY'}; erro = None
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or not reg.get('media_id'): continue
        if datetime.fromisoformat(reg.get('em', agora.isoformat())) < agora - timedelta(days=7): continue
        if iid in stories or iid.startswith(('story', 'comeceaqui', 'oracao', 'mc-oracao')): continue     # Story não tem comentário: não gasta consulta
        if fora_do_ar(reg, agora): continue
        try:
            cs = ig._req('GET', f"{reg['media_id']}/comments", fields='id,text,username,timestamp,replies{username}', limit=50).get('data', [])
        except Exception as e:
            if any(x in str(e) for x in FORA): reg['fora_do_ar_em'] = agora.isoformat()
            else: erro = str(e)[:300]
            continue                                                  # outros erros: tenta na próxima
        for c in cs:
            if c.get('username') == eu: continue
            if any(r.get('username') == eu for r in (c.get('replies') or {}).get('data', [])): continue
            pend.append({'id': c['id'], 'item': iid, 'usuario': c.get('username'), 'texto': c.get('text', '')[:300],
                         'quando': c.get('timestamp'), 'link': reg.get('link')})
    if erro: estado['_comentarios_erro'] = erro
    else: estado.pop('_comentarios_erro', None)
    salvar(COMENTARIOS, sorted(pend, key=lambda c: c.get('quando') or '')); log(f'Comentários sem resposta: {len(pend)}.')


def coletar_tudo(ig, agenda, estado, conta):
    """Métricas, Stories, curvas e comentários. Cada parte falha sozinha, sem derrubar as outras."""
    try: coletar_metricas(ig, agenda, estado)
    except Exception as e:
        log('AVISO: não consegui ler as métricas:', str(e)[:300])
        try: m_ = carregar(METRICAS, {}); m_['_erro_leitura'] = {'em': datetime.now(timezone.utc).isoformat(), 'erro': str(e)[:300]}; salvar(METRICAS, m_)
        except Exception: pass
    try: coletar_stories(ig, agenda, estado)
    except Exception as e: log('AVISO: não consegui reler os Stories:', str(e)[:300])
    try: coletar_curvas(ig, agenda, estado)
    except Exception as e: log('AVISO: não consegui ler as curvas:', str(e)[:300])
    try: coletar_comentarios(ig, estado, conta, agenda)
    except Exception as e: log('AVISO: não consegui ler os comentários:', str(e)[:300])
    salvar(ESTADO, estado)


def main():
    teste = '--teste' in sys.argv
    token, user = os.environ.get('IG_TOKEN'), os.environ.get('IG_USER_ID')
    if not token or not user:
        log('ERRO: faltam os segredos IG_TOKEN e/ou IG_USER_ID.'); sys.exit(1)
    agenda = carregar(AGENDA, [])
    estado = carregar(ESTADO, {})
    ig = IG(token, user)
    agora0 = datetime.now(timezone.utc)
    if estado.get('_pausa_ate') and datetime.fromisoformat(estado['_pausa_ate']) > agora0:
        log('Pausado até', estado['_pausa_ate'], '(o Instagram bloqueou ou limitou o app). Nada a fazer agora.'); return
    try:
        conta = ig.conta(); log('Conta:', conta)
        if estado.pop('_pausa_ate', None): estado.pop('_pausa_motivo', None); salvar(ESTADO, estado)
    except RuntimeError as e:
        txt = str(e)
        if "'code': 4," in txt or 'request limit' in txt or 'API access blocked' in txt or "'code': 200," in txt:
            # bloqueio ou limite do lado da Meta: espera 1 hora antes de tentar de novo (insistir piora) e não marca falha
            estado['_pausa_ate'] = (agora0 + timedelta(hours=1)).isoformat(); estado['_pausa_motivo'] = txt[:300]
            salvar(ESTADO, estado); log('Instagram recusou o acesso:', txt[:200], '→ pausa de 1 hora.'); return
        raise
    agora = datetime.now(timezone.utc)
    conferir_externos(ig, agenda, estado); salvar(ESTADO, estado)
    agenda_toda = agenda
    agenda = [i for i in agenda if i.get('via', 'api') == 'api']          # itens do Metricool só aparecem na página
    pendentes = [i for i in agenda if estado.get(i['id'], {}).get('status') != 'publicado']
    devidos = [i for i in pendentes if agora - JANELA <= datetime.fromisoformat(i['quando']) <= agora]
    log(f'{len(pendentes)} pendentes, {len(devidos)} na hora de publicar.')
    if teste:
        for i in sorted(pendentes, key=lambda i: i['quando'])[:10]: log(' ', i['quando'], i['tipo'], i['id'])
        coletar_tudo(ig, agenda_toda, estado, conta)
        return
    ig.t = renovar_token(token, estado)
    falhou = limitado = False
    for item in sorted(devidos, key=lambda i: i['quando']):
        reg = estado.setdefault(item['id'], {'tentativas': 0})
        if reg.get('tentativas', 0) >= MAX_TENTATIVAS:
            log('PULADO (tentativas esgotadas):', item['id']); continue
        desde = datetime.fromisoformat(item['quando']) - timedelta(minutes=30)
        ja = ja_publicado(ig, item, desde, estado)                 # já saiu numa execução anterior? então só registra
        if ja:
            reg.update(status='publicado', media_id=ja, em=datetime.now(timezone.utc).isoformat(), link=ig.link(ja)); reg.pop('ultimo_erro', None)
            log('JÁ ESTAVA NO AR:', item['id'], ja); salvar(ESTADO, estado); continue
        try:
            mid = publicar_item(ig, item, base_midia())
            reg.update(status='publicado', media_id=mid, em=datetime.now(timezone.utc).isoformat(), link=ig.link(mid))
            reg.pop('ultimo_erro', None)
            log('PUBLICADO:', item['id'], mid)
        except Exception as e:
            time.sleep(30)
            ja = ja_publicado(ig, item, desde, estado)             # a resposta foi erro, mas às vezes o post sai mesmo assim
            if ja:
                reg.update(status='publicado', media_id=ja, em=datetime.now(timezone.utc).isoformat(), link=ig.link(ja)); reg.pop('ultimo_erro', None)
                log('SAIU APESAR DO ERRO:', item['id'], ja)
            else:
                bloqueio = 'action is blocked' in str(e) or "'code': 4," in str(e)
                if not bloqueio: reg['tentativas'] = reg.get('tentativas', 0) + 1     # bloqueio do Instagram não gasta tentativa
                if item.get('teste') and not bloqueio and '2207081' not in str(e): reg['tentativas'] = MAX_TENTATIVAS   # Reel de teste pode não aparecer na lista: não repetir às cegas
                reg['ultimo_erro'] = str(e)[:500]
                log('ERRO ao publicar', item['id'], '->', e); falhou = True
            if 'action is blocked' in str(e) or "'code': 4," in str(e):
                log('Instagram limitou as publicações: paro por aqui e tento na próxima execução.'); salvar(ESTADO, estado); limitado = True; break
        salvar(ESTADO, estado)
    salvar(ESTADO, estado)
    if not limitado: coletar_tudo(ig, agenda_toda, estado, conta)      # 27/10: métricas DEPOIS de publicar (publicar tem hora marcada)
    if falhou: sys.exit(1)       # deixa a execução vermelha no GitHub (e manda e-mail de aviso)


if __name__ == '__main__':
    main()
