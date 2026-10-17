"""SONDA (roda uma vez, só lê): pergunta ao Instagram quais métricas de Reel ele entrega pela API, para sabermos se dá para guardar
taxa de pulos, seguidores x não seguidores e origem das visualizações. Poucas consultas (~20), nada é gravado, nenhum segredo é impresso."""
import os, sys, json
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publicar import IG, ESTADO, carregar


def tentar(rotulo, ig, caminho, **p):
    try:
        r = ig._req('GET', caminho, **p)
        print(f'OK   {rotulo}: {json.dumps(r.get("data", r), ensure_ascii=False)[:600]}', flush=True)
    except Exception as e:
        print(f'ERRO {rotulo}: {str(e)[:330]}', flush=True)


def main():
    ig = IG(os.environ['IG_TOKEN'], os.environ['IG_USER_ID'])
    estado = carregar(ESTADO, {})
    alvos = [k for k in ('reel07', 'ora-reel-noite-2026-10-16') if estado.get(k, {}).get('media_id')]
    print('Reels de teste:', alvos, flush=True)
    mid = estado[alvos[0]]['media_id']
    print('\n=== MÉTRICAS DO REEL (uma por vez)', flush=True)
    for m in ['reels_skip_rate', 'ig_reels_avg_watch_time', 'ig_reels_video_view_total_time', 'views', 'reach', 'follows',
              'profile_visits', 'shares', 'saved', 'plays', 'total_interactions']:
        tentar(m, ig, f'{mid}/insights', metric=m)
    print('\n=== QUEBRAS POR TIPO DE PÚBLICO NO REEL', flush=True)
    for m in ['reach', 'views']:
        tentar(f'{m} breakdown=follow_type', ig, f'{mid}/insights', metric=m, breakdown='follow_type', metric_type='total_value')
    tentar('views breakdown=media_product_type', ig, f'{mid}/insights', metric='views', breakdown='media_product_type', metric_type='total_value')
    print('\n=== NA CONTA INTEIRA (últimas 48 h)', flush=True)
    fim = int(datetime.now(timezone.utc).timestamp()); ini = fim - 2 * 86400
    for m, b in [('reach', 'follow_type'), ('views', 'follow_type'), ('views', 'media_product_type'), ('reach', 'media_product_type')]:
        tentar(f'conta {m} breakdown={b}', ig, f'{ig.u}/insights', metric=m, period='day', metric_type='total_value', breakdown=b, since=ini, until=fim)
    print('\nFim da sonda.', flush=True)


if __name__ == '__main__':
    main()
