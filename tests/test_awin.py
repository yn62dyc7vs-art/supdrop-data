"""Kontrola čtení feedů z Awinu (eObuv.cz, Myprotein přes eHUB): python tests/test_awin.py"""
import csv, gzip, io, os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc

COLS = bc.AWIN_COLS.split(',')
EOBUV = [  # skutečné řádky z feedu eObuv.cz (2. 11. 2026), zkrácené
    dict(merchant_product_id='0000304830082-44_5', product_name='Sneakersy Converse Star Player 76 Puff A11483C Černá', brand_name='Converse',
         product_type='eobuv > Pánské > Tenisky > Nízké tenisky', search_price='1409.00', base_price='2390,00', in_stock='1',
         product_GTIN='0194435327211', **{'Fashion:size': '44_5', 'Fashion:suitable_for': 'Unisex'}, custom_1='adult', condition='new',
         merchant_deep_link='https://eobuv.cz/p/sneakersy-converse-star-player-76-puff-a11483c-cerna-0000304830082',
         merchant_image_url='https://img.eobuwie.cloud/a.jpg'),
    dict(merchant_product_id='0000304830082-42', product_name='Sneakersy Converse Star Player 76 Puff A11483C Černá', brand_name='Converse',
         product_type='eobuv > Pánské > Tenisky > Nízké tenisky', search_price='1409.00', base_price='2390,00', in_stock='1',
         product_GTIN='0194435327198', **{'Fashion:size': '42', 'Fashion:suitable_for': 'Unisex'}, custom_1='adult', condition='new',
         merchant_deep_link='https://eobuv.cz/p/sneakersy-converse-star-player-76-puff-a11483c-cerna-0000304830082',
         merchant_image_url='https://img.eobuwie.cloud/a.jpg'),
    dict(merchant_product_id='0000306171527-37', product_name='Sneakersy Skechers Slip-Ins: Hyper-Quest 404212L/GRMT Zelená', brand_name='Skechers',
         product_type='eobuv > Dětské > Chlapec > Tenisky > Nízké tenisky', search_price='1219.00', base_price='1499,00', in_stock='1',
         **{'Fashion:size': '37', 'Fashion:suitable_for': 'Male'}, custom_1='kids', condition='new',
         merchant_deep_link='https://eobuv.cz/p/x-0000306171527', merchant_image_url='https://img.eobuwie.cloud/b.jpg'),
    dict(merchant_product_id='5906751515327-41', product_name='Bačkory G-Star Raw CEO-WSS21711-01A Béžová', brand_name='G-Star',
         product_type='eobuv > Dámské > Papuče', search_price='549.00', base_price='549,00', in_stock='1',
         **{'Fashion:size': '41', 'Fashion:suitable_for': 'Female'}, custom_1='adult', condition='new',
         merchant_deep_link='https://eobuv.cz/p/x-5906751515327', merchant_image_url='https://img.eobuwie.cloud/c.jpg'),
    dict(merchant_product_id='0000306370500-41', product_name='Polobotky Polo Ralph Lauren Jett Apron 803P15860001 Černá', brand_name='Polo Ralph Lauren',
         product_type='eobuv > Pánské > Polobotky > Každodenní polobotky', search_price='6399.00', base_price='6399,00', in_stock='1',
         **{'Fashion:size': '41', 'Fashion:suitable_for': 'Male'}, custom_1='adult', condition='new',
         merchant_deep_link='https://eobuv.cz/p/x-0000306370500', merchant_image_url='https://img.eobuwie.cloud/d.jpg'),
]
MYPROTEIN = [
    dict(merchant_product_id='10529384', product_name='Impact Whey Protein - 1kg - Čokoláda', brand_name='MyProtein',
         merchant_product_category_path='Sports and Nutrition > Sportovní výživa', merchant_category='Sports Nutrition',
         search_price='709.00', rrp_price='1189.00', base_price='709.00', in_stock='1', product_GTIN='5055534303856', condition='new',
         merchant_deep_link='https://www.myprotein.cz/p/10529384', merchant_image_url='https://static.thcdn.com/e.jpg'),
    dict(merchant_product_id='10530000', product_name='Tričko Essentials', brand_name='MyProtein',
         merchant_product_category_path='Apparel > Oděvy > T-Shirts & Tops', search_price='399.00', rrp_price='399.00', in_stock='0',
         condition='new', merchant_deep_link='https://www.myprotein.cz/p/10530000', merchant_image_url='https://static.thcdn.com/f.jpg'),
]

