"""Kontrola Allegra z feedů Dognetu (fetch_td.py) a jeho načtení do šupu: python tests/test_td.py"""
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

# skutečný tvar odkazu z feedu (5. 11. 2026)
LINK = ('https://allegro.cz/affiliate?redirect_url=https://allegro.cz/nabidka/reproduktor-jbl-flip-6-cerny-17887138612'
        '?utm_feed%3D6f067061%26utm_medium%3Dafiliacja%26utm_source%3Dctr_b%26utm_campaign%3D146013d2')
TD_LINK = ('https://pdt.tradedoubler.com/click?a(3038439)p(344855)product(257027-13654694856)ttid(19)url(https%3A%2F%2Fallegro.cz%2Faffiliate'
           '%3Fredirect_url%3Dhttps%3A%2F%2Fallegro.cz%2Fnabidka%2Fotocny-stolni-drzak-fellowes-13654694856%3Futm_feed%3Dbcad)')
def row(name, path, price, smart=True, avail='in stock', sid='17887138612', stav='nové', brand='JBL'):
    a = [{'name': 'Stav', 'values': [{'value': stav}]}] if stav else []
    if brand: a.append({'name': 'Značka', 'values': [{'value': brand}]})
    return {'id': sid, 'categoryId': '1', 'name': name, 'description': '<p>…</p>', 'imageLink': 'https://a.allegroimg.com/original/x',
            'price': f'{price:.2f}', 'itemLink': LINK.replace('17887138612', sid), 'availability': avail, 'gtin': '05904041377693',
            'attributes': {'attribute': a}, 'customLabel0': '0', 'customLabel2': 'smart' if smart else '', 'categoryNamePath': path}

check('čistá adresa nabídky', td.offer_url(LINK), 'https://allegro.cz/nabidka/reproduktor-jbl-flip-6-cerny-17887138612')
check('i z odkazu Tradedoubleru', td.offer_url(TD_LINK), 'https://allegro.cz/nabidka/otocny-stolni-drzak-fellowes-13654694856')
check('cizí adresa se nebere', td.offer_url('https://example.com/x'), '')
SPK = 'Elektronika > Audio, video a domácí spotřebiče > Přenosné audio > Přenosné reproduktory'
x = td.item(row('Reproduktor JBL Flip 6 černý', SPK, 2490)) or {}
check('reproduktor JBL projde', (x.get('price'), x.get('brand')), (2490.0, 'JBL'))
check('auto-moto pryč', td.item(row('Autorádio 2DIN', 'Auto-moto > Zařízení a příslušenství do auta > Autorádia', 3000)), None)
check('bez Smart pryč', td.item(row('Reproduktor JBL Flip 6', SPK, 2490, smart=False)), None)
check('neskladem pryč', td.item(row('Reproduktor JBL Flip 6', SPK, 2490, avail='out of stock')), None)
check('použité pryč', td.item(row('Reproduktor JBL Flip 6', SPK, 2490, stav='použité')), None)
check('levné pryč', td.item(row('Reproduktor JBL Go', SPK, 99)), None)
check('toner pryč', td.item(row('Toner pro Lexmark MS310', 'Elektronika > Počítače > Tiskárny a skenery > Tonery > Náhradní', 900)), None)
check('LEGO projde', bool(td.item(row('LEGO Technic 42171 Mercedes', 'Děti > Hračky > Stavebnice > LEGO > Technic', 4999, brand='LEGO'))), True)
check('značka „žádná“ se nebere', (td.item(row('Reproduktor bezdrátový', SPK, 900, brand='žádná')) or {}).get('brand'), '')

# čtení feedu po řádcích (bajty jako z HTTP), duplicity a rozbité řádky
lines = [json.dumps(row(f'Reproduktor JBL Flip {i}', SPK, 500 + i, sid=str(100 + i))).encode() + b'\n' for i in range(5)]
lines += [lines[0], b'{rozbity', json.dumps(row('Autorádio', 'Auto-moto > X', 900, sid='9')).encode() + b'\n']
out, seen = [], set()
check('čtení feedu', (td.read_feed(iter(lines), out, seen), len(out)), ((8, True), 5))

# načtení do šupu
check('značka z názvu', [bc.guess_brand(x) for x in ['Reproduktor JBL Flip 6', 'Apple iPhone 16 128GB', 'LEGO Technic 42171', 'Držák na TV']],
      ['JBL', 'Apple', 'LEGO', ''])
now = datetime(2026, 11, 5, 10, 0, tzinfo=bc.TZ)
def cache(at): return gzip.compress(json.dumps({'at': at, 'items': [td.item(row('Reproduktor JBL Flip 6 černý', SPK, 2490, sid='777'))]}).encode())
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
