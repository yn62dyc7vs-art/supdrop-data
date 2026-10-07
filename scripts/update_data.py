#!/usr/bin/env python3
"""Denní aktualizace dat šupu.

Pro každou položku z data/watchlist.json načte stránku produktu v obchodě,
přečte aktuální cenu a dostupnost (strukturovaná data schema.org na stránce),
spočítá slevu a skóre, uloží historii cen a zapíše data/latest.json.
Web i appka si data/latest.json načtou přímo z repozitáře supdrop-data (bez nasazení na Netlify).

Až budou k dispozici produktové feedy z affiliate sítí, nahradí čtení stránek.
"""
import json, re, sys, time, html, urllib.request, urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Prague')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/129.0 Safari/537.36')
STORE_TRUST = {'Alza': 9, 'Notino': 9, 'Smarty': 8}

def load(p, default):
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except FileNotFoundError:
        return default

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'cs-CZ,cs;q=0.9',
                                               'Accept': 'text/html,application/xhtml+xml'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode('utf-8', 'replace')

def num(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = re.sub(r'[^\d,.\-]', '', str(v))
    if ',' in s and '.' in s: s = s.replace('.', '').replace(',', '.')
    else: s = s.replace(',', '.')
    try: return float(s)
    except ValueError: return None

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def parse(page):
    """Vrátí (cena, skladem) ze schema.org dat, nebo (None, None)."""
    price, stock = None, None
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try: data = json.loads(html.unescape(m.group(1).strip()))
        except Exception: continue
        for node in walk(data):
            t = node.get('@type')
            if t in ('Offer', 'AggregateOffer') or (isinstance(t, list) and 'Offer' in t):
                p = num(node.get('price') or node.get('lowPrice'))
                if p and (price is None or p < price): price = p
                a = str(node.get('availability', ''))
                if a:
                    ok = any(k in a for k in ('InStock', 'PreOrder', 'LimitedAvailability', 'OnlineOnly'))
                    stock = ok if stock is None else (stock or ok)
    if price is None:
        for pat in (r'itemprop=["\']price["\'][^>]*content=["\']([\d.,\s]+)', r'content=["\']([\d.,\s]+)["\'][^>]*itemprop=["\']price',
                    r'property=["\'](?:product|og):price:amount["\'][^>]*content=["\']([\d.,\s]+)'):
            m = re.search(pat, page, re.I)
            if m: price = num(m.group(1)); break
    if price is None:  # poslední možnost: cena v datech stránky
        m = re.search(r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)', page)
        if m: price = num(m.group(1))
    if stock is None:
        low = page.lower()
        if 'vyprodáno' in low or 'není skladem' in low or 'outofstock' in low: stock = False
        elif 'skladem' in low or 'instock' in low: stock = True
    return (round(price) if price else None), stock

def score(disc, hype, shop, at_min):
    sleva = min(40, round(max(disc, 0) * 40 / 25))
    poptavka = min(25, round((hype or 5) * 2.5))
    minimum = 7 if at_min is None else (15 if at_min else 3)
    obchod = STORE_TRUST.get(shop, 7)
    nalehavost = 5
    return {'total': sleva + poptavka + minimum + obchod + nalehavost, 'sleva': sleva, 'poptavka': poptavka,
            'minimum': minimum, 'obchod': obchod, 'nalehavost': nalehavost}

def main():
    now = datetime.now(TZ)
    stamp = now.strftime('%Y-%m-%dT%H:%M'); today = now.strftime('%Y-%m-%d')
    watch = load('data/watchlist.json', [])
    old = load('data/latest.json', {'deals': []})
    prev = {d['id']: d for d in old.get('deals', [])}
    hist = load('data/history.json', {})
    deals, ok, failed = [], 0, []
    for w in watch:
        p = prev.get(w['id'], {})
        try:
            price, stock = parse(fetch(w['url']))
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            price, stock = None, None; print(f"! {w['id']}: {e}")
        ref0 = w.get('ref') or price
        if price is not None and ref0 and not (0.3 * ref0 <= price <= 1.6 * ref0):
            print(f"! {w['id']}: podezřelá cena {price} (čekáno kolem {ref0}), přeskakuji"); price = None
        if price is None:
            failed.append(w['id'])
            if p:  # necháme poslední známá data, po 2 dnech bez kontroly položku vyřadíme
                d = dict(p); d['stale'] = d.get('stale', 0) + 1
                if d['stale'] >= 2: d['pass'] = False; d['reason'] = 'Cenu se nepodařilo ověřit.'
                deals.append(d)
            time.sleep(2); continue
        ok += 1
        if stock is None: stock = p.get('stock', True)
        ref = max(w.get('ref') or price, price)
        disc = round((1 - price / ref) * 100) if ref else 0
        h = [x for x in hist.get(w['id'], []) if x[0] >= (now - timedelta(days=60)).strftime('%Y-%m-%d')]
        h = [x for x in h if x[0] != today] + [[today, price]]
        hist[w['id']] = h
        last30 = [x[1] for x in h if x[0] >= (now - timedelta(days=30)).strftime('%Y-%m-%d')]
        at_min = (price <= min(last30)) if len(last30) >= 7 else None
        sc = score(disc, w.get('hype'), w['shop'], at_min)
        d = {k: w.get(k) for k in ('id', 'cat', 'kind', 'brand', 'name', 'variant', 'shop', 'url', 'hype', 'img', 'imgby')}
        d.update({'now': price, 'was': ref, 'disc': disc, 'stock': bool(stock), 'checked': stamp, 'score': sc,
                  'pass': bool(stock), 'reason': None if stock else 'Není skladem.'})
        deals.append(d)
        print(f"  {w['id']}: {price} Kč (ref {ref}, -{disc} %, {'skladem' if stock else 'NENÍ skladem'})")
        time.sleep(2)
    out = {'batch': today if ok else old.get('batch'), 'checkedAt': stamp if ok else old.get('checkedAt'),
           'source': 'Kontrola cen a skladu přímo na webech obchodů', 'deals': deals,
           'note': f'Ověřeno {ok} z {len(watch)} položek.' + (f" Nepodařilo se: {', '.join(failed)}." if failed else '')}
    with open('data/latest.json', 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, indent=1)
    with open('data/history.json', 'w', encoding='utf-8') as f: json.dump(hist, f, ensure_ascii=False, indent=1)
    print(out['note'])
    if not ok:
        print('Žádnou cenu se nepodařilo ověřit.'); sys.exit(1)

if __name__ == '__main__':
    main()
