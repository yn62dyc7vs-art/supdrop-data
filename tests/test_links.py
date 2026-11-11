"""Denní kontrola odkazů a obrázků (check_links.py) a vynechání vyřazených obchodů: python tests/test_links.py"""
import io, os, sys
from datetime import date, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc
import check_links as cl

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

cl.SLEEP = lambda s: None
log = lambda *a: None

# adresa produktu z partnerského odkazu (partnerský odkaz se neotevírá)
check('eHUB', cl.target('https://ehub.cz/system/scripts/click.php?a_aid=2659764d&a_bid=55927a45&desturl=https%3A%2F%2Fwww.coolboty.cz%2Fpenny'),
      'https://www.coolboty.cz/penny')
check('Dognet', cl.target('https://go.dognet.com/?chid=s8IEiPDU&url=https%3A%2F%2Fwww.kkboty.cz%2Fcepka%2F%3FvariantId%3D1'),
      'https://www.kkboty.cz/cepka/?variantId=1')
check('CJ', cl.target('https://www.tkqlhce.com/click-101903676-17123912?url=https%3A%2F%2Fanswear.cz%2Fp%2Fkabelka'), 'https://answear.cz/p/kabelka')
check('eHUB → prf.hn → obchod', cl.target('https://ehub.cz/system/scripts/click.php?a_aid=x&a_bid=y&desturl=' +
      'https%3A%2F%2Fprf.hn%2Fclick%2Fcamref%3A1100l%2Fdestination%3Ahttps%253A%252F%252Fwww.emp-shop.cz%252Fp%252Ftricko%252F1.html'),
      'https://www.emp-shop.cz/p/tricko/1.html')
check('prf.hn s nezakódovanou adresou (skutečný EMP)', cl.target('https://ehub.cz/system/scripts/click.php?a_aid=x&a_bid=y&desturl=' +
      'https%3A%2F%2Fprf.hn%2Fclick%2Fcamref%3A1100l8wRY%2Fcreativeref%3A1101l34545%2Fdestination%3Ahttps%3A%2F%2Fwww.emp-shop.cz%2Fp%2Fmikina%2F5.html'),
      'https://www.emp-shop.cz/p/mikina/5.html')
check('přímý odkaz', cl.target('https://eobuv.cz/p/kabelka?utm_source=supdrop'), 'https://eobuv.cz/p/kabelka?utm_source=supdrop')
check('CJ bez adresy', cl.target('https://www.tkqlhce.com/click-101903676-17123912'), None)

# stránka produktu
def fake(code, final=None, ctype='text/html', body=b'<title>Kabelka | Shop</title>'):
    return lambda url, limit, headers=None: (code, final or url, ctype, body)
U = 'https://www.shop.cz/p/kabelka-123'
check('200 = ok', cl.check_page(U, fake(200)), ('ok', ''))
check('404 = pryč', cl.check_page(U, fake(404))[0], 'pryč')
check('úvodní stránka', cl.check_page(U, fake(200, 'https://www.shop.cz/'))[0], 'úvod')
check('úvodní /cs/', cl.check_page(U, fake(200, 'https://www.shop.cz/cs/'))[0], 'úvod')
check('kategorie není úvod', cl.check_page(U, fake(200, 'https://www.shop.cz/kabelky/'))[0], 'ok')
check('stránka nenalezena v titulku', cl.check_page(U, fake(200, body=b'<title>Str\xc3\xa1nka nebyla nalezena</title>'))[0], 'pryč')
check('403 = nelze ověřit', cl.check_page(U, fake(403))[0], 'nelze')
check('cizí web (Shopcom → cnn.com) = nelze', cl.check_page(U, fake(200, 'https://edition.cnn.com/'))[0], 'nelze')
check('subdoména je stejný web', cl.check_page(U, fake(200, 'https://shop.cz/p/kabelka-123'))[0], 'ok')
def boom(url, limit, headers=None): raise TimeoutError()
check('výpadek = chyba', cl.check_page(U, boom)[0], 'chyba')

# obrázek
I = 'https://cdn.shop.cz/img/kabelka.jpg'
check('obrázek ok', cl.check_img(I, fake(206, ctype='image/jpeg')), ('ok', ''))
check('obrázek 404', cl.check_img(I, fake(404))[0], 'špatný')
check('HTML místo obrázku', cl.check_img(I, fake(200, ctype='text/html'))[0], 'špatný')
check('zástupný obrázek Sport365', cl.check_img('https://www.sport365.cz/pictures/products/02-nenalezeno-madmax.jpg', fake(200, ctype='image/jpeg'))[0], 'špatný')
check('obrázek 403 = nelze', cl.check_img(I, fake(403))[0], 'nelze')

# verdikt obchodu
R = lambda p, i='ok': {'page': (p, ''), 'img': (i, '')}
check('vše ok', cl.verdict([R('ok')] * 5)[0], 'ok')
check('4 z 5 pryč', cl.verdict([R('pryč')] * 4 + [R('ok')]), ('špatný', '4 z 5 odkazů nefunguje (404 / nenalezeno)'))
check('úvodní stránka', cl.verdict([R('úvod')] * 5)[1], '5 z 5 odkazů vede na úvodní stránku')
check('2 z 5 pryč je ok', cl.verdict([R('pryč')] * 2 + [R('ok')] * 3)[0], 'ok')
check('obrázky', cl.verdict([R('ok', 'špatný')] * 4 + [R('ok')])[1], '4 z 5 obrázků nefunguje')
check('vše blokované', cl.verdict([R('nelze', 'nelze')] * 5)[0], 'nelze')

