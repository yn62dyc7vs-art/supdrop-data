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
def slow(token, v): raise TimeoutError('The read operation timed out')
fc.bc.cj_call = slow; calls.clear()
try: fc.call('t', {}); r = 'bez chyby'
except TimeoutError: r = 'TimeoutError po opakování'
check('vypršení spojení se zkusí znovu, pak chyba', r, 'TimeoutError po opakování')
fc.DEADLINE[0] = time.time() - 1
try: fc.call('t', {}); r = 'bez chyby'
except fc.OutOfTime: r = 'OutOfTime'
check('po termínu se už nedotazuje', r, 'OutOfTime')
fc.DEADLINE[0] = None
fc.bc.cj_call = orig_call
def stuck_items(adv, token, ads=None, call=None):
    yield LEGO
    raise fc.OutOfTime()
out, n, whole = fc.fetch({'shop': 'Allegro', 'cj': '6464420', 'cjMinDisc': 25}, 't', time.time() + 60, items_fn=stuck_items)
check('došel čas uprostřed: uloží, co má', (n, len(out), whole, fc.DEADLINE[0]), (1, 1, False, None))

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

# pořadí a časový strop jednoho obchodu (Allegro nesmí čekat za pomalým Answearem)
lst = [{'shop': 'Kabea.cz'}, {'shop': 'Answear.cz'}, {'shop': 'Allegro', 'cjPrio': 1}, {'shop': 'Husky'}]
check('Allegro první, ostatní v původním pořadí', [s['shop'] for s in fc.order(lst)], ['Allegro', 'Kabea.cz', 'Answear.cz', 'Husky'])
check('strop obchodu výchozí 40 min', fc.shop_deadline({}, 1000, 1000 + 140 * 60), 1000 + 40 * 60)
check('strop obchodu cjBudget 10 min', fc.shop_deadline({'cjBudget': 10}, 1000, 1000 + 140 * 60), 1000 + 10 * 60)
check('strop nepřesáhne celkový limit', fc.shop_deadline({'cjBudget': 90}, 1000, 1000 + 20 * 60), 1000 + 20 * 60)

# nedočtený výsledek nesmí přepsat větší soubor
big = os.path.join(tmp, 'velky.json.gz')
fc.save(big, {'shop': 'Answear.cz'}, [dict(LEGO, id=str(i)) for i in range(5)], 5, True)
check('nedočtený menší nepřepíše větší', fc.keeps_old(big, [LEGO] * 2, False), True)
check('nedočtený větší přepíše', fc.keeps_old(big, [LEGO] * 8, False), False)
check('dočtený vždy přepíše', fc.keeps_old(big, [LEGO] * 2, True), False)
check('bez souboru se uloží', fc.keeps_old(os.path.join(tmp, 'nic.json.gz'), [LEGO], False), False)
# dělení velkého obchodu na cenová pásma
many = [dict(LEGO, id=str(i), price=float(100 + i)) for i in range(2000)]          # ceny 100–2099
bs = fc.bands(many, 2)
check('2 pásma, hranice u mediánu', (len(bs), bs[0][0], bs[0][1], bs[1][0], bs[1][1]), (2, 0.0, 1099.99, 1100.0, 1000000.0))
sl = os.path.join(tmp, 'sl.json.gz')
fc.save(sl, {'shop': 'Answear.cz'}, many, 2000, True)
sh = {'shop': 'Answear.cz', 'cjFile': sl, 'cjSlices': 2}
check('pásmo se střídá po 12 h', (fc.slice_plan(sh, 0)[0], fc.slice_plan(sh, 12 * 3600)[0]), (bs[0], bs[1]))
check('bez cjSlices se dělí nic', fc.slice_plan(dict(sh, cjSlices=None), 0), None)
check('bez souboru se dělí nic', fc.slice_plan(dict(sh, cjFile=os.path.join(tmp, 'neni2.json.gz')), 0), None)
new = [dict(LEGO, id='5', price=105.0), dict(LEGO, id='n1', price=500.0)]           # 5 zdražilo na 105 (zůstává v pásmu), n1 je nové
m = fc.merge(many, new, bs[0])
check('merge: staré pásmo se nahradí, druhé zůstane', (len(m), sum(1 for x in m if x['price'] >= 1100), sorted(x['id'] for x in m if x['id'] in ('5', 'n1'))), (1000 + 2, 1000, ['5', 'n1']))
seen = []
def fake_call(token, v): seen.append((v.get('lo'), v.get('hi'))); return {'totalCount': 0}
list(bc.cj_items('1', 't', None, call=fake_call, rng=(1100.0, 1000000.0)))
check('cj_items se drží v pásmu', seen, [(1100.0, 1000000.0)])
cfg = [s for s in json.load(open(fc.CONFIG, encoding='utf-8')) if s.get('cjFile')]
check('v shops.json je Allegro první', fc.order(cfg)[0]['shop'], 'Allegro')

print('\nVŠE V POŘÁDKU' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
