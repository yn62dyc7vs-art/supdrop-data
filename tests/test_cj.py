"""Velké katalogy z CJ stažené zvlášť (fetch_cj.py) a jejich načtení do šupu: python tests/test_cj.py"""
import gzip, io, json, os, sys, tempfile, time, urllib.error
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc
import fetch_cj as fc

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

def item(name, price, orig=None, stock=True, path='Děti > Hračky > Stavebnice > LEGO', brand='', cond='', iid='1', url='https://www.anrdoezrs.net/click-1?url=x'):
    return {'id': iid, 'group': iid, 'name': name, 'brand': brand, 'path': path, 'url': url, 'tracked': True, 'img': 'https://a.allegroimg.com/x',
            'price': price, 'orig': orig, 'stock': stock, 'size': None, 'ean': '', 'gender': '', 'cond': cond}

# síta při stahování
LEGO = item('LEGO Pocta Spice Girls 40548', 1000.0, 1426.0)
check('sleva 29 % projde', fc.keep(LEGO, 25), True)
check('bez slevy pryč (Allegro)', fc.keep(item('LEGO 40548', 1000.0), 25), False)
check('sleva 3 % pryč (Allegro)', fc.keep(item('Korálky sada', 401.0, 415.0), 25), False)
check('bez cjMinDisc i bez slevy', fc.keep(item('Šaty Answear', 899.0), 0), True)
check('erotika pryč', fc.keep(item('Dorcel MILFs Anthology 2 (DVD)', 401.0, 900.0, path='Filmy > DVD'), 25), False)
check('erotické prádlo pryč', fc.keep(item('Erotické prádlo krajkové', 600.0), 0), False)
check('sexy šaty zůstanou', fc.keep(item('Sexy šaty Answear', 900.0), 0), True)
check('levné pryč', fc.keep(item('Ozdobný roh', 99.0, 300.0), 25), False)
check('neskladem pryč', fc.keep(item('LEGO 40548', 1000.0, 1426.0, stock=False), 25), False)
check('použité pryč', fc.keep(item('LEGO 40548', 1000.0, 1426.0, cond='used'), 25), False)
check('jen vybrané značky', (fc.keep(item('Hodinky Casio', 900.0, brand='Casio'), 0, {'casio'}),
                             fc.keep(item('Hodinky XY', 900.0, brand='XY'), 0, {'casio'})), (True, False))

# stahování: 403 od CJ → počkat a znovu; jiná chyba hned ven
calls = []
def flaky(token, v):
    calls.append(1)
    if len(calls) < 3: raise urllib.error.HTTPError('x', 403, 'Forbidden', {}, None)
    return {'totalCount': 0}
orig_call, fc.SLEEP = fc.bc.cj_call, lambda s: None
fc.bc.cj_call = flaky
check('403 se zkusí znovu', (fc.call('t', {}), len(calls)), ({'totalCount': 0}, 3))
def bad(token, v): raise urllib.error.HTTPError('x', 400, 'Bad Request', {}, None)
fc.bc.cj_call = bad
try: fc.call('t', {}); r = 'bez chyby'
except urllib.error.HTTPError as e: r = e.code
check('400 se neopakuje', r, 400)
fc.bc.cj_call = orig_call

def fake_items(adv, token, ads=None, call=None):
    yield LEGO; yield item('LEGO 40197', 2667.0, 3637.0, iid='2'); yield item('Držák na TV', 900.0, iid='3')
out, n, whole = fc.fetch({'shop': 'Allegro', 'cj': '6464420', 'cjMinDisc': 25}, 't', time.time() + 60, items_fn=fake_items)
check('fetch: 3 položky, 2 se slevou', (n, [x['id'] for x in out], whole), (3, ['1', '2'], True))

# uložení a načtení do šupu
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, 'allegro.json.gz')
fc.save(path, {'shop': 'Allegro'}, out, n, whole)
with gzip.open(path, 'rt', encoding='utf-8') as f: data = json.load(f)
check('soubor', (data['shop'], data['total'], len(data['items'])), ('Allegro', 3, 2))

now = datetime.fromisoformat(data['at'])
shops = [{'shop': 'Allegro', 'net': 'cj', 'cj': '6464420', 'cjFile': path, 'cjMinDisc': 25, 'trust': 6}]
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None, cj_token=None)
d = next((p for p in prods if p['name'].startswith('LEGO Pocta')), {})
check('Allegro z CJ v šupu', (len(prods), d.get('brand'), d.get('now'), d.get('was'), d.get('disc'), d.get('aff')), (2, 'LEGO', 1000.0, 1426.0, 30, True))
check('odkaz přes CJ', d.get('url'), 'https://www.anrdoezrs.net/click-1?url=x')
check('CJ API se nevolá (bez tokenu nevadí)', any('CJ_TOKEN' in r for r in rep), False)

old = dict(data, at='2026-11-01T03:00+01:00')
with gzip.open(path, 'wt', encoding='utf-8') as f: json.dump(old, f)
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None)
check('stará data se nezobrazí', (len(prods), any('starší než 36 h' in r for r in rep)), (0, True))

shops = [dict(shops[0], cjFile=os.path.join(tmp, 'neni.json.gz'))]
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None)
check('chybí soubor → hlášení', (len(prods), rep[0]), (0, 'Allegro: chybí data z denního stahování (větev cj) – starší data nejsou'))

# „every“ u obchodu s cjFile neplatí (soubor se čte každou hodinu)
shops = [{'shop': 'Answear.cz', 'net': 'cj', 'cj': '4327375', 'cjFile': path, 'every': 3, 'trust': 8}]
fc.save(path, {'shop': 'Answear.cz'}, out, n, whole)
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None, prev_fn=lambda s: (None, None))
check('every se ignoruje', len(prods), 2)

print('\nVŠE V POŘÁDKU' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
