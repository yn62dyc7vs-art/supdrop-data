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
# Kategorie podle stromu Heureky / Google (úrovně od nejhlubší), pak podle názvu.
# Vyřazené: dětské věci, ponožky a prádlo, pantofle a sandály, příslušenství, náhradní díly…
SKIP = ['detsk', 'kojen', 'junior', 'ponozk', 'punchoch', 'puncoch', 'spodni pradlo', 'pantofl', 'zabk', 'sandal', 'holink',
        'snehul', 'backor', 'domaci obuv', 'lodick', 'kozack', 'balerin', 'capack', 'vlozk', 'kompres', 'zdravotn', 'bandaz',
        'ortez', 'penal', 'skolni', 'kufr', 'destnik', 'plastenk', 'prislusenstvi', 'komponent', 'nahradni', 'tiskarn',
        'sitove prvky', 'kancelar', 'baterie', 'powerbank', 'kuchyn', 'uklidov', 'stavba', 'elektromaterial', 'pece o telo',
        'pouzdr', 'kryt', 'folie', 'tvrzene sklo', 'ochranne sklo', 'kabel ', 'kabely', 'nabijec', 'adapter', 'drzak',
        'reminek', 'redukce', 'stylus', 'cistic', 'darkova krabice', 'poukaz', 'servis', 'pujcovna', 'tkanick',
        'impregnac', 'naplne', 'naradi', 'pumpick', 'duse', 'plaste', 'brzd', 'retez', 'pedal', 'sedlo', 'riditk',
        'prehazovac', 'lahev', 'lahve', 'kosik na lahev', 'cocky', 'stoupaci pas', 'vnitrni boticky', 'kalhotky', 'podprsenk',
        'polobotk', 'kotnikove boty', 'sitova karta', 'access point', 'switch ', 'router', 'toner', 'inkoust',
        'sklo', 'ochrana objektivu', 'klicenk', 'poutko', 'jmenovk', 'mys ', 'mouse', 'klavesnic', 'keyboard', 'flash disk',
        'usb ', 'presenter', 'replacement', 'hrot', 'chladic', 'zdroj', 'pametova karta', 'disk ',
        'glass', 'sklem', 'obal ', 'brasn', 'sleeve', 'cpu ', 'zakladni desk', 'graficka karta', 'operacni pamet']
RULES = [  # (kategorie, klíčová slova v úrovni stromu)
    ('mobily',   ['mobilni telefony', 'chytre telefony']),
    ('gadgety',  ['chytre hodinky', 'chytre naramky', 'wearables', 'drony', 'akcni kamery', 'fotoaparaty', 'stabilizator', 'gimbal', 'chytra domacnost']),
    ('audio',    ['sluchatka', 'reproduktory', 'soundbar', 'gramofon']),
    ('gaming',   ['herni konzole', 'herni ovladace', 'hry na playstation', 'hry na xbox', 'hry na nintendo', 'hry na pc', 'herni sluchatka']),
    ('pocitace', ['notebooky', 'tablety', 'ctecky', 'stolni pocitace', 'monitory']),
    ('pokoj',    ['svitidla', 'osvetleni', 'lampy', 'projektory', 'projekcni', 'dekorace']),
    ('tenisky',  ['tenisky', 'sneakers', 'bezecke boty', 'sportovni obuv', 'volnocasova obuv']),
    ('sport',    ['skialp', 'turistick', 'obleceni na behani', 'cyklistick', 'jizdni kola', 'lyze', 'lyzarsk', 'trekov', 'outdoor', 'kolobezk', 'skateboard', 'stany', 'spacak']),
    ('fitness',  ['fitness', 'posilov', 'sportovni vyziva', 'proteiny']),
    ('doplnky',  ['batoh', 'kabelk', 'penezenk', 'tasky', 'cepice', 'ksiltovk', 'rukavice', 'saly', 'satky', 'opasky', 'slunecni bryle', 'hodinky', 'na hlavu a krk']),
    ('obleceni', ['mikin', 'svetr', 'bundy', 'bunda', 'kabat', 'vesty', 'kalhoty', 'teplaky', 'leginy', 'tricka', 'kosile', 'saty', 'sukne', 'sortky', 'kratasy', 'volnocasove obleceni', 'svrchni obleceni', 'obleceni']),
    ('kosmetika',['parfem', 'kosmetik', 'pece o plet']),
]
NAME_RULES = [  # když obchod kategorii nemá (iStyle), podle názvu
    ('mobily',   ['iphone', 'galaxy s', 'galaxy a', 'galaxy z', 'redmi note', 'redmi ', 'poco ', 'pixel ']),
    ('pocitace', ['macbook', 'ipad', 'imac', 'mac mini', 'notebook', 'tablet', 'kindle', 'monitor']),
    ('audio',    ['airpods', 'sluchatka', 'reproduktor', 'beats', 'sonos', 'buds', 'soundbar', 'homepod']),
    ('gadgety',  ['apple watch', 'chytre hodinky', 'smartwatch', 'osmo', 'gopro', 'insta360', 'dron', 'instax', 'airtag']),
    ('gaming',   ['playstation', 'xbox', 'nintendo switch', 'dualsense']),
    ('pokoj',    ['lampa', 'lamp ', 'svetelny pasek', 'led pasek']),
    ('tenisky',  ['tenisky', 'sneakers']),
]
LEAF_ONLY = {'mobily', 'gadgety', 'audio', 'gaming', 'pocitace', 'pokoj'}
KIDS = re.compile(r'-J[BG]?\b|\bjunior\b|\bkids?\b|\bdetsk|\d+\s*-\s*\d+\s*let\b', re.I)

