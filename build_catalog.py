#!/usr/bin/env python3
"""Denní katalog šupu z produktových feedů partnerských obchodů (eHUB).

Každé ráno:
  1. stáhne XML feedy obchodů (formát Heureka nebo Google),
  2. spojí varianty (velikosti, barvy) do jednoho produktu,
  3. zařadí produkt do kategorie šupu (tenisky, oblečení, mobily, …),
  4. spočítá slevu: u Google feedu z původní ceny obchodu, u Heureka feedu
     z nejvyšší ceny za posledních 30 dní (historii si pamatuje ve state.json),
  5. vytvoří partnerský odkaz přes eHUB,
  6. zapíše out/latest.json (čte ho web a appka) a out/state.json (historie cen).

Adresy feedů jsou v tajné proměnné SHOP_FEEDS (JSON {"CoolBoty": "https://…", …}),
ostatní nastavení obchodů v config/shops.json.
"""
import gzip, io, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Prague')
EHUB_AID = '2659764d'
G = '{http://base.google.com/ns/1.0}'
UA = 'supdrop-catalog/1.0 (+https://supdrop.cz; info@supdrop.cz)'
EPOCH = date(2026, 1, 1)
MIN_PRICE = 150          # levnější drobnosti (fólie, tkaničky…) do dropu nepatří
HISTORY_DAYS = 30

