"""Junta os arquivos de dados quando duas execuções do robô gravam ao mesmo tempo (21/10/2026).
Uso (no workflow): python juntar_dados.py <pasta_com_a_nossa_versão>
Lê a versão do GitHub (arquivos atuais na pasta do repo) e a nossa (cópia salva antes do reset) e grava a junção:
- estado.json: união; para o mesmo item, vale quem tem 'publicado' (ou a nossa, se nenhum tem).
- metricas.json: por item, vale a leitura mais nova (lido_em); campos com '_' vêm de quem tem o '_em' mais novo.
- curvas.json: união dos pontos de cada Reel, sem repetir horário.
- historico.json: por dia, vale quem tem o 'em' mais novo.
- comentarios.json: a nossa versão."""
import json, os, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
NOSSA = sys.argv[1]


def ler(p):
    try: return json.load(open(p, encoding='utf-8-sig'))
    except Exception: return None


def gravar(nome, dado):
    json.dump(dado, open(os.path.join(AQUI, nome), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def juntar_estado(g, n):
    out = dict(g or {})
    for k, v in (n or {}).items():
        gv = out.get(k)
        if not isinstance(gv, dict) or not isinstance(v, dict): out.setdefault(k, v); continue
        if gv.get('status') == 'publicado' and v.get('status') != 'publicado': continue
        out[k] = v
    return out


def juntar_metricas(g, n):
    g, n = g or {}, n or {}
    out = dict(g)
    for k, v in n.items():
        if k.startswith('_'): continue
        gv = out.get(k)
        if not isinstance(gv, dict) or (isinstance(v, dict) and str(v.get('lido_em', '')) >= str(gv.get('lido_em', ''))): out[k] = v
    base = n if str(n.get('_em', '')) >= str(g.get('_em', '')) else g
    for k, v in base.items():
        if k.startswith('_'): out[k] = v
    return out


def juntar_curvas(g, n):
    out = dict(g or {})
    for k, v in (n or {}).items():
        if k not in out: out[k] = v; continue
        pts = {p['t']: p for p in out[k].get('pontos', [])}
        for p in v.get('pontos', []): pts[p['t']] = p
        out[k]['pontos'] = [pts[t] for t in sorted(pts)]
    return out


def juntar_historico(g, n):
    out = dict(g or {})
    for d, v in (n or {}).items():
        if d not in out or str(v.get('em', '')) >= str(out[d].get('em', '')): out[d] = v
    return out


for nome, f in (('estado.json', juntar_estado), ('metricas.json', juntar_metricas), ('curvas.json', juntar_curvas),
                ('historico.json', juntar_historico)):
    n = ler(os.path.join(NOSSA, nome))
    if n is None: continue
    gravar(nome, f(ler(os.path.join(AQUI, nome)), n))
c = ler(os.path.join(NOSSA, 'comentarios.json'))
if c is not None: gravar('comentarios.json', c)
print('dados juntados')
