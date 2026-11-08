"""Výpadek feedu: opakované stažení a převzetí posledních dat (max. 6 hodin): python3 tests/test_fallback.py"""
import io, os, sys, urllib.error
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

G = 'xmlns:g="http://base.google.com/ns/1.0"'
FEED = (f'<?xml version="1.0"?><rss {G}><channel><item><g:id>1</g:id><g:title>Běžecké boty adidas Pureboost 22</g:title>'
        '<g:brand>Adidas</g:brand><g:product_type>Obuv &gt; Běžecké boty</g:product_type><g:link>https://shop.cz/pureboost</g:link>'
        '<g:image_link>https://shop.cz/a.jpg</g:image_link><g:price>1955 CZK</g:price>'
        '<g:availability>in stock</g:availability><g:condition>new</g:condition></item></channel></rss>').encode('utf-8')
now = datetime(2026, 11, 8, 15, 0, tzinfo=bc.TZ)
SHOP = {'shop': 'Shop.cz', 'format': 'google', 'bid': 'abc123', 'trust': 8}
def fail(url): raise urllib.error.HTTPError(url, 500, 'Internal Server Error', {}, None)
def good(url): return io.BytesIO(FEED)
log = lambda *a: None

# poslední zveřejněná data (v2/p): jeden produkt s odkazem přes eHUB a jeden s hotovým odkazem (e=1)
META = {'checkedAt': '2026-11-08T12:01', 'aid': '2659764d', 'bids': {'Shop.cz': 'abc123'},
        'pre': {'Shop.cz': ['https://shop.cz/', 'https://shop.cz/img/']}}
RECS = [{'i': 'shop-cz-1', 'c': 'tenisky', 'sb': 'bezecke', 'b': 'Adidas', 'n': 'Pureboost 22', 's': 'Shop.cz', 'u': 'pureboost',
         'g': 'a.jpg', 'p': 1955.0, 'd': 20, 't': 71, 'w': 2449.0, 'bs': 'obchod', 'ld': 8, 'l': 1900.0, 'hs': [7, 2449.0, 1, 1955.0],
         'dy': 3, 'gd': 'u', 'v': 'Skladem: 42, 43'},
        {'i': 'shop-cz-2', 'c': 'obleceni', 'sb': 'mikiny', 'b': 'Nike', 'n': 'Mikina', 's': 'Shop.cz', 'u': '~https://partner.example/x?u=1',
         'g': '~https://cdn.example/b.jpg', 'p': 990.0, 'd': 0, 't': 40, 'e': 1, 'x': 1, 'r': 'Není skladem.'}]
def prev(shop): return (RECS, META) if shop == 'Shop.cz' else (None, None)

# 1) úspěšný běh si pamatuje čas posledních čerstvých dat obchodu
p, st, rep = bc.build([SHOP], {'Shop.cz': 'https://feed'}, {}, now, open_fn=good, log=log, prev_fn=prev)
check('úspěch: produkt z feedu', len(p), 1)
check('úspěch: čas čerstvých dat', st['fresh'], {'Shop.cz': '2026-11-08T15:00'})

# 2) feed selže, poslední data jsou 3 h stará -> převezmou se
state = {'fresh': {'Shop.cz': '2026-11-08T12:01'}, 'last': {'shop-cz:1': 1955.0, 'jiny-cz:9': 100.0}}
p, st, rep = bc.build([SHOP], {'Shop.cz': 'https://feed'}, state, now, open_fn=fail, log=log, prev_fn=prev)
check('výpadek: převzaté produkty', [d['id'] for d in p], ['shop-cz-1', 'shop-cz-2'])
d = p[0]
check('odkaz eHUB složený zpět', d['url'], 'https://ehub.cz/system/scripts/click.php?a_aid=2659764d&a_bid=abc123&desturl=https%3A%2F%2Fshop.cz%2Fpureboost')
check('obrázek', d['img'], 'https://shop.cz/img/a.jpg')
check('cena, sleva, původní cena', (d['now'], d['disc'], d['was'], d['basis']), (1955.0, 20, 2449.0, 'obchod'))
check('graf ceny', d['hist'], [[7, 2449.0], [1, 1955.0]])
check('kontrola času dat', d['checked'], '2026-11-08T12:01')
check('druhý: hotový odkaz a neprošel', (p[1]['url'], p[1]['aff'], p[1]['pass'], p[1]['img']), ('https://partner.example/x?u=1', True, False, 'https://cdn.example/b.jpg'))
check('hlášení', rep, ['Shop.cz: chyba feedu (HTTPError: HTTP Error 500: Internal Server Error) – použita data z 12:01 (2 produktů)'])
check('čas čerstvých dat se nemění', st['fresh'], {'Shop.cz': '2026-11-08T12:01'})
check('předchozí ceny obchodu zůstávají', st['last'], {'shop-cz:1': 1955.0})

