"""Relatórios por e-mail dos dois canais (diário, semanal, mensal e anual), sempre comparando com o período anterior.

Roda no GitHub Actions todo dia por volta das 23h50 (horário de Brasília), depois da leitura final do dia que o
publicar.py faz às 23h30. Lê o historico.json de cada canal (um retrato dos números por dia).
  - diário: todo dia;  semanal: domingo (semana de segunda a domingo);
  - mensal: último dia do mês;  anual: 31/12.

Segredos (GitHub → Settings → Secrets and variables → Actions):
  EMAIL_USUARIO  o endereço do Gmail que envia (e recebe) os relatórios
  EMAIL_SENHA    a "senha de app" do Gmail (não é a senha normal)
  EMAIL_PARA     (opcional) outro endereço para receber; se não tiver, vai para o próprio EMAIL_USUARIO

Uso manual:  python relatorio.py [--dia AAAA-MM-DD] [--tipo diario|semanal|mensal|anual] [--sem-email]
"""
import json, os, smtplib, sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from html import escape
import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
BRT = timezone(timedelta(hours=-3))
PAGINA = 'https://recortesdabibliaoficial-cmyk.github.io/recortes-publicador/'
CANAIS = [  # (nome, arroba, pasta local ou None, endereço dos arquivos no GitHub)
    ('Recortes da Bíblia', '@recortesdabiblia.oficial', AQUI, None),
    ('Curiosidades em Papel', '@curiosidadesempapel', None,
     'https://raw.githubusercontent.com/recortesdabibliaoficial-cmyk/curiosidades-publicador/main/'),
]
TIPO_NOME = {'CARROSSEL': 'Carrossel', 'REEL': 'Reel', 'STORY': 'Story'}
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
         'novembro', 'dezembro']


def ler(canal, arquivo, padrao):
    _, _, pasta, url = canal
    try:
        if pasta:
            with open(os.path.join(pasta, arquivo), encoding='utf-8') as f: return json.load(f)
        r = requests.get(url + arquivo, timeout=60); r.raise_for_status(); return r.json()
    except Exception as e:
        print('AVISO: não consegui ler', arquivo, 'de', canal[0], '-', str(e)[:150]); return padrao


# ---------- contas ----------

def dias(d0, d1):
    d = d0
    while d <= d1: yield d; d += timedelta(days=1)


def retrato_ate(hist, d):
    """O retrato mais recente até o dia d (inclusive)."""
    chaves = [k for k in hist if k <= d.isoformat()]
    return hist[max(chaves)] if chaves else None


def periodo(hist, agenda, estado, d0, d1):
    """Números de um período [d0, d1]. Devolve None se não houver nenhum retrato no período."""
    if not any(d.isoformat() in hist for d in dias(d0, d1)): return None
    fim, ini = retrato_ate(hist, d1), retrato_ate(hist, d0 - timedelta(days=1))
    r = {'seguidores': fim.get('seguidores'),
         'novos_seguidores': (fim.get('seguidores') or 0) - (ini.get('seguidores') or 0) if ini and ini.get('seguidores') is not None else None}
    # números da conta (somados dia a dia)
    for c in ('reach', 'views', 'accounts_engaged', 'total_interactions', 'profile_views'):
        vals = [hist[d.isoformat()].get('conta', {}).get(c) for d in dias(d0, d1) if d.isoformat() in hist]
        vals = [v for v in vals if isinstance(v, (int, float))]
        r['conta_' + c] = sum(vals) if vals else None
    # o que os posts ganharam no período (diferença entre o retrato do fim e o do dia anterior ao início)
    a, b = (ini or {}).get('itens'), fim.get('itens', {})
    for c in ('views', 'likes', 'comments', 'saved', 'shares'):     # sem retrato dos posts antes do período: não dá para saber
        r['ganho_' + c] = sum(max(0, (m.get(c) or 0) - (a.get(k, {}).get(c) or 0)) for k, m in b.items()) if a is not None else None
    # o que foi publicado no período
    tit = {i['id']: (i.get('titulo') or i['id'], i['tipo']) for i in agenda}
    pubs = []
    for iid, reg in estado.items():
        if not isinstance(reg, dict) or reg.get('status') != 'publicado' or iid not in tit: continue
        try: dia = datetime.fromisoformat(reg['em']).astimezone(BRT).date()
        except Exception: continue
        if d0 <= dia <= d1:
            m = b.get(iid, {})
            pubs.append({'id': iid, 'titulo': tit[iid][0], 'tipo': tit[iid][1], 'dia': dia, 'link': reg.get('link'), **m})
    pubs.sort(key=lambda p: (-(p.get('reach') or 0), p['dia']))
    r['publicados'] = pubs
    r['n_publicados'] = len(pubs)
    for t in ('CARROSSEL', 'REEL', 'STORY'):
        rs = [p.get('reach') or 0 for p in pubs if p['tipo'] == t and 'reach' in p]
        r['media_alcance_' + t] = round(sum(rs) / len(rs), 1) if rs else None
    ws = [p['ig_reels_avg_watch_time'] / 1000 for p in pubs if p.get('ig_reels_avg_watch_time')]
    r['reel_tempo_medio'] = round(sum(ws) / len(ws), 1) if ws else None
    return r