def categorize(path, name):
    segs = [norm(x) for x in re.split(r'\s*[|>]\s*', path or '') if x.strip()]
    segs = [x for x in segs if x not in ('heureka cz', 'heureka sk', 'zbozi cz')]
    n = ' ' + norm(name) + ' '
    full = ' ' + ' '.join(segs) + ' '
    leaf = ' ' + (segs[-1] if segs else '') + ' '
    if any(' ' + k in full for k in ('detsk', 'kojen', 'junior')) or any(' ' + k in leaf or ' ' + k in n for k in SKIP) \
            or KIDS.search(name or '') or ' tah ' in n or (' pasek ' in n and 'hodink' not in full):
        return None
    if ' monitor' in n: return 'pocitace'
    for i, seg in enumerate(reversed(segs)):
        seg = ' ' + seg + ' '
        for cat, keys in RULES:
            if i > 0 and cat in LEAF_ONLY: continue   # elektronika jen podle nejhlubší úrovně
            if any(k in seg for k in keys): return cat
    if segs: return None          # obchod kategorii má, ale není pro šup
    for cat, keys in NAME_RULES:
        if any(' ' + k in n for k in keys): return cat
    return None

KIND = {'tenisky': 'shoe', 'obleceni': 'hoodie', 'doplnky': 'cap', 'mobily': 'phone', 'audio': 'headphones', 'gaming': 'controller',
        'pocitace': 'laptop', 'gadgety': 'camera', 'pokoj': 'lamp', 'sport': 'scooter', 'fitness': 'dumbbell', 'kosmetika': 'bottle'}

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
    for _ in range(3):                                   # koncové kódy (EAN, objednací čísla)
        m = re.search(r'\s(\(?[A-Za-z0-9/\-]+\)?)$', n)
        if not m: break
        t = m.group(1).strip('()')
        code = len(t) >= 5 and sum(c.isdigit() for c in t) >= 2 and not any(c.islower() for c in t) \
            and not re.fullmatch(r'[\d/]+(GB|TB|MB|W|MAH|MM)(/[\d]+(GB|TB))?', t.upper())
        if not code: break
        n = n[:m.start()]
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
    """Pestrý výběr pro web: nejlepší skóre, max. 3 z kategorie, 4 z obchodu, bez barevných variant téhož."""
    out, seen, per_cat, per_shop = [], set(), {}, {}
    def base(d): return norm(d['brand'] + ' ' + d['name']).split(' ')[:4]
    def take(cond, cat_lim, shop_lim):
        for d in products:
            if len(out) >= n: return
            if d['id'] in out or not (d['pass'] and d['img']) or not cond(d): continue
            b = ' '.join(base(d))
            if b in seen or per_cat.get(d['cat'], 0) >= cat_lim or per_shop.get(d['shop'], 0) >= shop_lim: continue
            out.append(d['id']); seen.add(b)
            per_cat[d['cat']] = per_cat.get(d['cat'], 0) + 1; per_shop[d['shop']] = per_shop.get(d['shop'], 0) + 1
    take(lambda d: d['disc'] > 0, 3, 4)
    take(lambda d: d['hype'] >= 7, 3, 4)
    take(lambda d: True, 3, 4)
    take(lambda d: True, 99, 99)
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