# 3) poslední data starší než 6 h -> obchod vypadne
state = {'fresh': {'Shop.cz': '2026-11-08T08:30'}}
p, st, rep = bc.build([SHOP], {'Shop.cz': 'https://feed'}, state, now, open_fn=fail, log=log, prev_fn=prev)
check('stará data: nic', len(p), 0)
check('stará data: hlášení', rep, ['Shop.cz: chyba feedu (HTTPError: HTTP Error 500: Internal Server Error) – poslední data z 08:30 jsou starší než 6 h'])

# 4) bez záznamu o čerstvých datech rozhoduje čas zveřejněných dat
p, st, rep = bc.build([SHOP], {'Shop.cz': 'https://feed'}, {}, now, open_fn=fail, log=log, prev_fn=prev)
check('bez záznamu: převzato', len(p), 2)

# 5) prázdný feed se bere jako výpadek
p, st, rep = bc.build([SHOP], {'Shop.cz': 'https://feed'}, {}, now, open_fn=lambda u: io.BytesIO(b'<rss></rss>'), log=log, prev_fn=prev)
check('prázdný feed: převzato', len(p), 2)

# 6) open_feed zkusí přechodnou chybu znovu
calls = []
class Resp(io.BytesIO):
    headers = {}
def fake_urlopen(req, timeout=0):
    calls.append(1)
    if len(calls) < 3: raise urllib.error.HTTPError(req.full_url, 503, 'Busy', {}, None)
    return Resp(b'ok')
orig, bc.urllib.request.urlopen = bc.urllib.request.urlopen, fake_urlopen
bc.SLEEP = lambda s: None
try:
    r = bc.open_feed('https://feed.example/x.xml')
    check('3. pokus uspěl', (len(calls), r.read()), (3, b'ok'))
    calls.clear()
    def nf(req, timeout=0): calls.append(1); raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)
    bc.urllib.request.urlopen = nf
    try: bc.open_feed('https://feed.example/x.xml'); check('404 bez opakování', 'bez chyby', 'chyba')
    except urllib.error.HTTPError: check('404 se neopakuje', len(calls), 1)
finally:
    bc.urllib.request.urlopen = orig

# 7) pomalý obchod („every“: 3): do 3 h se nestahuje a převezmou se poslední data
SLOW = dict(SHOP, every=3)
opened = []
def track(url): opened.append(url); return io.BytesIO(FEED)
p, st, rep = bc.build([SLOW], {'Shop.cz': 'https://feed'}, {'fresh': {'Shop.cz': '2026-11-08T13:30'}}, now, open_fn=track, log=log, prev_fn=prev)
check('every: nestahuje se', opened, [])
check('every: převzato', (len(p), rep), (2, ['Shop.cz: data z 13:30 (2 produktů, obnova jednou za 3 h)']))
check('every: čas dat se nemění', st['fresh'], {'Shop.cz': '2026-11-08T13:30'})
p, st, rep = bc.build([SLOW], {'Shop.cz': 'https://feed'}, {'fresh': {'Shop.cz': '2026-11-08T12:10'}}, now, open_fn=track, log=log, prev_fn=prev)
check('every: po 3 h se stáhne', (len(opened), len(p), st['fresh']), (1, 1, {'Shop.cz': '2026-11-08T15:00'}))
opened.clear()
p, st, rep = bc.build([SLOW], {'Shop.cz': 'https://feed'}, {}, now, open_fn=track, log=log, prev_fn=prev)
check('every: bez záznamu se stáhne', len(opened), 1)

print('\nVŠE OK' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
