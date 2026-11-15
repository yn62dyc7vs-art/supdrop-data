"""Obrázky 8a.pl (Cloudflare challenge) se přepisují na 8a.cz, i v katalogu z feedu: python3 tests/test_img.py"""
import io, os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

P = '/media/catalog/product/n/a/namiot-1794394873.webp'
check('https 8a.pl', bc.fix_img('https://8a.pl' + P), 'https://8a.cz' + P)
check('www + http', bc.fix_img('http://www.8a.pl' + P), 'https://8a.cz' + P)
check('dotaz zůstane', bc.fix_img('https://8a.pl' + P + '?v=2'), 'https://8a.cz' + P + '?v=2')
check('8a.cz beze změny', bc.fix_img('https://8a.cz' + P), 'https://8a.cz' + P)
check('cizí doména (cdn.8a.pl.example) beze změny', bc.fix_img('https://x8a.pl' + P), 'https://x8a.pl' + P)
check('prázdné', bc.fix_img(''), '')

G = 'xmlns:g="http://base.google.com/ns/1.0"'
FEED = (f'<?xml version="1.0"?><rss {G}><channel><item><g:id>1</g:id><g:title>Běžecké boty adidas Pureboost 22</g:title>'
        '<g:brand>Adidas</g:brand><g:product_type>Obuv &gt; Běžecké boty</g:product_type><g:link>https://8a.cz/pureboost</g:link>'
        '<g:image_link>https://8a.pl' + P + '</g:image_link><g:price>1955 CZK</g:price><g:sale_price>1955 CZK</g:sale_price>'
        '<g:availability>in stock</g:availability><g:condition>new</g:condition></item></channel></rss>').encode('utf-8')
now = datetime(2026, 11, 15, 10, 0, tzinfo=bc.TZ)
prods, state, rep = bc.build([{'shop': '8a.cz', 'format': 'google', 'bid': 'd2eba940', 'trust': 8}], {'8a.cz': 'https://feed'}, {}, now,
                             open_fn=lambda u: io.BytesIO(FEED), log=lambda *a: None)
check('katalog: obrázek z 8a.cz', [p['img'] for p in prods], ['https://8a.cz' + P])

print('\nVŠE OK' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