# běh: vyřazení až druhý den po sobě, návrat po opravě, ochrana proti hromadnému vyřazení
META = {'checkedAt': '2026-11-11T12:01', 'aid': '2659764d', 'bids': {'Zlý.cz': 'b1', 'Dobrý.cz': 'b2'}, 'pre': {}}
def rec(shop, i): return {'i': f'{shop}-{i}', 'c': 'obleceni', 'n': f'Tričko {i}', 's': shop, 'u': f'https://www.{shop.lower()}/p/{i}', 'g': f'https://www.{shop.lower()}/img/{i}.jpg', 'p': 500, 'e': 1}
DATA = {s: [rec(s, i) for i in range(8)] for s in ('Zlý.cz', 'Dobrý.cz')}
prev = lambda s: (DATA.get(s), META)
broken = {'zlý.cz'}
def page(url): return ('pryč', '404') if any(b in url for b in broken) else ('ok', '')
def img(url): return ('ok', '')
shops = [{'shop': 'Zlý.cz'}, {'shop': 'Dobrý.cz'}]
d1 = cl.run(shops, prev, {}, date(2026, 11, 11), page, img, log)
check('1. den: špatný, ale nevyřazen', (d1['last']['Zlý.cz']['stav'], d1['auto_off']), ('špatný', {}))
d2 = cl.run(shops, prev, d1, date(2026, 11, 12), page, img, log)
check('2. den po sobě: vyřazen', (list(d2['auto_off']), d2['auto_off']['Zlý.cz']['od'], len(d2['auto_off']['Zlý.cz']['urls'])), (['Zlý.cz'], '2026-11-12', 5))
broken.clear()
d3 = cl.run(shops, prev, d2, date(2026, 11, 13), page, img, log)
check('opravené odkazy: zpět', d3['auto_off'], {})
MANY = {f'Obchod{i}.cz': [rec(f'Obchod{i}.cz', k) for k in range(5)] for i in range(10)}
d4 = cl.run([{'shop': s} for s in MANY], lambda s: (MANY.get(s), META), {'history': {s: {'2026-11-11': 'špatný'} for s in MANY}},
            date(2026, 11, 12), lambda u: ('pryč', '404'), img, log)
check('hromadné selhání (10 z 10): nikdo vyřazen', d4['auto_off'], {})
d5 = cl.run([{'shop': 'Zlý.cz', 'linkcheckOk': '2026-11-12'}, {'shop': 'Dobrý.cz'}], prev, d2, date(2026, 11, 13), lambda u: ('pryč', '404') if 'zlý' in u else ('ok', ''), img, log)
check('ruční odblokování', 'Zlý.cz' in d5['auto_off'], False)
d6 = cl.run([{'shop': 'Zlý.cz', 'linkcheck': False}, {'shop': 'Dobrý.cz'}], prev, d2, date(2026, 11, 13), page, img, log)
check('linkcheck: false se nekontroluje', ('Zlý.cz' in d6['last'], d6['auto_off']), (False, {}))

# hodinový běh vynechá vyřazený obchod
G = 'xmlns:g="http://base.google.com/ns/1.0"'
FEED = (f'<?xml version="1.0"?><rss {G}><channel><item><g:id>1</g:id><g:title>Běžecké boty adidas Pureboost 22</g:title>'
        '<g:brand>Adidas</g:brand><g:product_type>Obuv &gt; Běžecké boty</g:product_type><g:link>https://shop.cz/pureboost</g:link>'
        '<g:image_link>https://shop.cz/a.jpg</g:image_link><g:price>1955 CZK</g:price>'
        '<g:availability>in stock</g:availability><g:condition>new</g:condition></item></channel></rss>').encode('utf-8')
now = datetime(2026, 11, 12, 15, 0, tzinfo=bc.TZ)
AO = {'Shop.cz': {'od': '2026-11-12', 'proc': '5 z 5 odkazů nefunguje (404 / nenalezeno)'}}
sh = {'shop': 'Shop.cz', 'format': 'google', 'bid': 'abc', 'trust': 8}
prods, state, rep = bc.build([sh], {'Shop.cz': 'x'}, {}, now, open_fn=lambda u: io.BytesIO(FEED), log=log, auto_off=AO)
check('vyřazený obchod se vynechá', (len(prods), rep[0]), (0, 'Shop.cz: vypnuto kontrolou odkazů od 2026-11-12 (5 z 5 odkazů nefunguje (404 / nenalezeno))'))
prods, state, rep = bc.build([dict(sh, linkcheckOk='2026-11-12')], {'Shop.cz': 'x'}, {}, now, open_fn=lambda u: io.BytesIO(FEED), log=log, auto_off=AO)
check('ručně odblokovaný se načte', len(prods), 1)

print('\nVŠE V POŘÁDKU' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
