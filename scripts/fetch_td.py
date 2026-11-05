#!/usr/bin/env python3
"""Allegro.cz pro šup: produktové feedy Allegra od Dognetu (JSON Lines), stahuje se zvlášť jednou denně.

Hlavní hodinový běh (build_catalog.py) by se s tím nevešel do času, proto tenhle skript uloží výsledek
do cache/allegro.json.gz a workflow „Allegro pro šup“ ho dá do větve allegro, odkud si ho hlavní běh vezme.

- Feedy jsou velké (GB), čtou se proudem řádek po řádku; když dojde čas, uloží se, co se stihlo.
  (API Tradedoubleru se nedalo použít: dovolí jen pár dotazů a celý katalog by trval přes hodinu.)
- Bereme jen nové nabídky Smart! (Allegro je dovolí jen prodejcům s kvalitou prodeje aspoň „Neutrální“),
  skladem, od MIN_PRICE Kč, bez Auto-moto a jen to, co šup umí zařadit do své kategorie.
- Odkaz do obchodu je čistá adresa nabídky; partnerský odkaz Dognetu z ní udělá build_catalog.py.
"""
import gzip, json, os, re, sys, time, urllib.parse, urllib.request
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc

BASE = 'https://dl.product-service.dognet.sk/fp/'
FEEDS = ['allegro.cz', 'allegro.cz-sport_and_tourism', 'allegro.cz-electronics']   # v tomhle pořadí, když dojde čas
OUT = 'cache/allegro.json.gz'
BUDGET = 45 * 60                 # nejvýš 45 minut, pak se uloží, co se stihlo
SKIP_TOP = ('auto moto',)        # autodíly, pneumatiky, nářadí – do šupu nepatří
NO_BRAND = {'zadna', 'bez znacky', 'neznama', 'no name', 'noname', 'jina', 'ostatni', 'generic'}

def say(*a): print(*a, flush=True)

def offer_url(u):
    """Z partnerského odkazu (Allegro / Tradedoubler) vytáhne čistou adresu nabídky (https://allegro.cz/nabidka/…-ID)."""
    m = re.search(r'url\((.+)\)\s*$', u or '')
    t = urllib.parse.unquote(m.group(1)) if m else (u or '')
    q = urllib.parse.parse_qs(urllib.parse.urlparse(t).query)
    if q.get('redirect_url'): t = q['redirect_url'][0]
    t = t.split('#')[0].split('?')[0]
    return t if re.match(r'https://allegro\.cz/nabidka/[^/]+-\d+$', t) else ''

def attrs(o):
    a = (o.get('attributes') or {}).get('attribute') or []
    return {x.get('name'): ((x.get('values') or [{}])[0] or {}).get('value') for x in a if isinstance(x, dict)}

def item(o):
    """Řádek feedu → malý záznam pro cache, nebo None (nepatří do šupu)."""
    path = o.get('categoryNamePath') or ''
    if bc.norm(path).startswith(SKIP_TOP): return None
    if str(o.get('customLabel2') or '').lower() != 'smart': return None
    if str(o.get('availability') or 'in stock').lower() != 'in stock': return None
    a = attrs(o)
    if a.get('Stav') and bc.norm(a['Stav']) not in ('nove', 'nova', 'novy'): return None
    price = bc.num(o.get('price'))
    url = offer_url(o.get('itemLink') or '')
    name = re.sub(r'\s+', ' ', o.get('name') or '').strip()
    if not price or price < bc.MIN_PRICE or not url or not name: return None
    if bc.categorize(path, name) is None: return None   # díly, filtry, tonery… (a menší cache)
    brand = (a.get('Značka') or '').strip()
    if bc.norm(brand) in NO_BRAND: brand = ''
    return {'id': str(o.get('id') or url.rsplit('-', 1)[-1]), 'name': name, 'brand': brand, 'path': path, 'url': url,
            'img': o.get('imageLink') or '', 'price': price, 'ean': o.get('gtin') or ''}

def read_feed(lines, out, seen, deadline=None):
    """Projde řádky jednoho feedu; vrátí (počet řádků, dočteno do konce?)."""
    n = 0
    for line in lines:
        n += 1
        if b'smart' not in line: continue                 # rychlé síto, json jen u kandidátů
        try: o = json.loads(line)
        except ValueError: continue
        x = item(o)
        if x and x['id'] not in seen: seen.add(x['id']); out.append(x)
        if n % 200000 == 0:
            say(f'  {n} řádků, {len(out)} do cache')
            if deadline and time.time() > deadline: return n, False
    return n, True

def open_stream(name):
    req = urllib.request.Request(BASE + name + '.jsonl', headers={'User-Agent': bc.UA, 'Accept-Encoding': 'gzip'})
    r = urllib.request.urlopen(req, timeout=120)
    return gzip.GzipFile(fileobj=r) if r.headers.get('Content-Encoding') == 'gzip' else r

def main():
    t0 = time.time(); deadline = t0 + BUDGET
    out, seen, done = [], set(), []
    for name in FEEDS:
        if time.time() > deadline: say(f'{name}: došel čas, přeskakuji'); continue
        t1 = time.time()
        try:
            n, whole = read_feed(open_stream(name), out, seen, deadline)
        except Exception as e:
            say(f'{name}: chyba {e}'); continue
        say(f'{name}: {n} řádků za {time.time() - t1:.0f} s{"" if whole else " (nedočteno, došel čas)"}, celkem {len(out)} do cache')
        done.append(name)
    if not out: say('Allegro: nic nestaženo'); sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump({'at': datetime.now(bc.TZ).isoformat(timespec='minutes'), 'feeds': done, 'items': out}, f, ensure_ascii=False, separators=(',', ':'))
    say(f'Allegro: {len(out)} produktů, cache {os.path.getsize(OUT) // 1024} kB, {time.time() - t0:.0f} s')

if __name__ == '__main__':
    main()
