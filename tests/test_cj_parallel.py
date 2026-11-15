"""Paralelní stahování CJ po cenových pásmech (fetch_cj.py --slice K/N a --assemble): python tests/test_cj_parallel.py
Bez skutečného CJ API – stahování nahrazuje falešná funkce."""
import gzip, json, os, sys, tempfile, time, urllib.error
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc
import fetch_cj as fc

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

def item(iid, price, name='Šaty Answear'):
    return {'id': str(iid), 'group': str(iid), 'name': name, 'brand': 'Answear', 'path': 'Oblečení > Šaty', 'url': 'https://www.anrdoezrs.net/click-1?url=x',
            'tracked': True, 'img': 'https://x/img.jpg', 'price': float(price), 'orig': None, 'stock': True, 'size': None, 'ean': '', 'gender': '', 'cond': ''}

tmp = tempfile.mkdtemp()
parts = os.path.join(tmp, 'parts')
now = datetime.now(bc.TZ)
iso = lambda t: t.isoformat(timespec='minutes')

# zápis K/N
check('parse 2/4', fc.parse_slice('2/4'), (2, 4))
for bad in ('0/3', '4/3', 'x', '1-3'):
    try: fc.parse_slice(bad); r = 'prošlo'
    except ValueError: r = 'ValueError'
    check(f'chybné {bad!r}', r, 'ValueError')

# pásma: podle minulého souboru, bez něj pevná; souvislá od 0 do 1 000 000
old_items = [item(i, 200 + i) for i in range(3000)]                  # ceny 200–3199
path = os.path.join(tmp, 'answear.json.gz')
sh = {'shop': 'Answear.cz', 'cj': '4327375', 'cjFile': path}
fc.save(path, sh, old_items, 3000, True, at=iso(now - timedelta(hours=12)))
bs = fc.slice_bands(sh, 3)
check('3 pásma z minulého souboru = bands()', bs, fc.bands(old_items, 3))
fb = fc.slice_bands(dict(sh, cjFile=os.path.join(tmp, 'neni.json.gz')), 3)
check('bez souboru: 3 pevná pásma', len(fb), 3)
for name, b in (('z souboru', bs), ('pevná', fb)):
    check(f'pásma {name} navazují bez mezery', (b[0][0], b[-1][1], all(abs(b[i][1] + 0.01 - b[i + 1][0]) < 1e-6 for i in range(len(b) - 1))), (0.0, 1000000.0, True))
check('každá cena patří do právě jednoho pásma', all(sum(lo <= x['price'] <= hi for lo, hi in bs) == 1 for x in old_items), True)

# --slice: každé pásmo zvlášť, hlavní soubor se nemění
def fake_items_for(rng, fresh_price_shift=1, fail=False, stop=False):
    def gen(adv, token, ads=None, call=None):
        for x in old_items:
            if rng[0] <= x['price'] <= rng[1] and int(x['id']) % 10:          # každá 10. položka z nabídky zmizela
                yield dict(x, price=x['price'] + fresh_price_shift * 0)
                if stop and int(x['id']) % 10 == 5: raise fc.OutOfTime()
        if fail: raise urllib.error.HTTPError('x', 403, 'Forbidden', {}, None)
    return gen
mtime = os.path.getmtime(path)
p1 = fc.fetch_slice(sh, 't', 1, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[0]))
d1 = fc.read(p1)
check('pásmo 1: soubor pásma', (os.path.basename(p1), d1['k'], d1['of'], d1['lo'], d1['hi'], d1['whole']),
      ('answear.1.json.gz', 1, 3, bs[0][0], bs[0][1], True))
check('pásmo 1: jen jeho ceny', all(bs[0][0] <= x['price'] <= bs[0][1] for x in d1['items']), True)
check('hlavní soubor se při --slice nemění', os.path.getmtime(path), mtime)
check('neexistující pásmo (12/12, pevných hranic je jen 9) → nic', fc.fetch_slice(dict(sh, cjFile=os.path.join(tmp, 'mini.json.gz')), 't', 12, 12,
                                                     time.time() + 60, parts, items_fn=fake_items_for(bs[0])), None)
check('chyba (403 po opakováních) → žádné pásmo', fc.fetch_slice(sh, 't', 3, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[2], fail=True)), None)

# --assemble: pásmo 3 chybí → jeho položky zůstanou z minulého souboru (je čerstvý)
fc.fetch_slice(sh, 't', 2, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[1]))
check('assemble uloží', fc.assemble(sh, now, parts), True)
d = fc.read(path)
in3 = [x for x in d['items'] if bs[2][0] <= x['price'] <= bs[2][1]]
check('formát pro build_catalog', sorted(k for k in d if k != 'parts'), ['at', 'items', 'shop', 'total', 'whole'])
check('pásma 1+2 bez zmizelých, pásmo 3 celé z minulého souboru',
      (len(d['items']), len(in3), any(int(x['id']) % 10 == 0 for x in d['items'] if x['price'] <= bs[1][1])), (900 + 900 + 1000, 1000, False))
check('chybí pásmo → whole False', d['whole'], False)
check('parts: 2 pásma + zbytek s časem minulého souboru', (len(d['parts']), d['parts'][-1]['at']), (3, iso(now - timedelta(hours=12))))
ids = [x['id'] for x in d['items']]
check('žádná položka dvakrát', len(ids), len(set(ids)))

