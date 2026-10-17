"""SONDA (roda uma vez, só lê): pergunta ao Instagram quais métricas de Reel ele entrega pela API. Poucas consultas, nada é gravado,
nenhum segredo é impresso. Rodada 1 (17/10/2026): taxa de pulos, tempo assistido, views, alcance, salvamentos e quebras por seguidor/
não seguidor e por tipo de conteúdo no nível da conta funcionam; follows e profile_visits não existem para Reel; quebra por seguidor
em cada Reel não é aceita. Rodada 2: origem da visualização (surface_type) e resumo legível das quebras da conta."""
import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publicar import IG, ESTADO, carregar


def resumo(r):
    d = (r.get('data') or [{}])[0]
    tv = d.get('total_value') or {}
    if tv.get('breakdowns'):
        pares = [f'{"/".join(x["dimension_values"])}={x["value"]}' for b in tv['breakdowns'] for x in b.get('results', [])]
        return f'total={tv.get("value")} | ' + ', '.join(pares)
    return json.dumps(r.get('data', r), ensure_ascii=False)[:400]


def tentar(rotulo, ig, caminho, **p):
    try:
        print(f'OK   {rotulo}: {resumo(ig._req("GET", caminho, **p))}', flush=True)
    except Exception as e:
        print(f'ERRO {rotulo}: {str(e)[:300]}', flush=True)


def main():
    ig = IG(os.environ['IG_TOKEN'], os.environ['IG_USER_ID'])
    estado = carregar(ESTADO, {})
    mid = estado['reel07']['media_id']
    fim = int(datetime.now(timezone.utc).timestamp()); ini = fim - 2 * 86400
    print('=== RODADA 2: ORIGEM DA VISUALIZAÇÃO (surface_type) E RESUMO', flush=True)
    for m in ('views', 'reach'):
        tentar(f'REEL {m} breakdown=surface_type', ig, f'{mid}/insights', metric=m, breakdown='surface_type', metric_type='total_value')
    for m, b in [('views', 'surface_type'), ('reach', 'surface_type'), ('reach', 'follow_type'), ('views', 'follow_type'), ('views', 'media_product_type')]:
        tentar(f'CONTA {m} breakdown={b}', ig, f'{ig.u}/insights', metric=m, period='day', metric_type='total_value', breakdown=b, since=ini, until=fim)
    print('Fim da sonda.', flush=True)


if __name__ == '__main__':
    main()
