#!/usr/bin/env python3
"""Denní katalog šupu z produktových feedů partnerských obchodů (eHUB).

Každé ráno:
  1. stáhne XML feedy obchodů (formát Heureka nebo Google),
  2. spojí varianty (velikosti, barvy) do jednoho produktu,
  3. zařadí produkt do kategorie a podkategorie šupu (podle stromu kategorií obchodu),
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
# Každý produkt dostane kategorii a podkategorii šupu podle stromu kategorií obchodu
# (Heureka / Google, od nejhlubší úrovně), a když obchod strom nemá, podle názvu.
# Zásada: oblečení je vždy v Oblečení, boty vždy v Botách (i sportovní), Sport je vybavení.
SKIP = ['kojen', 'backor', 'domaci obuv', 'vlozk', 'kompres', 'zdravotn', 'bandaz', 'ortez', 'penal', 'skolni', 'destnik',
        'plastenk', 'komponent', 'nahradni', 'tiskarn', 'sitove prvky', 'kancelar', 'baterie', 'kuchyn', 'uklidov', 'stavba',
        'elektromaterial', 'pece o telo', 'cistic', 'darkova krabice', 'poukaz', 'servis', 'pujcovna', 'tkanick', 'impregnac',
        'naplne', 'naradi', 'pumpick', 'duse', 'plaste', 'brzd', 'retez', 'pedal', 'sedlo', 'riditk', 'prehazovac',
        'kosik na lahev', 'cocky', 'stoupaci pas', 'vnitrni boticky', 'sitova karta', 'access point', 'switch ', 'router',
        'klicenk', 'poutko', 'jmenovk', 'flash disk', 'presenter', 'replacement', 'hrot',
        'pametova karta', 'ip kamery', 'webkamery', 'kamery do auta', 'sluzba', 'pojisteni', 'prodlouzena zaruka', 'brasn',
        'plavecke pomucky', 'nafukovac', 'vodni radovanky', 'svetla na kolo', 'zamky na kolo', 'nosice kol']
# slova, která rozhodují jen v kategorii obchodu (v názvu by vyřadila i „inkoustové“ hodinky nebo MacBook s „CPU“)
SKIP_PATH = ['toner', 'inkoust', 'naplne a tonery', 'cpu', 'procesory', 'zdroj', 'zakladni desk', 'graficka karta', 'operacni pamet',
             'pameti', 'chladic', 'disk', 'usb ', 'kabely a konektory', 'servis', 'sleeve', 'switche', 'tiskarny']
# příslušenství k elektronice: jen do podkategorie Příslušenství, do „celé kategorie“ ne
ACC = ['prislusenstvi', 'ochranny film', 'ochranna folie', 'ochranne', 'pouzdr', 'kryt', 'folie', 'tvrzene sklo', 'sklo', 'skla', 'glass', 'remink', 'sklem', 'obal ', 'kabel ', 'kabely',
       'nabijec', 'adapter', 'drzak', 'reminek', 'powerbank', 'magsafe', 'redukce', 'stylus', 'ochrana cocek',
       'ochrana objektivu', 'ochrana fotoaparatu', 'stojanek', 'grip', 'case', 'cover', 'pasek pro', 'naramek pro']
ELEC_CTX = ['mobil', 'telefon', 'tablet', 'apple', 'iphone', 'ipad', 'elektronik', 'gsm', 'notebook', 'chytre hodink',
            'pocitac', 'galaxy', 'macbook', 'airpods', 'pixel', 'xiaomi', 'samsung', 'usb c', 'lightning', 'watch']
PERIPH = ['mys ', 'mysi', 'mouse', 'klavesnic', 'keyboard', 'podlozka pod mys']
GAMER = ['herni', 'gaming', 'razer', 'steelseries', 'hyperx', 'logitech g', 'corsair', 'roccat']
# (kategorie, podkategorie, klíčová slova v úrovni stromu, jen nejhlubší úroveň)
EXC = [  # sportovní vybavení, které by jinak spadlo do bot / doplňků
    ('sport', 'lyze', ['lyzarske boty', 'lyzaky', 'snowboardove boty', 'skialpove boty', 'bezkarske boty', 'lyzarske bryle',
                       'lyzarske helmy', 'lyzarske hulky', 'hulky'], False),
    ('sport', None, ['cyklisticke lahve', 'bezecke lahve', 'cyklisticke tretry', 'tretry', 'cyklisticke helmy', 'cyklisticke bryle', 'prilby na kolo', 'helmy na kolo'], False),
    ('sport', 'skate', ['chranice', 'helmy na skate'], False),
]
ELEC = [
    ('gaming', 'ovladace', ['dokovaci stanice', 'herni prislusenstvi', 'prislusenstvi k hernim'], True),
    ('gaming', 'konzole', ['herni konzole', 'konzole', 'virtualni realita', 'vr bryle'], True),
    ('gaming', 'hry', ['hry na playstation', 'hry na xbox', 'hry na nintendo', 'hry na pc', 'pc hry', 'videohry', 'hry pro'], True),
    ('gaming', 'ovladace', ['herni ovladace', 'gamepad', 'ovladace', 'herni sluchatka', 'headsety', 'herni headset'], True),
    ('gaming', 'periferie', ['herni mysi', 'herni klavesnice', 'herni podlozky', 'herni zidle'], True),
    ('elektronika', 'mobily', ['mobilni telefony', 'chytre telefony', 'mobily', 'smartphony'], True),
    ('elektronika', 'tablety', ['tablety', 'ctecky'], True),
    ('elektronika', 'notebooky', ['notebooky', 'stolni pocitace', 'monitory', 'pocitace'], True),
    ('elektronika', 'hodinky', ['chytre hodinky', 'chytre naramky', 'wearables', 'fitness naramky', 'sporttestery', 'sportovni hodinky'], True),
    ('elektronika', 'sluchatka', ['sluchatka', 'true wireless', 'headphones'], True),
    ('elektronika', 'repro', ['reproduktory', 'soundbar', 'zesilovac', 'audio systemy', 'hifi'], True),
    ('elektronika', 'foto', ['drony', 'akcni kamery', 'sportovni kamery', 'fotoaparaty', 'stabilizator', 'gimbal', 'objektivy', 'instantni'], True),
    ('elektronika', 'smarthome', ['chytra domacnost', 'smart home', 'chytre osvetleni', 'lokalizator'], True),
    ('pokoj', 'svetla', ['svitidla', 'osvetleni', 'lampy', 'lampicky', 'led pasky', 'svetla'], True),
    ('pokoj', 'dekorace', ['dekorace', 'plakaty', 'obrazy', 'svicky', 'difuzer', 'bytove doplnky'], True),
    ('pokoj', 'spotrebice', ['projektory', 'projekcni', 'kavovary', 'mixery', 'zvlhcovac', 'ventilator', 'vysavac', 'kuchynske spotrebice'], True),
    ('pokoj', 'lahve', ['termohrnky', 'termosky', 'lahve na piti', 'lahve', 'lahev', 'hrnky'], True),
]
SHOES = [
    ('tenisky', 'bezecke', ['bezecke boty', 'bezecka obuv', 'trailova obuv', 'trailove boty', 'silnicni bezecke'], False),
    ('tenisky', 'outdoor', ['outdoorove boty', 'outdoorova obuv', 'trekova obuv', 'trekove boty', 'turisticke boty', 'turisticka obuv',
                            'zimni obuv', 'zimni boty', 'snehul', 'holink', 'kotnikove boty', 'kotnikova obuv', 'pohorky', 'vysoke boty'], False),
    ('tenisky', 'pantofle', ['pantofl', 'sandal', 'zabky', 'slapky', 'nazouvaky'], False),
    ('tenisky', 'tenisky', ['tenisky', 'sneakers', 'volnocasova obuv', 'skate boty', 'nizka obuv', 'slip on'], False),
    ('tenisky', None, ['obuv', 'boty', 'lodick', 'kozack', 'balerin', 'polobotk', 'mokasin'], False),
]
CLOTH = [
    ('obleceni', 'pradlo', ['spodni pradlo', 'ponozk', 'puncoch', 'punchoch', 'boxerk', 'trenyrk', 'slipy', 'kalhotky', 'podprsenk',
                            'termopradlo', 'funkcni pradlo', 'pyzam'], False),
    ('obleceni', 'plavky', ['plavky', 'plazove', 'bikin'], False),
    ('obleceni', 'mikiny', ['mikin', 'svetr', 'fleece', 'rolak', 'cardigan'], False),
    ('obleceni', 'bundy', ['bundy', 'bunda', 'kabat', 'vesty', 'vesta', 'parka', 'softshell', 'svrchni obleceni'], False),
    ('obleceni', 'kratasy', ['kratasy', 'sortky', 'kratke kalhoty'], False),
    ('obleceni', 'kalhoty', ['kalhoty', 'teplaky', 'leginy', 'dziny', 'jeans', 'joggers'], False),
    ('obleceni', 'saty', ['saty', 'sukne', 'overal'], False),
    ('obleceni', 'tricka', ['tricka', 'tricko', 'triko', 'topy', 'tilka', 'tilko', 'kosile', 'polokosile', 'dresy', 'dres ', 'body ', 'trika'], False),
    ('obleceni', None, ['volnocasove obleceni', 'obleceni', 'komplety', 'soupravy', 'odevy'], False),
]
ACCES = [
    ('doplnky', 'batohy', ['batoh', 'kabelk', 'tasky', 'taska', 'ledvink', 'gymsack', 'zavazadla', 'kufr', 'cestovni'], False),
    ('doplnky', 'cepice', ['cepice', 'ksiltovk', 'klobouk', 'kukl', 'celenk', 'na hlavu'], False),
    ('doplnky', 'hodinky', ['hodinky'], False),
    ('doplnky', 'sperky', ['sperky', 'nahrdelnik', 'naramk', 'nausnic', 'prsten', 'retizk'], False),
    ('doplnky', 'bryle', ['slunecni bryle', 'bryle'], False),
    ('doplnky', 'penezenky', ['penezenk'], False),
    ('doplnky', 'saly', ['saly', 'satky', 'satek', 'sal ', 'rukavice', 'nakrcnik', 'opasky', 'opasek'], False),
]
SPORT = [
    ('sport', 'vyziva', ['sportovni vyziva', 'proteiny', 'protein', 'kreatin', 'vyziva', 'gainery', 'aminokyseliny'], False),
    ('sport', 'kola', [' kola ', ' kolo ', 'elektrokol', 'kolobezk', 'bmx'], False),  # jen kola a koloběžky, ne cyklo doplňky
    ('sport', 'skate', ['skateboard', 'longboard', 'pennyboard', 'brusle', 'inline', 'skate', 'waveboard'], False),
    ('sport', 'lyze', ['lyze', 'lyzar', 'snowboard', 'skialp', 'bezk', 'zimni sporty', 'sjezd'], False),
    ('sport', 'outdoor', ['stany', 'stan ', 'spacak', 'karimatk', 'kemp', 'turistik', 'outdoor', 'horolez', 'treking', 'trekov',
                          'celovk', 'vybaveni na hory', 'nadobi do prirody', 'vareni v prirode'], False),
    ('sport', 'fitness', ['fitness', 'posilov', 'cinky', 'joga', 'crossfit', 'hyrox', 'trenink', 'expander'], False),
    ('sport', 'micove', ['basketbal', 'fotbal', 'volejbal', 'tenis', 'padel', 'florbal', 'hokej', 'micove', 'badminton', 'squash', 'golf', 'mice'], False),
    ('sport', None, ['sport', ' beh', 'plavani', 'vodni sporty', 'atletika', 'cyklistik', 'cyklo'], False),
]
COSM = [
    ('kosmetika', 'parfemy', ['parfem', 'toaletni voda', 'parfemovana voda', 'kolinsk'], False),
    ('kosmetika', 'plet', ['pece o plet', 'kosmetik', 'pletov', 'make up', 'licidla'], False),
    ('kosmetika', 'vlasy', ['vlasy', 'vlasov', 'kulma', 'fen '], False),
]
RULES = EXC + ELEC + SHOES + CLOTH + ACCES + SPORT + COSM
PRIORITY = [  # celé větve, kde rozhoduje rodič (merch kapel, vinyly, LEGO)
    ('sberatelske', 'lego', [' lego']),
    ('sberatelske', 'karty', [' pokemon', ' sberatelske karty', ' tcg', ' karetni hry']),
    ('sberatelske', 'figurky', [' funko', ' figurky', ' sberatelsk']),
    ('sberatelske', 'deskovky', [' deskove hry', ' spolecenske hry', ' puzzle']),
    ('hudba', 'vinyly', [' vinyl', ' gramofon', ' lp ', ' cd ']),
    ('hudba', 'merch', [' merchandise kapel', ' kapely']),
    ('hudba', 'fanmerch', [' fan merchandise', ' filmy a serialy', ' gaming merch', ' anime']),
]
NAME_RULES = [  # když obchod kategorii nemá (iStyle, JBL), podle názvu
    ('elektronika', 'mobily', ['iphone', 'galaxy s', 'galaxy a', 'galaxy z', 'redmi note', 'redmi ', 'poco ', 'pixel ']),
    ('elektronika', 'tablety', ['ipad', 'tablet', 'kindle']),
    ('elektronika', 'notebooky', ['macbook', 'imac', 'mac mini', 'mac studio', 'notebook', 'monitor']),
    ('elektronika', 'hodinky', ['apple watch', 'chytre hodinky', 'smartwatch', 'galaxy watch', 'garmin', 'amazfit']),
    ('elektronika', 'repro', ['reproduktor', 'soundbar', 'homepod', 'sonos', 'speaker', 'flip ', 'charge ', 'clip ', ' go ',
                              'partybox', 'xtreme', 'boombox', 'pulse ', 'authentics', 'encore', 'pill', 'bar ']),
    ('elektronika', 'sluchatka', ['airpods', 'sluchatka', 'buds', 'headphones', 'beats', 'tune ', 'live ', 'tour ', 'wave ', 'endurance']),
    ('elektronika', 'foto', ['osmo', 'gopro', 'insta360', 'dron', 'instax', 'fotoaparat', 'dji ']),
    ('elektronika', 'smarthome', ['airtag', 'apple tv']),
    ('gaming', 'konzole', ['playstation 5', 'ps5 ', 'xbox series', 'nintendo switch', 'steam deck']),
    ('gaming', 'ovladace', ['dualsense', 'ovladac', 'controller']),
    ('pokoj', 'svetla', ['lampa', 'lamp ', 'svetelny pasek', 'led pasek']),
    ('tenisky', 'tenisky', ['tenisky', 'sneakers']),
]
LEGACY = {'mobily': ('elektronika', 'mobily'), 'audio': ('elektronika', None), 'gadgety': ('elektronika', None),
          'pocitace': ('elektronika', 'notebooky'), 'fitness': ('sport', 'fitness')}
KIDS_PATH = ('detsk', 'kojen', 'junior', 'hracky pro holky', 'hracky pro kluky', 'mala paradnice', 'baby born', 'panenky',
             'plysov', 'dzieci', 'dzieciec', 'chlopi', 'dziewcz')
MAIN_NAME = ('apple watch', 'iphone 1', 'iphone air', 'ipad', 'macbook', 'airpods', 'galaxy watch', 'pixel watch')
KIDS = re.compile(r'-J[BG]?\b|\bjunior\b|\bkids?\b|\bdetsk|\d+\s*-\s*\d+\s*let\b', re.I)

def _match(text, rules, leaf_ok=True):
    for cat, sub, keys, leaf_only in rules:
        if leaf_only and not leaf_ok: continue
        if any(k in text for k in keys): return cat, sub
    return None

def categorize(path, name):
    """Vrátí (kategorie, podkategorie, příslušenství) nebo None."""
    segs = [norm(x) for x in re.split(r'\s*[|>]\s*', path or '') if x.strip()]
    segs = [x for x in segs if x not in ('heureka cz', 'heureka sk', 'zbozi cz')]
    n = ' ' + norm(name) + ' '
    full = ' ' + ' '.join(segs) + ' '
    leaf = ' ' + (segs[-1] if segs else '') + ' '
    if any(' ' + k in full for k in KIDS_PATH) or KIDS.search(name or ''): return None
    ln = leaf + n
    if any(' ' + k in ln for k in SKIP) or any(' ' + k in full for k in SKIP_PATH) or ' tah ' in n or (' pasek ' in n and 'hodink' not in full): return None
    if any(k in (leaf if segs else n) for k in PERIPH):
        return ('gaming', 'periferie', False) if any(g in full + n for g in GAMER) else None
    # příslušenství: podle kategorie obchodu; bez kategorie podle názvu (ale „Apple Watch … pouzdro“ je hodinky)
    acc_txt = leaf if segs else (n if not any(n.startswith(' ' + m) for m in MAIN_NAME) else '')
    if any(k in acc_txt for k in ACC) and not any(k in ln for k in ('kryt na kolo', 'obal na kolo')):
        if any(c in full + n for c in ELEC_CTX): return ('elektronika', 'prislusenstvi', True)
        if not any(k in ln for k in ('bryle', 'hodinky', 'batoh', 'taska')): return None
    if ' monitor' in n: return ('elektronika', 'notebooky', False)
    for cat, sub, keys in PRIORITY:
        if any(k in full for k in keys): return (cat, sub, False)
    hit = None
    for i, seg in enumerate(reversed(segs)):
        hit = _match(' ' + seg + ' ', RULES, leaf_ok=(i == 0))
        if hit: break
    if hit:
        cat, sub = hit
        if cat == 'sport':  # sportovní oblečení a boty patří do Oblečení / Bot
            alt = _match(n, EXC) or _match(n, SHOES[:-1] + CLOTH[:-1])
            if alt and alt[0] != 'sport': cat, sub = alt
        elif cat == 'tenisky' and sub is None:
            alt = _match(n, SHOES[:-1])
            if alt: sub = alt[1]
        elif cat == 'obleceni' and sub is None:
            alt = _match(n, CLOTH[:-1])
            if alt: sub = alt[1]
        return (cat, sub, False)
    if segs:  # strom obchodu nepomohl (např. 8a.cz má polské kategorie) – zkusíme český název oblečení, bot, doplňků a sportu
        alt = _match(n, EXC + SHOES[:-1] + CLOTH[:-1] + ACCES + SPORT[:-1] + [('pokoj', 'lahve', ['termohrn', 'hrnek', 'lahev', 'termosk'], False)])
        return (alt[0], alt[1], False) if alt else None
    for cat, sub, keys in NAME_RULES:
        if any((k if k.startswith(' ') else ' ' + k) in n for k in keys):
            return (cat, sub, False)
    return None

KIND = {'hudba': 'vinyl', 'sberatelske': 'figure', 'tenisky': 'shoe', 'obleceni': 'hoodie', 'doplnky': 'cap', 'elektronika': 'phone',
        'gaming': 'controller', 'pokoj': 'lamp', 'sport': 'scooter', 'kosmetika': 'bottle', 'knihy': 'book'}
SUBKIND = {'sluchatka': 'headphones', 'repro': 'speaker', 'notebooky': 'laptop', 'tablety': 'tablet', 'hodinky': 'watch',
           'foto': 'camera', 'smarthome': 'camera', 'fitness': 'dumbbell', 'vyziva': 'cup', 'batohy': 'bag', 'bryle': 'glasses',
           'lahve': 'cup', 'tricka': 'tee', 'kalhoty': 'pants', 'bundy': 'jacket', 'skate': 'board', 'karty': 'cards', 'lego': 'brick'}
def kind_of(cat, sub): return SUBKIND.get(sub) or KIND.get(cat, 'spark')

HYPE = {'nike': 8, 'jordan': 9, 'adidas': 8, 'new balance': 8, 'asics': 7, 'salomon': 7, 'on': 7, 'hoka': 7, 'vans': 6,
        'converse': 6, 'puma': 6, 'reebok': 5, 'the north face': 7, 'carhartt': 7, 'carhartt wip': 7, 'stussy': 8, 'columbia': 5,
        'apple': 9, 'samsung': 7, 'xiaomi': 6, 'google': 7, 'jbl': 7, 'sony': 7, 'marshall': 7, 'bose': 7, 'beats': 7,
        'dji': 7, 'garmin': 7, 'govee': 6, 'nothing': 7, 'dynafit': 6, 'specialized': 6, 'kilpi': 5, 'lego': 8, 'playstation': 8}

def hype_of(brand): return HYPE.get(norm(brand), 5)

# značky, které obchody vedou pod mateřskou firmou (Beats je ve feedech jako „Apple“)
SUBBRANDS = {'beats': 'Beats', 'jordan': 'Jordan', 'harman kardon': 'Harman Kardon'}
def fix_brand(brand, name):
    n = norm(name)
    for k, v in SUBBRANDS.items():
        if (n + ' ').startswith(k + ' ') and norm(brand) != k: return v
    return brand

# obrázky z domén s ochranou proti botům (v cizí stránce se nenačtou) bereme ze sesterské domény obchodu
IMG_HOSTS = {'https://8a.pl/': 'https://8a.cz/'}
def fix_img(url):
    for a, b in IMG_HOSTS.items():
        if url and url.startswith(a): return b + url[len(a):]
    return url

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

def ehub_items(src):
    """eHUB formát (datadepo): URL už je partnerský odkaz, cena PRICE_VAT, sklad STORE_STATE."""
    for ev, el in ET.iterparse(src, events=('end',)):
        if el.tag != 'SHOPITEM': continue
        params = {}
        for p in el.findall('PARAM'):
            k, v = norm(txt(p, 'PARAM_NAME')), txt(p, 'VAL')
            if k and v: params[k] = v
        st = norm(txt(el, 'STORE_STATE'))
        yield {'id': txt(el, 'ITEM_ID'), 'group': txt(el, 'ITEMGROUP_ID') or txt(el, 'ITEM_ID'),
               'name': txt(el, 'NAME') or txt(el, 'PRODUCTNAME'), 'brand': txt(el, 'MANUFACTURER') or params.get('skupina', ''),
               'path': txt(el, 'CATEGORY_FULL') or txt(el, 'CATEGORYTEXT'), 'url': txt(el, 'URL'), 'img': txt(el, 'IMAGE_MAIN') or txt(el, 'IMGURL'),
               'price': num(txt(el, 'PRICE_VAT')), 'orig': None, 'stock': st in ('store', 'skladem', 'in stock', ''),
               'size': params.get('velikost') or None, 'ean': txt(el, 'EAN'), 'tracked': True}
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

def legacy_cat(d):
    c, sb = d['cat'], d.get('sub')
    if d.get('acc'): return None
    if c == 'elektronika':
        return {'mobily': 'mobily', 'tablety': 'pocitace', 'notebooky': 'pocitace', 'sluchatka': 'audio', 'repro': 'audio'}.get(sb, 'gadgety')
    if c == 'sport' and sb in ('fitness', 'vyziva'): return 'fitness'
    return c

PATHS = {}  # (obchod, cesta kategorie, zařazení) -> [počet, ukázkový název]; pro kontrolu zařazování

def build(shops, feeds, state, now, open_fn=open_feed, log=print):
    today = day_no(now.date())
    stamp = now.strftime('%Y-%m-%dT%H:%M')
    hist = state.get('h', {})
    last, new_last = state.get('last', {}), {}
    products, report = [], []
    for sh in shops:
        name = sh['shop']; url = feeds.get(name)
        if not url: report.append(f'{name}: chybí adresa feedu'); continue
        t0 = time.time(); groups = {}; n = 0
        try:
            src = open_fn(url)
            fmt = sh.get('format')
            it = google_items(src) if fmt == 'google' else ehub_items(src) if fmt == 'ehub' else heureka_items(src)
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
            brand = fix_brand((f['brand'] or '').strip(), nm)
            res = categorize(f['path'], nm)
            if not res and not (f['path'] or '').strip() and sh.get('defaultCat'):
                dc = LEGACY.get(sh['defaultCat'], (sh['defaultCat'], None)); res = (dc[0], dc[1], False)
            lab = (res[0] + ('/' + res[1] if res[1] else '') + ('*' if res[2] else '')) if res else None
            pr = PATHS.setdefault((name, f['path'] or '(bez kategorie)', lab), [0, nm]); pr[0] += 1
            if not res: continue
            cat, sub, acc = res
            if cat in sh.get('skipCats', []): continue
            now_p = cheapest['price']
            if now_p < MIN_PRICE: continue
            key = slug(name) + ':' + str(gk)
            # historie: [den, nejnižší, nejvyšší cena toho dne] (starší záznamy měly jen [den, cena])
            h = [x if len(x) == 3 else [x[0], x[1], x[1]] for x in hist.get(key, []) if x[0] >= today - HISTORY_DAYS]
            if h and h[-1][0] == today: h[-1] = [today, min(h[-1][1], now_p), max(h[-1][2], now_p)]
            else: h.append([today, now_p, now_p])
            hist[key] = h
            prev = last.get(key); new_last[key] = now_p
            low = min(x[1] for x in h)
            if cheapest.get('orig'):
                was = cheapest['orig']; basis = 'obchod'
            else:
                was = max(x[2] for x in h); basis = 'historie'
            was = max(was, now_p)
            disc = round((1 - now_p / was) * 100) if was else 0
            days = today - min(x[0] for x in h)
            at_min = days >= 7 and now_p <= low
            hy = hype_of(brand)
            sc = score(disc, hy, sh.get('trust', 7), at_min)
            stock = bool(avail)
            products.append({
                'id': key.replace(':', '-'), 'cat': cat, 'sub': sub, 'acc': acc or None, 'kind': kind_of(cat, sub), 'brand': brand or name, 'name': nm,
                'variant': ('Skladem: ' + ', '.join(sizes[:12])) if sizes else None,
                'shop': name, 'url': cheapest['url'] if cheapest.get('tracked') else aff(cheapest['url'], sh.get('bid')), 'aff': bool(sh.get('bid')) or bool(cheapest.get('tracked')),
                'now': now_p, 'was': was, 'disc': disc, 'basis': basis, 'hype': hy, 'stock': stock, 'pass': stock,
                'reason': None if stock else 'Není skladem.', 'score': sc, 'checked': stamp, 'img': fix_img(f['img'] or cheapest['img']),
                'imgby': name, 'days': days, 'low': low, 'lowDays': min(HISTORY_DAYS, days + 1),
                'prev': prev if prev and prev != now_p else None})
            kept += 1
        report.append(f'{name}: {n} položek ve feedu, {len(groups)} produktů, do šupu {kept} ({time.time() - t0:.0f} s)')
        log(report[-1])
    # historie: zahodit produkty, které 45 dní nikdo neviděl
    hist = {k: v for k, v in hist.items() if v and v[-1][0] >= today - 45}
    products.sort(key=lambda d: -d['score']['total'])
    return products, {'h': hist, 'last': new_last, 'updated': stamp, 'prevAt': state.get('updated')}, report

def pick_top(products, n=12):
    """Pestrý výběr pro web: nejlepší skóre, max. 3 z kategorie, 4 z obchodu, bez barevných variant téhož."""
    out, seen, per_cat, per_shop = [], set(), {}, {}
    def base(d): return norm(d['brand'] + ' ' + d['name']).split(' ')[:4]
    def take(cond, cat_lim, shop_lim):
        for d in products:
            if len(out) >= n: return
            if d['id'] in out or not (d['pass'] and d['img']) or d.get('acc') or not cond(d): continue
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
           'prevAt': state.get('prevAt'), 'top': pick_top(products), 'deals': products}
    with open('out/full.json', 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))  # pro split_catalog.py
    # latest.json zůstává ve starých kategoriích, dokud běží starší verze appky
    old = dict(out, deals=[dict(d, cat=legacy_cat(d)) for d in products if legacy_cat(d)])
    with open('out/latest.json', 'w', encoding='utf-8') as f: json.dump(old, f, ensure_ascii=False, separators=(',', ':'))
    with open('out/state.json', 'w', encoding='utf-8') as f: json.dump(state, f, ensure_ascii=False, separators=(',', ':'))
    with open('out/paths.json', 'w', encoding='utf-8') as f:  # přehled zařazení cest kategorií (kontrola)
        json.dump(sorted([[k[0], k[1], v[0], k[2], v[1]] for k, v in PATHS.items()], key=lambda r: (r[0], -r[2])), f, ensure_ascii=False, separators=(',', ':'))
    by = {}
    for d in products: by[d['cat']] = by.get(d['cat'], 0) + 1
    print('Celkem', len(products), 'produktů:', by)
    print('Velikost latest.json:', os.path.getsize('out/latest.json') // 1024, 'kB')

if __name__ == '__main__':
    main()
