"""Kontrola Allegra z Tradedoubleru (fetch_td.py) a jeho načtení do šupu: python tests/test_td.py"""
import gzip, io, json, os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc
import fetch_td as td

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

# skutečný tvar odkazu z API (5. 11. 2026)
LINK = ('https://pdt.tradedoubler.com/click?a(3038439)p(344855)product(257027-13654694856)ttid(19)url(https%3A%2F%2Fallegro.cz%2Faffiliate'
        '%3Fredirect_url%3Dhttps%3A%2F%2Fallegro.cz%2Fnabidka%2Fotocny-stolni-drzak-pro-lcd-monitor-fellowes-13654694856%3Futm_feed%3Dbcad'
        '%26utm_medium%3Dafiliacja%26utm_source%3Dctr_b%26utm_campaign%3D1613)')
def prod(name, path, price, smart=True, avail='in stock', sid='1', link=None):
    f = [{'name': 'availability', 'value': avail}, {'name': 'gtin', 'value': '08885020104189'}]
    if smart is not None: f.append({'name': 'smart', 'value': 'true'} if smart else {'name': 'smart', 'value': 'false'})
    return {'name': name, 'categories': [{'name': path}], 'fields': f, 'productImage': {'url': 'https://a.allegroimg.com/original/x'},
            'offers': [{'sourceProductId': sid, 'priceHistory': [{'price': {'value': str(price), 'currency': 'CZK'}}],
                        'productUrl': link or LINK.replace('13654694856', sid)}]}

check('čistá adresa nabídky', td.offer_url(LINK), 'https://allegro.cz/nabidka/otocny-stolni-drzak-pro-lcd-monitor-fellowes-13654694856')
check('cizí adresa se nebere', td.offer_url('https://example.com/x'), '')
check('fid a token z adresy feedu', td.parse_feed_url('https://api.tradedoubler.com/1.0/products.xml;page=1;pageSize=100;fid=257027?token=ABC123'), ('257027', 'ABC123'))
SPK = 'Elektronika > Audio, video a domácí spotřebiče > Přenosné audio > Přenosné reproduktory'
check('reproduktor JBL projde', (td.item(prod('Reproduktor JBL Flip 6 černý', SPK, 2490)) or {}).get('price'), 2490.0)
check('auto-moto pryč', td.item(prod('Autorádio 2DIN', 'Auto-moto > Zařízení a příslušenství do auta > Autorádia', 3000)), None)
check('bez Smart pryč', td.item(prod('Reproduktor JBL Flip 6', SPK, 2490, smart=None)), None)
check('Smart false pryč', td.item(prod('Reproduktor JBL Flip 6', SPK, 2490, smart=False)), None)
check('neskladem pryč', td.item(prod('Reproduktor JBL Flip 6', SPK, 2490, avail='out of stock')), None)
check('levné pryč', td.item(prod('Reproduktor JBL Go', SPK, 99)), None)
check('toner pryč', td.item(prod('Toner pro Lexmark MS310', 'Elektronika > Počítače > Tiskárny a skenery > Tonery > Náhradní', 900)), None)
check('LEGO projde', bool(td.item(prod('LEGO Technic 42171 Mercedes', 'Děti > Hračky > Stavebnice > LEGO > Technic', 4999))), True)

# dělení podle ceny: 2500 produktů, API vrátí nejvýš 1000 na dotaz
CAT = [prod(f'Reproduktor JBL Flip {i}', SPK, 500 + i * 3, sid=str(100000 + i)) for i in range(2500)]
calls = []
def fake(fid, token, lo, hi, size):
    calls.append((lo, hi, size))
    hit = [p for p in CAT if lo <= float(p['offers'][0]['priceHistory'][0]['price']['value']) <= hi]
    return {'productHeader': {'totalHits': len(hit)}, 'products': hit[:size]}
items, n, nc = td.fetch_all('257027', 'X', call=fake, log=lambda *a: None)
check('dělení podle ceny: všechny produkty', len(items), 2500)
check('žádný dotaz nad 1000', max(c[2] for c in calls) <= 1000, True)

# načtení do šupu
check('značka z názvu', [bc.guess_brand(x) for x in ['Reproduktor JBL Flip 6', 'Apple iPhone 16 128GB', 'LEGO Technic 42171', 'Držák na TV']],
      ['JBL', 'Apple', 'LEGO', ''])
now = datetime(2026, 11, 5, 10, 0, tzinfo=bc.TZ)
def cache(at): return gzip.compress(json.dumps({'at': at, 'items': [td.item(prod('Reproduktor JBL Flip 6 černý', SPK, 2490, sid='777'))]}).encode())
shops = [{'shop': 'Allegro', 'net': 'dognet', 'format': 'td', 'td': 'cache/neexistuje.json.gz', 'trust': 6, 'low': True}]
prods, state, rep = bc.build(shops, {'Allegro': 'cache/allegro.json.gz'}, {}, now, open_fn=lambda u: io.BytesIO(cache('2026-11-05T03:00+01:00')), log=lambda *a: None)
d = prods[0] if prods else {}
check('Allegro v šupu', (d.get('shop'), d.get('brand'), d.get('cat'), d.get('now'), d.get('aff')), ('Allegro', 'JBL', 'elektronika', 2490.0, True))
check('proklik přes Dognet', d.get('url', '').startswith('https://go.dognet.com/?chid=') and 'allegro.cz%2Fnabidka%2F' in d.get('url', ''), True)
prods, state, rep = bc.build(shops, {'Allegro': 'x'}, {}, now, open_fn=lambda u: io.BytesIO(cache('2026-11-01T03:00+01:00')), log=lambda *a: None)
check('stará data se nezobrazí', (len(prods), 'starší než 3 dny' in rep[0]), (0, True))
prods, state, rep = bc.build(shops, {}, {}, now, log=lambda *a: None)
check('bez souboru jen hlášení', rep, ['Allegro: chybí data z denního stahování (větev allegro)'])
sys.exit(0 if ok else 1)