# soubor přečte hodinový běh (bc.build) jako dřív
shops = [{'shop': 'Answear.cz', 'net': 'cj', 'cj': '4327375', 'cjFile': path, 'trust': 8}]
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None, prev_fn=lambda s: (None, None))
check('build_catalog soubor načte', (len(prods) > 0, any('starší' in r or 'chybí' in r for r in rep)), (True, False))

# další běh: pásmo 3 znovu chybí, ale jeho data už jsou starší než 36 h → zahodí se (nezobrazit neplatné ceny)
later = now + timedelta(hours=30)       # zbytek je z (now - 12 h) → 42 h
check('podruhé assemble', fc.assemble(sh, later, parts), True)
d = fc.read(path)
check('staré pásmo (>36 h) zmizí, čerstvá pásma zůstanou', (len(d['items']), any(bs[2][0] <= x['price'] <= bs[2][1] for x in d['items'])), (1800, False))

# všechna pásma dočtená → whole, staré položky z dočtených pásem pryč
for f in os.listdir(parts): os.remove(os.path.join(parts, f))
fc.save(path, sh, old_items, 3000, True, at=iso(now - timedelta(hours=12)))
for k in (1, 2, 3): fc.fetch_slice(sh, 't', k, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[k - 1]))
fc.assemble(sh, now, parts)
d = fc.read(path)
check('3/3 dočteno → whole, 2700 položek', (d['whole'], len(d['items']), d['total']), (True, 2700, 2700))

# nedočtené pásmo: nové + staré, které nepřišly (nic se neztratí)
for f in os.listdir(parts): os.remove(os.path.join(parts, f))
fc.save(path, sh, old_items, 3000, True, at=iso(now - timedelta(hours=12)))
for k in (1, 2): fc.fetch_slice(sh, 't', k, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[k - 1]))
fc.fetch_slice(sh, 't', 3, 3, time.time() + 60, parts, items_fn=fake_items_for(bs[2], stop=True))
check('pásmo 3 nedočtené', fc.read(fc.part_path(sh, 3, parts))['whole'], False)
fc.assemble(sh, now, parts)
d = fc.read(path)
check('nedočtené pásmo doplněno z minulého souboru', (len([x for x in d['items'] if x['price'] >= bs[2][0]]), d['whole']), (1000, False))
p3 = next(p for p in d['parts'] if p['lo'] == bs[2][0])
check('nedočtené pásmo nese čas minulého souboru', p3['at'], iso(now - timedelta(hours=12)))

# keeps_old po pásmech: nedočtené pásmo přes celý rozsah s 1 položkou nesmí smazat 5000 starých
for f in os.listdir(parts): os.remove(os.path.join(parts, f))
big = [item(i, 200 + i) for i in range(5000)]
fc.save(path, sh, big, 5000, True, at=iso(now - timedelta(hours=1)))
fc.save(fc.part_path(sh, 1, parts), sh, [item('n', 300)], 10, False, {'lo': 0.0, 'hi': 1000000.0, 'k': 1, 'of': 1})
check('nedočtené nic neubere', (fc.assemble(sh, now, parts), len(fc.read(path)['items']), fc.read(path)['whole']), (True, 5001, False))
check('bez pásem nic', fc.assemble(sh, now, os.path.join(tmp, 'prazdne')), False)

# čekání po 403 s náhodným prodloužením (CJ_JITTER), ať souběžné úlohy nebuší najednou
slept, calls = [], []
def flaky(token, v):
    calls.append(1)
    if len(calls) < 2: raise urllib.error.HTTPError('x', 429, 'Too Many', {}, None)
    return {'totalCount': 0}
orig = (fc.bc.cj_call, fc.SLEEP, fc.JITTER)
fc.bc.cj_call, fc.SLEEP, fc.JITTER = flaky, slept.append, 30
fc.call('t', {})
check('jitter: 60–90 s', 60 <= slept[0] <= 90, True)
fc.bc.cj_call, fc.SLEEP, fc.JITTER = orig

# CLI: --assemble nepotřebuje token; bez přepínačů se chová jako dřív (bez tokenu konec s chybou)
cfg = os.path.join(tmp, 'shops.json')
json.dump([{'shop': 'Answear.cz', 'cj': '4327375', 'cjFile': path}], open(cfg, 'w', encoding='utf-8'))
old_cfg, fc.CONFIG, old_parts, fc.PARTS = fc.CONFIG, cfg, fc.PARTS, parts
tok = os.environ.pop('CJ_TOKEN', None)
fc.save(fc.part_path(sh, 1, parts), sh, [item(i, 200 + i) for i in range(6000)], 6000, True, {'lo': 0.0, 'hi': 1000000.0, 'k': 1, 'of': 1})
try: fc.main(['--assemble', 'Answear.cz']); r = 'ok'
except SystemExit as e: r = f'exit {e.code}'
check('--assemble bez tokenu', (r, len(fc.read(path)['items'])), ('ok', 6000))
for argv in ([], ['Answear.cz'], ['--slice', '1/2', 'Answear.cz']):
    try: fc.main(argv); r = 'ok'
    except SystemExit as e: r = f'exit {e.code}'
    check(f'bez tokenu {argv}', r, 'exit 1')
fc.CONFIG, fc.PARTS = old_cfg, old_parts
if tok: os.environ['CJ_TOKEN'] = tok

print('\nVŠE V POŘÁDKU' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