def limites(tipo, d):
    """Período do relatório e o período anterior para comparar."""
    if tipo == 'diario':
        return (d, d), (d - timedelta(days=1), d - timedelta(days=1))
    if tipo == 'semanal':
        ini = d - timedelta(days=d.weekday())
        return (ini, ini + timedelta(days=6)), (ini - timedelta(days=7), ini - timedelta(days=1))
    if tipo == 'mensal':
        ini = d.replace(day=1)
        fim = (ini + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        ant_fim = ini - timedelta(days=1)
        return (ini, fim), (ant_fim.replace(day=1), ant_fim)
    ini = d.replace(month=1, day=1)
    return (ini, d.replace(month=12, day=31)), (ini.replace(year=ini.year - 1), ini - timedelta(days=1))


# ---------- e-mail ----------

LINHAS = [  # (chave, nome, é número "quanto maior melhor")
    ('seguidores', 'Seguidores (total)'),
    ('novos_seguidores', 'Seguidores ganhos'),
    ('conta_reach', 'Alcance da conta (pessoas, somado por dia)'),
    ('conta_views', 'Visualizações da conta'),
    ('conta_accounts_engaged', 'Pessoas que interagiram'),
    ('conta_profile_views', 'Visitas ao perfil'),
    ('ganho_views', 'Visualizações ganhas pelos posts'),
    ('ganho_likes', 'Curtidas ganhas'),
    ('ganho_comments', 'Comentários ganhos'),
    ('ganho_saved', 'Salvamentos ganhos'),
    ('ganho_shares', 'Compartilhamentos ganhos'),
    ('n_publicados', 'Publicações feitas'),
    ('media_alcance_REEL', 'Alcance médio por Reel'),
    ('media_alcance_CARROSSEL', 'Alcance médio por carrossel'),
    ('media_alcance_STORY', 'Alcance médio por Story'),
    ('reel_tempo_medio', 'Tempo médio assistindo os Reels (s)'),
]
VERDE, VERMELHO, CINZA = '#1a7f37', '#c62828', '#8a8a8a'


def fmt(v):
    if v is None: return '—'
    if isinstance(v, float) and not v.is_integer(): return f'{v:.1f}'.replace('.', ',')
    return f'{int(v):,}'.replace(',', '.')


def comparar(v, ant):
    if v is None or ant is None: return f'<span style="color:{CINZA}">sem comparação</span>'
    dif = v - ant
    if abs(dif) < 1e-9: return f'<span style="color:{CINZA}">= igual</span>'
    cor, seta = (VERDE, '▲') if dif > 0 else (VERMELHO, '▼')
    pct = f' ({dif / ant * 100:+.0f}%)'.replace('.', ',') if ant else ''
    return f'<span style="color:{cor}">{seta} {fmt(abs(dif))}{pct}</span>'


def titulo_periodo(tipo, p):
    d0, d1 = p
    if tipo == 'diario': return d0.strftime('%d/%m/%Y')
    if tipo == 'semanal': return f'{d0:%d/%m} a {d1:%d/%m/%Y}'
    if tipo == 'mensal': return f'{MESES[d0.month - 1]} de {d0.year}'
    return str(d0.year)


NOME_ANT = {'diario': 'o dia anterior', 'semanal': 'a semana anterior', 'mensal': 'o mês anterior', 'anual': 'o ano anterior'}


def conferir_dia(agenda, estado, d):
    """Alarme (Felipe, 17/10/2026): do que estava agendado para o dia d e cuja hora já passou, o que saiu e o que NÃO saiu."""
    agora = datetime.now(BRT); esperados, faltam = [], []
    for i in agenda:
        q = datetime.fromisoformat(i['quando']).astimezone(BRT)
        if q.date() != d or q > agora: continue
        esperados.append(i)
        if estado.get(i['id'], {}).get('status') != 'publicado': faltam.append((q, i))
    return esperados, faltam


def banner_alarme(esperados, faltam):
    if not esperados:
        return f'<p style="color:{CINZA};font-size:13px;margin:4px 0">Nada estava agendado para este dia.</p>'
    if not faltam:
        return (f'<div style="background:#e8f5e9;border-left:4px solid {VERDE};padding:8px 12px;margin:6px 0;font-size:14px">'
                f'✅ Tudo que estava agendado saiu: {len(esperados)} de {len(esperados)}.</div>')
    itens = ''.join(f'<li>{q:%H:%M} · {TIPO_NOME.get(i["tipo"], i["tipo"])} · {escape(i.get("titulo") or i["id"])}</li>'
                    for q, i in sorted(faltam, key=lambda x: x[0]))
    return (f'<div style="background:#fdecea;border-left:4px solid {VERMELHO};padding:8px 12px;margin:6px 0;font-size:14px">'
            f'⚠️ <b>NÃO SAIU: {len(faltam)} de {len(esperados)} agendados.</b><ul style="margin:6px 0 0 18px">{itens}</ul></div>')


def bloco_canal(nome, arroba, tipo, atual, ant, media7, alarme=None):
    td = 'padding:6px 8px;border-bottom:1px solid #eee;font-size:14px'
    h = [f'<h2 style="font-size:18px;margin:24px 0 6px">{escape(nome)} <span style="color:{CINZA};font-weight:normal;font-size:14px">{arroba}</span></h2>']
    if alarme is not None: h.append(banner_alarme(*alarme))
    if not atual:
        return ''.join(h) + f'<p style="color:{CINZA}">Ainda não há números guardados para este período.</p>'
    h.append('<table style="border-collapse:collapse;width:100%">')
    cab = f'<tr><th style="{td};text-align:left">Número</th><th style="{td};text-align:right">Agora</th><th style="{td};text-align:right">vs {NOME_ANT[tipo]}</th>'
    cab += f'<th style="{td};text-align:right">vs média de 7 dias</th></tr>' if media7 is not None else '</tr>'
    h.append(cab)
    for k, rot in LINHAS:
        v = atual.get(k)
        if v is None and (ant or {}).get(k) is None: continue
        lin = f'<tr><td style="{td}">{rot}</td><td style="{td};text-align:right"><b>{fmt(v)}</b></td>'
        lin += f'<td style="{td};text-align:right">{comparar(v, (ant or {}).get(k))}</td>'
        if media7 is not None: lin += f'<td style="{td};text-align:right">{comparar(v, media7.get(k)) if k != "seguidores" else ""}</td>'
        h.append(lin + '</tr>')
    h.append('</table>')
    pubs = atual['publicados']
    if pubs:
        lim = 10 if tipo != 'diario' else 20
        h.append(f'<p style="margin:14px 0 4px;font-size:14px"><b>{"Publicado no dia" if tipo == "diario" else f"Melhores publicações ({min(lim, len(pubs))} de {len(pubs)})"}</b> — ordenado por alcance</p>')
        h.append('<table style="border-collapse:collapse;width:100%">')
        h.append(f'<tr><th style="{td};text-align:left">Post</th><th style="{td}">Pessoas</th><th style="{td}">Curt.</th><th style="{td}">Coment.</th><th style="{td}">Salv.</th><th style="{td}">Compart.</th></tr>')
        for p in pubs[:lim]:
            t = escape(p['titulo'][:60]); t = f'<a href="{p["link"]}" style="color:#1a4fa0">{t}</a>' if p.get('link') else t
            extra = f' · assistido {p["ig_reels_avg_watch_time"] / 1000:.0f}s'.replace('.', ',') if p.get('ig_reels_avg_watch_time') else ''
            dia = '' if tipo == 'diario' else f' · {p["dia"]:%d/%m}'
            h.append(f'<tr><td style="{td}">{t}<br><span style="color:{CINZA};font-size:12px">{TIPO_NOME[p["tipo"]]}{dia}{extra}</span></td>'
                     + ''.join(f'<td style="{td};text-align:center">{fmt(p.get(c))}</td>' for c in ('reach', 'likes', 'comments', 'saved', 'shares')) + '</tr>')
        h.append('</table>')
    else:
        h.append(f'<p style="color:{CINZA};font-size:14px">Nenhuma publicação neste período.</p>')
    return ''.join(h)


def media_7_dias(hist, agenda, estado, d):
    """Média diária dos 7 dias anteriores a d (para o relatório diário)."""
    ps = [periodo(hist, agenda, estado, x, x) for x in dias(d - timedelta(days=7), d - timedelta(days=1))]
    ps = [p for p in ps if p]
    if not ps: return None
    m = {}
    for k, _ in LINHAS:
        vs = [p[k] for p in ps if p.get(k) is not None]
        m[k] = sum(vs) / len(vs) if vs else None
    return m


def montar(tipo, d):
    p, pa = limites(tipo, d)
    nome = {'diario': 'diário', 'semanal': 'semanal', 'mensal': 'mensal', 'anual': 'anual'}[tipo]
    corpo = [f'<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#222">',
             f'<h1 style="font-size:22px;margin:0 0 4px">📊 Relatório {nome} — {titulo_periodo(tipo, p)}</h1>',
             f'<p style="color:{CINZA};margin:0 0 8px;font-size:13px">Comparando com {NOME_ANT[tipo]} ({titulo_periodo(tipo, pa)}).</p>']
    faltaram = 0
    for canal in CANAIS:
        hist, agenda, estado = ler(canal, 'historico.json', {}), ler(canal, 'agenda.json', []), ler(canal, 'estado.json', {})
        atual, ant = periodo(hist, agenda, estado, *p), periodo(hist, agenda, estado, *pa)
        m7 = media_7_dias(hist, agenda, estado, d) if tipo == 'diario' else None
        alarme = conferir_dia(agenda, estado, p[0]) if tipo == 'diario' else None
        if alarme: faltaram += len(alarme[1])
        corpo.append(bloco_canal(canal[0], canal[1], tipo, atual, ant, m7, alarme))
    corpo.append(f'<p style="color:{CINZA};font-size:12px;margin-top:24px">"Seguidores ganhos" já desconta quem deixou de seguir. '
                 'O alcance da conta é somado dia a dia (a mesma pessoa em dois dias conta duas vezes). '
                 'Números dos posts dos últimos 14 dias; Stories só nas primeiras 24 horas.<br>'
                 f'<a href="{PAGINA}" style="color:#1a4fa0">Abrir a página da agenda</a></p></div>')
    aviso = f'⚠️ FALTOU POST ({faltaram}) · ' if faltaram else ''
    return aviso + f'📊 Relatório {nome} dos canais — {titulo_periodo(tipo, p)}', ''.join(corpo)


def enviar(assunto, html):
    usuario, senha = os.environ.get('EMAIL_USUARIO'), os.environ.get('EMAIL_SENHA')
    if not usuario or not senha:
        print('AVISO: faltam os segredos EMAIL_USUARIO e/ou EMAIL_SENHA; o e-mail não foi enviado.'); return False
    usuario, senha = usuario.strip(), ''.join(senha.split())        # a senha de app vem em grupos com espaço
    para = (os.environ.get('EMAIL_PARA') or usuario).strip()
    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'], msg['From'], msg['To'] = assunto, f'Relatórios dos canais <{usuario}>', para
    erros = []
    for porta in (587, 465):                                        # 587 (STARTTLS) e, se falhar, 465 (SSL)
        try:
            s = smtplib.SMTP('smtp.gmail.com', porta, timeout=60) if porta == 587 else smtplib.SMTP_SSL('smtp.gmail.com', porta, timeout=60)
            with s:
                if porta == 587: s.ehlo(); s.starttls(); s.ehlo()
                s.ehlo_or_helo_if_needed(); s.user, s.password = usuario, senha
                cod, resp = s.auth('PLAIN', s.auth_plain)     # só PLAIN: se o Gmail recusar, o erro real aparece (o login() esconde)
                if cod != 235: raise smtplib.SMTPAuthenticationError(cod, resp)
                s.send_message(msg)
            print('Enviado:', assunto, f'(porta {porta})'); return True
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(f'o Gmail recusou o usuário/senha de app ({e.smtp_code}): confira os segredos EMAIL_USUARIO e EMAIL_SENHA')
        except Exception as e:
            erros.append(f'porta {porta}: {type(e).__name__}: {str(e)[:150]}')
    raise RuntimeError(' | '.join(erros))


def main():
    args = sys.argv[1:]
    agora = datetime.now(BRT)
    d = date.fromisoformat(args[args.index('--dia') + 1]) if '--dia' in args else (agora - timedelta(hours=3)).date()
    if '--tipo' in args: tipos = [args[args.index('--tipo') + 1]]
    else:
        tipos = ['diario']
        if d.weekday() == 6: tipos.append('semanal')
        if (d + timedelta(days=1)).day == 1: tipos.append('mensal')
        if d.month == 12 and d.day == 31: tipos.append('anual')
    erro = False
    for t in tipos:
        assunto, html = montar(t, d)
        with open(os.path.join(os.environ.get('RUNNER_TEMP', AQUI), f'relatorio_{t}.html'), 'w', encoding='utf-8') as f: f.write(html)
        if '--sem-email' in args: print('Gerado (sem e-mail):', assunto); continue
        try: enviar(assunto, html)
        except Exception as e: print('ERRO ao enviar', t, '->', str(e)[:300]); erro = True
    if erro: sys.exit(1)


if __name__ == '__main__':
    main()