# ---------------------------------------------------------------- pomocné
def norm(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def num(v):
    if v is None: return None
    s = re.sub(r'[^\d,.\-]', '', str(v))
    if not s: return None
    if ',' in s and '.' in s: s = s.replace('.', '').replace(',', '.') if s.rfind(',') > s.rfind('.') else s.replace(',', '')
    else: s = s.replace(',', '.')
    try: return round(float(s), 2)
    except ValueError: return None

def day_no(d): return (d - EPOCH).days

def slug(s): return norm(s).replace(' ', '-')[:40]

def aff(url, bid):
    if not bid: return url
    return ('https://ehub.cz/system/scripts/click.php?a_aid=' + EHUB_AID + '&a_bid=' + bid +
            '&desturl=' + urllib.parse.quote(url, safe=''))

# ---------------------------------------------------------------- kategorie
# pořadí je důležité: první shoda vyhrává
SKIP = ['nahradni dil', 'nahradni dily', 'komponent', 'pouzdr', 'kryt', 'folie', 'tvrzene sklo', 'ochranne sklo',
        'ochranna folie', 'kabel ', 'kabely', 'nabijec', 'adapter', 'drzak', 'reminek', 'naramek pro', 'redukce', 'stylus',
        'cistic', 'darkova krabice', 'darkovy poukaz', 'poukaz', 'servis', 'pujcovna', 'detsk', 'kojen',
        'tkanick', 'vlozk', 'impregnac', 'kartus', 'naplne', 'naradi', 'pumpick', 'duse', 'plast na kolo', 'plaste',
        'brzd', 'retez', 'pedal', 'sedlo', 'riditk', 'naboj', 'kazeta', 'prehazovac', 'lahev', 'lahve', 'kontaktni cocky', 'cocky']
RULES = [
    ('mobily',   ['mobilni telefony', 'smartphone', 'chytre telefony'], ['mobilni telefon', 'iphone', 'galaxy s', 'galaxy a', 'redmi', 'poco', 'pixel']),
    ('audio',    ['sluchatk', 'reproduktor', 'soundbar', 'audio'], ['sluchatk', 'airpods', 'reproduktor', 'buds']),
    ('gaming',   ['herni konzol', 'gaming', 'herni ovladac', 'videohry', 'playstation', 'xbox', 'nintendo'], ['playstation', 'xbox', 'nintendo switch', 'herni konzole', 'ovladac dualsense']),
    ('pocitace', ['notebook', 'tablet', 'ctecky elektronickych knih', 'pocitace'], ['macbook', 'ipad', 'notebook', 'tablet', 'kindle']),
    ('gadgety',  ['chytre hodinky', 'fitness naramk', 'dron', 'kamery', 'akcni kamer', 'gimbal', 'stabilizator', 'chytra domacnost', 'diktafon'], ['apple watch', 'chytre hodinky', 'smartwatch', 'gimbal', 'osmo', 'dron', 'gopro', 'instax']),
    ('pokoj',    ['lamp', 'svitid', 'osvetleni', 'dekorace', 'led pasky'], ['lampa', 'lamp', 'led pasek', 'svetlo']),
    ('tenisky',  ['tenisk', 'sneaker', 'bezecka obuv', 'bezecke boty', 'sportovni obuv', 'volnocasova obuv', 'vychazkova obuv', 'skate obuv', 'obuv'], ['tenisky', 'sneaker']),
    ('fitness',  ['fitness', 'posilovani', 'vyziva', 'protein', 'fitness obleceni'], ['protein', 'kreatin', 'cinka']),
    ('doplnky',  ['batoh', 'tasky', 'kabelk', 'penezenk', 'cepic', 'ksiltovk', 'satky', 'saly', 'rukavic', 'slunecni bryle', 'opasky', 'hodinky', 'ponozky', 'doplnky'], ['batoh', 'cepice', 'ksiltovka', 'kabelka', 'penezenka', 'bryle', 'ponozky']),
    ('obleceni', ['obleceni', 'mikiny', 'tricka', 'bundy', 'kalhoty', 'sortky', 'vesty', 'svetry', 'kosile', 'saty', 'leginy', 'teplaky', 'kraťasy'], ['mikina', 'tricko', 'bunda', 'kalhoty', 'sortky', 'vesta', 'svetr', 'kosile', 'leginy', 'mikina', 'hoodie']),
    ('sport',    ['cyklistika', 'jizdni kola', 'kola ', 'lyze', 'lyzovani', 'skialp', 'turistika', 'outdoor', 'kempovani', 'stany', 'spacaky', 'kolobezk', 'skateboard', 'beh'], ['kolo ', 'lyze', 'stan ', 'spacak', 'kolobezka', 'skateboard']),
    ('kosmetika',['kosmetika', 'parfem', 'drogerie', 'pece o plet', 'vlasova kosmetika'], ['parfem', 'toaletni voda', 'serum']),
]
KIND = {'tenisky': 'shoe', 'obleceni': 'hoodie', 'doplnky': 'cap', 'mobily': 'phone', 'audio': 'headphones', 'gaming': 'controller',
        'pocitace': 'laptop', 'gadgety': 'camera', 'pokoj': 'lamp', 'sport': 'scooter', 'fitness': 'dumbbell', 'kosmetika': 'bottle'}

def categorize(path, name):
    """Kategorie podle nejhlubší úrovně cesty (Heureka: "A | B | C", Google: "A > B > C"), pak podle názvu."""
    segs = [norm(x) for x in re.split(r'\s*[|>/]\s*', path or '') if x.strip()]
    segs = [x for x in segs if x not in ('heureka cz', 'heureka sk', 'zbozi cz')]
    n = ' ' + norm(name) + ' '
    full = ' ' + ' '.join(segs) + ' '
    if any(' ' + k in full or ' ' + k in n for k in SKIP): return None
    for seg in reversed(segs):
        seg = ' ' + seg + ' '
        for cat, pk, nk in RULES:
            if any(k in seg for k in pk): return cat
    for cat, pk, nk in RULES:
        if any(' ' + k in n for k in nk): return cat
    return None

HYPE = {'nike': 8, 'jordan': 9, 'adidas': 8, 'new balance': 8, 'asics': 7, 'salomon': 7, 'on': 7, 'hoka': 7, 'vans': 6,
        'converse': 6, 'puma': 6, 'reebok': 5, 'the north face': 7, 'carhartt': 7, 'carhartt wip': 7, 'stussy': 8, 'columbia': 5,
        'apple': 9, 'samsung': 7, 'xiaomi': 6, 'google': 7, 'jbl': 7, 'sony': 7, 'marshall': 7, 'bose': 7, 'beats': 7,
        'dji': 7, 'garmin': 7, 'govee': 6, 'nothing': 7, 'dynafit': 6, 'specialized': 6, 'kilpi': 5, 'lego': 8, 'playstation': 8}

def hype_of(brand): return HYPE.get(norm(brand), 5)

def score(disc, hype, trust, at_min):
    s = {'sleva': round(min(40, max(0, disc) * 40 / 25)), 'poptavka': hype * 3, 'minimum': 10 if at_min else 0, 'obchod': trust, 'zaklad': 5}
    s['total'] = sum(s.values()); return s

# ---------------------------------------------------------------- čtení feedů
SIZE_KEYS = {'velikost', 'size', 'velikost obuvi', 'velikost eu', 'eu velikost'}

def clean_name(name, size=None):
    n = re.sub(r'\s+', ' ', name or '').strip()
    n = re.split(r'\s+(?:Barva|Velikost|Šířka|Délka|Rozměr|Varianta|Color|Size)\s*:', n)[0]
    n = re.sub(r'\s\d{8,}$', '', n)                      # koncové EAN/kódy
    if size:
        n = re.sub(r'\s' + re.escape(size) + r'(?=\s|$)', '', n).strip()
    return n.strip(' ,-–')

def open_feed(url, timeout=180):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
    r = urllib.request.urlopen(req, timeout=timeout)
    if r.headers.get('Content-Encoding') == 'gzip' or url.endswith('.gz'):
        return gzip.GzipFile(fileobj=r)
    return r

def txt(el, tag):
    x = el.find(tag)
    return (x.text or '').strip() if x is not None and x.text else ''

def heureka_items(src):
    for ev, el in ET.iterparse(src, events=('end',)):
        if el.tag != 'SHOPITEM': continue
        params = {}
        for p in el.findall('PARAM'):
            k, v = norm(txt(p, 'PARAM_NAME')), txt(p, 'VAL')
            if k and v: params[k] = v
        size = next((params[k] for k in SIZE_KEYS if k in params), None)
        dd = txt(el, 'DELIVERY_DATE')
        stock = True
        if dd and not re.fullmatch(r'\d+', dd): stock = False if norm(dd) in ('vyprodano', 'nedostupne', 'out of stock') else True
        elif dd and int(dd) > 14: stock = False
        yield {'id': txt(el, 'ITEM_ID'), 'group': txt(el, 'ITEMGROUP_ID') or txt(el, 'ITEM_ID'),
               'name': txt(el, 'PRODUCT') or txt(el, 'PRODUCTNAME'), 'brand': txt(el, 'MANUFACTURER'),
               'path': txt(el, 'CATEGORYTEXT'), 'url': txt(el, 'URL'), 'img': txt(el, 'IMGURL'),
               'price': num(txt(el, 'PRICE_VAT')), 'orig': None, 'stock': stock, 'size': size, 'ean': txt(el, 'EAN')}
        el.clear()

def google_items(src):
    for ev, el in ET.iterparse(src, events=('end',)):
        if el.tag not in ('item', 'entry'): continue
        g = lambda t: txt(el, G + t)
        price, sale = num(g('price')), num(g('sale_price'))
        now = sale if sale and price and sale < price else price
        orig = price if sale and price and sale < price else None
        link = txt(el, 'link') or g('link')
        yield {'id': g('id'), 'group': g('item_group_id') or g('id'), 'name': txt(el, 'title') or g('title'),
               'brand': g('brand'), 'path': g('product_type') or g('google_product_category'), 'url': link,
               'img': g('image_link'), 'price': now, 'orig': orig,
               'stock': g('availability').lower().replace('_', ' ') in ('in stock', 'preorder', 'backorder', ''),
               'size': g('size') or None, 'ean': g('gtin')}
        el.clear()

# ---------------------------------------------------------------- hlavní běh
def load(p, default):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def build(shops, feeds, state, now, open_fn=open_feed, log=print):
    today = day_no(now.date())
    stamp = now.strftime('%Y-%m-%dT%H:%M')
    hist = state.get('h', {})
    products, report = [], []
    for sh in shops:
        name = sh['shop']; url = feeds.get(name)
        if not url: report.append(f'{name}: chybí adresa feedu'); continue
        t0 = time.time(); groups = {}; n = 0
        try:
            src = open_fn(url)
            it = google_items(src) if sh.get('format') == 'google' else heureka_items(src)
            for x in it:
                n += 1
                if not x['price'] or not x['url']: continue
                gk = x['group'] or x['id']
                g = groups.get(gk)
                if g is None: g = groups[gk] = {'v': [], 'first': x}
                g['v'].append(x)
        except Exception as e:  # jeden rozbitý feed nesmí shodit celý běh
            report.append(f'{name}: chyba feedu ({type(e).__name__}: {e})'); log(report[-1])
            if not groups: continue
        kept = 0
        for gk, g in groups.items():
            f = g['first']; vs = g['v']
            avail = [v for v in vs if v['stock']] or []
            base = avail or vs
            cheapest = min(base, key=lambda v: v['price'])
            sizes = []
            for v in avail:
                if v['size'] and v['size'] not in sizes: sizes.append(v['size'])
            nm = clean_name(cheapest['name'], cheapest['size'] if len(vs) > 1 else None)
            if len(vs) > 1:  # společný začátek názvů variant
                words = [clean_name(v['name']).split(' ') for v in vs[:20]]
                common = []
                for col in zip(*words):
                    if all(w == col[0] for w in col): common.append(col[0])
                    else: break
                if len(common) >= 2: nm = ' '.join(common).strip(' ,-–')
            brand = (f['brand'] or '').strip()
            cat = categorize(f['path'], nm)
            if not cat or cat in sh.get('skipCats', []): continue
            now_p = cheapest['price']
            if now_p < MIN_PRICE: continue
            key = slug(name) + ':' + str(gk)
            h = [x for x in hist.get(key, []) if x[0] >= today - HISTORY_DAYS]
            if not h or h[-1][1] != now_p or h[-1][0] != today:
                h = [x for x in h if x[0] != today] + [[today, now_p]]
            hist[key] = h
            if cheapest.get('orig'):
                was = cheapest['orig']; basis = 'obchod'
            else:
                was = max(x[1] for x in h); basis = 'historie'
            was = max(was, now_p)
            disc = round((1 - now_p / was) * 100) if was else 0
            days = today - min(x[0] for x in h)
            at_min = days >= 7 and now_p <= min(x[1] for x in h)
            hy = hype_of(brand)
            sc = score(disc, hy, sh.get('trust', 7), at_min)
            stock = bool(avail)
            products.append({
                'id': key.replace(':', '-'), 'cat': cat, 'kind': KIND.get(cat, 'spark'), 'brand': brand or name, 'name': nm,
                'variant': ('Skladem: ' + ', '.join(sizes[:12])) if sizes else None,
                'shop': name, 'url': aff(cheapest['url'], sh.get('bid')), 'aff': bool(sh.get('bid')),
                'now': now_p, 'was': was, 'disc': disc, 'basis': basis, 'hype': hy, 'stock': stock, 'pass': stock,
                'reason': None if stock else 'Není skladem.', 'score': sc, 'checked': stamp, 'img': f['img'] or cheapest['img'],
                'imgby': name, 'days': days})
            kept += 1
        report.append(f'{name}: {n} položek ve feedu, {len(groups)} produktů, do šupu {kept} ({time.time() - t0:.0f} s)')
        log(report[-1])
    # historie: zahodit produkty, které 45 dní nikdo neviděl
    hist = {k: v for k, v in hist.items() if v and v[-1][0] >= today - 45}
    products.sort(key=lambda d: -d['score']['total'])
    return products, {'h': hist, 'updated': stamp}, report

def pick_top(products, n=12):
    """Pestrý výběr pro web: nejlepší skóre, ale max. 3 z jedné kategorie a 4 z jednoho obchodu."""
    out, per_cat, per_shop = [], {}, {}
    for d in products:
        if not (d['pass'] and d['img'] and d['disc'] > 0): continue
        if per_cat.get(d['cat'], 0) >= 3 or per_shop.get(d['shop'], 0) >= 4: continue
        out.append(d['id']); per_cat[d['cat']] = per_cat.get(d['cat'], 0) + 1; per_shop[d['shop']] = per_shop.get(d['shop'], 0) + 1
        if len(out) >= n: break
    if len(out) < n:
        for d in products:
            if d['pass'] and d['img'] and d['id'] not in out: out.append(d['id'])
            if len(out) >= n: break
    return out

def main():
    now = datetime.now(TZ)
    shops = load('config/shops.json', [])
    feeds = json.loads(os.environ.get('SHOP_FEEDS') or '{}') or load('config/feeds.local.json', {})
    os.makedirs('out', exist_ok=True)
    state = load('out/state.json', {})
    products, state, report = build(shops, feeds, state, now)
    if not products:
        print('Žádný produkt, nic nezapisuji.'); print('\n'.join(report)); sys.exit(1)
    out = {'batch': now.strftime('%Y-%m-%d'), 'checkedAt': now.strftime('%Y-%m-%dT%H:%M'),
           'source': 'Produktové feedy partnerských obchodů (eHUB)', 'affiliate': True,
           'shops': [s['shop'] for s in shops], 'note': ' · '.join(report),
           'top': pick_top(products), 'deals': products}
    with open('out/latest.json', 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    with open('out/state.json', 'w', encoding='utf-8') as f: json.dump(state, f, ensure_ascii=False, separators=(',', ':'))
    by = {}
    for d in products: by[d['cat']] = by.get(d['cat'], 0) + 1
    print('Celkem', len(products), 'produktů:', by)
    print('Velikost latest.json:', os.path.getsize('out/latest.json') // 1024, 'kB')

if __name__ == '__main__':
    main()