def feed(rows):
    b = io.StringIO(); w = csv.DictWriter(b, COLS, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    return io.BytesIO(gzip.compress(b.getvalue().encode('utf-8')))

ok = True
def check(name, got, want):
    global ok
    good = got == want; ok &= good
    print(('OK   ' if good else 'CHYBA') + f' {name}: {got!r}' + ('' if good else f' (čekáno {want!r})'))

shops = [{'shop': 'eObuv.cz', 'format': 'awin', 'awin': '75859', 'bid': '568a437d', 'trust': 8, 'orig': 'base_price'},
         {'shop': 'Myprotein', 'format': 'awin', 'awin': '24533', 'bid': '04890426', 'trust': 8, 'orig': '-'}]
urls = {}
def open_fn(url):
    urls[url] = True
    return feed(EOBUV if '/fid/75859/' in url else MYPROTEIN)

prods, state, rep = bc.build(shops, {}, {}, datetime(2026, 11, 2, 10, 0, tzinfo=bc.TZ), open_fn=open_fn, log=lambda *a: None, awin_key='KLIC')
by = {p['name']: p for p in prods}
conv = next((p for p in prods if 'Converse' in p['name']), None)
check('bez klíče se feed nestahuje', bc.build(shops, {}, {}, datetime(2026, 11, 2, tzinfo=bc.TZ), open_fn=open_fn, log=lambda *a: None)[2][0], 'eObuv.cz: chybí AWIN_KEY')
check('adresa feedu obsahuje klíč a gzip', all('/apikey/KLIC/' in u and '/compression/gzip/' in u for u in urls), True)
check('Converse: dvě velikosti = jeden produkt', len([p for p in prods if 'Converse' in p['name']]), 1)
check('Converse: kategorie', (conv['cat'], conv['sub']), ('tenisky', 'tenisky'))
check('Converse: sleva z původní ceny obchodu', (conv['now'], conv['was'], conv['disc'], conv['basis']), (1409.0, 2390.0, 41, 'obchod'))
check('Converse: velikosti seřazené', conv['variant'], 'Skladem: 42, 44,5')
check('velikosti: NOSIZE a 0 pryč, třetiny, EUR', [bc.norm_size(s) for s in ['NOSIZE', '0', '43_13', '40_23', '44_5', 'EUR 40', 'EUR EU S', 'xs']],
      [None, None, '43 1/3', '40 2/3', '44,5', '40', 'S', 'XS'])
check('velikosti: řazení', sorted(['44,5', 'M', '43 1/3', '40 1/2', 'XS', '40', '39,5'], key=bc.size_key), ['39,5', '40', '40 1/2', '43 1/3', '44,5', 'XS', 'M'])
check('Converse: partnerský odkaz eHUB', conv['url'].startswith('https://ehub.cz/system/scripts/click.php?a_aid=') and 'a_bid=568a437d' in conv['url'] and 'desturl=https%3A%2F%2Feobuv.cz' in conv['url'], True)
check('dětské boty vyřazené', any('Skechers' in p['name'] for p in prods), False)
check('bačkory vyřazené', any('Bačkory' in p['name'] for p in prods), False)
rl = next((p for p in prods if 'Ralph' in p['name']), None)
check('polobotky bez slevy', (rl['cat'], rl['disc']), ('tenisky', 0))
wh = next((p for p in prods if 'Whey' in p['name']), None)
check('Myprotein: sportovní výživa', (wh['cat'], wh['sub']), ('sport', 'vyziva'))
check('Myprotein: doporučená cena se nebere jako sleva', (wh['was'], wh['disc']), (709.0, 0))
tr = next((p for p in prods if 'Tričko' in p['name']), None)
check('Myprotein: tričko není skladem', (tr['cat'], tr['stock']), ('obleceni', False))
print('\n'.join(rep))
sys.exit(0 if ok else 1)
