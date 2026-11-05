#!/usr/bin/env python3
"""Allegro.cz pro šup: produkty z Tradedoubleru (feed přes Dognet), stahuje se zvlášť jednou denně.

Hlavní hodinový běh (build_catalog.py) by se s tím nevešel do času, proto tenhle skript uloží výsledek
do cache/allegro.json.gz a workflow „Allegro pro šup“ ho dá do větve allegro, odkud si ho hlavní běh vezme.

- API Tradedoubleru vrátí na jeden dotaz nejvýš 1000 produktů, katalog se proto dělí podle ceny.
- Bereme jen nabídky Smart! (Allegro je dovolí jen prodejcům s kvalitou prodeje aspoň „Neutrální“),
  skladem, od MIN_PRICE Kč, bez Auto-moto a jen to, co šup umí zařadit do své kategorie.
- Adresa feedu i s tokenem je v tajné proměnné TD_FEED_URL (tlačítko „Copy URL“ u feedu Allegro.cz v Dognetu).
"""
import gzip, json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc

API = 'https://api.tradedoubler.com/1.0/products.json'
PAGE = 1000                      # víc API na jeden dotaz nevrátí
SKIP_TOP = ('auto moto',)        # autodíly, pneumatiky, nářadí – do šupu nepatří
OUT = 'cache/allegro.json.gz'
WORKERS = 6                      # souběžné dotazy (jeden po druhém by stahování trvalo přes hodinu)
BUDGET = 40 * 60                 # nejvýš 40 minut, pak se uloží, co se stihlo

def parse_feed_url(u):
    fid = re.search(r'fid=(\d+)', u or '')
    tok = re.search(r'token=([A-Za-z0-9]+)', u or '')
    if not fid or not tok: raise SystemExit('TD_FEED_URL: v adrese chybí fid nebo token')
    return fid.group(1), tok.group(1)

def call(fid, token, lo, hi, size, tries=4):
    url = f'{API};fid={fid};minPrice={lo:.2f};maxPrice={hi:.2f};page=1;pageSize={size}?token={token}'
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': bc.UA, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'})
            r = urllib.request.urlopen(req, timeout=90); b = r.read()
            if r.headers.get('Content-Encoding') == 'gzip' or b[:2] == b'\x1f\x8b': b = gzip.decompress(b)
            return json.loads(b)
        except Exception:
            if k == tries - 1: raise
            time.sleep(5 * (k + 1))

def offer_url(u):
    """Z partnerského odkazu Tradedoubleru vytáhne čistou adresu nabídky (https://allegro.cz/nabidka/…-ID)."""
    m = re.search(r'url\((.+)\)\s*$', u or '')
    t = urllib.parse.unquote(m.group(1)) if m else (u or '')
    q = urllib.parse.parse_qs(urllib.parse.urlparse(t).query)
    if q.get('redirect_url'): t = q['redirect_url'][0]
    t = t.split('#')[0].split('?')[0]
    return t if re.match(r'https://allegro\.cz/nabidka/[^/]+-\d+$', t) else ''

def item(p):
    """Produkt z API → malý záznam pro cache, nebo None (nepatří do šupu)."""
    path = (((p.get('categories') or [{}])[0]) or {}).get('name') or ''
    if bc.norm(path).startswith(SKIP_TOP): return None
    f = {x.get('name'): x.get('value') for x in p.get('fields') or []}
    if 'smart' not in f or str(f.get('smart')).lower() == 'false': return None
    if str(f.get('availability') or 'in stock').lower() != 'in stock': return None
    of = (p.get('offers') or [{}])[0] or {}
    price = bc.num(((((of.get('priceHistory') or [{}])[0]) or {}).get('price') or {}).get('value'))
    url = offer_url(of.get('productUrl') or '')
    name = re.sub(r'\s+', ' ', p.get('name') or '').strip()
    if not price or price < bc.MIN_PRICE or not url or not name: return None
    if bc.categorize(path, name) is None: return None   # díly, filtry, tonery… (a menší cache)
    return {'id': str(of.get('sourceProductId') or url.rsplit('-', 1)[-1]), 'name': name, 'path': path, 'url': url,
            'img': (p.get('productImage') or {}).get('url') or '', 'price': price, 'ean': f.get('gtin') or ''}

def say(*a): print(*a, flush=True)

def fetch_all(fid, token, call=call, log=say, workers=WORKERS, budget=BUDGET):
    """Celý katalog po cenových pásmech (do každého se vejde nejvýš PAGE produktů), několik dotazů souběžně.
    1. krok: počty produktů v pásmech (malé dotazy), pásma nad PAGE se půlí; 2. krok: stažení pásem.
    Když dojde čas (budget v sekundách), uloží se, co se stihlo – lepší část Allegra než nic."""
    t0, calls, seen, out, n = time.time(), 0, set(), [], 0
    def count(r): return r, ((call(fid, token, r[0], r[1], 1) or {}).get('productHeader') or {}).get('totalHits') or 0
    def page(rt): (lo, hi), tot = rt; return (call(fid, token, lo, hi, min(tot, PAGE)) or {}).get('products') or []
    todo, ready = [(float(bc.MIN_PRICE), 1000000.0)], []
    with ThreadPoolExecutor(workers) as ex:
        while todo:
            nxt = []
            for (lo, hi), tot in ex.map(count, todo):
                calls += 1
                if not tot: continue
                if tot > PAGE and hi - lo > 1:
                    mid = float(int((lo + hi) / 2)); nxt += [(lo, mid - 0.01), (mid, hi)]
                else:
                    if tot > PAGE: log(f'{lo:.2f} Kč: {tot} produktů za stejnou cenu, beru prvních {PAGE}')
                    ready.append(((lo, hi), tot))
            todo = nxt
        log(f'{len(ready)} cenových pásem, {sum(t for _, t in ready)} produktů ({calls} dotazů, {time.time() - t0:.0f} s)')
        futs = [ex.submit(page, rt) for rt in ready]
        for i, f in enumerate(futs):
            if budget and time.time() - t0 > budget:
                log(f'Došel čas: staženo {i} z {len(futs)} pásem')
                for g in futs[i:]: g.cancel()
                break
            for p in f.result():
                n += 1
                x = item(p)
                if x and x['id'] not in seen: seen.add(x['id']); out.append(x)
            calls += 1
            if i % 50 == 49: log(f'  {i + 1}/{len(futs)} pásem, {len(out)} do cache ({time.time() - t0:.0f} s)')
    return out, n, calls

def main():
    u = (os.environ.get('TD_FEED_URL') or '').strip()
    if not u: print('Chybí TD_FEED_URL, Allegro přeskakuji.'); return
    fid, token = parse_feed_url(u)
    t0 = time.time()
    items, n, calls = fetch_all(fid, token)
    print(f'Allegro: {n} produktů v API, do cache {len(items)} ({calls} dotazů, {time.time() - t0:.0f} s)')
    if not items: sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump({'at': datetime.now(bc.TZ).isoformat(timespec='minutes'), 'items': items}, f, ensure_ascii=False, separators=(',', ':'))
    print('Velikost cache:', os.path.getsize(OUT) // 1024, 'kB')

if __name__ == '__main__':
    main()
