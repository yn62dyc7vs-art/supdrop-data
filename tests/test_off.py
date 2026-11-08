"""Vypnutý obchod („off“ v shops.json) se nestahuje a v katalogu nic nemá: python3 tests/test_off.py"""
import io, os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc

G = 'xmlns:g="http://base.google.com/ns/1.0"'
FEED = (f'<?xml version="1.0"?><rss {G}><channel><item><g:id>1</g:id><g:title>Běžecké boty adidas Pureboost 22</g:title>'
        '<g:brand>Adidas</g:brand><g:product_type>Obuv &gt; Běžecké boty</g:product_type><g:link>https://8a.cz/pureboost</g:link>'
        '<g:image_link>https://8a.cz/media/a.jpg</g:image_link><g:price>1955 CZK</g:price><g:sale_price>1955 CZK</g:sale_price>'
        '<g:availability>in stock</g:availability><g:condition>new</g:condition></item></channel></rss>').encode('utf-8')

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

now = datetime(2026, 11, 8, 10, 0, tzinfo=bc.TZ)
opened = []
def open_fn(url):
    opened.append(url); return io.BytesIO(FEED)

shop = {'shop': '8a.cz', 'format': 'google', 'bid': 'd2eba940', 'trust': 8}
prods, state, rep = bc.build([shop], {'8a.cz': 'https://feed'}, {}, now, open_fn=open_fn, log=lambda *a: None)
check('zapnutý obchod má produkt', len(prods), 1)

opened.clear()
off = dict(shop, off='Cloudflare blokuje mimo Polsko (8. 11.)')
prods, state, rep = bc.build([off], {'8a.cz': 'https://feed'}, {}, now, open_fn=open_fn, log=lambda *a: None)
check('vypnutý obchod nemá produkty', len(prods), 0)
check('vypnutý obchod se nestahuje', opened, [])
check('hlášení uvádí důvod', rep, ['8a.cz: vypnuto (Cloudflare blokuje mimo Polsko (8. 11.))'])

print('\nVŠE OK' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
