#!/usr/bin/env python3
"""Velké katalogy z CJ (Answear, Allegro) pro šup: stahují se zvlášť dvakrát denně.

Stažení přes CJ Product Feed API trvá desítky minut (Answear ~100 000 položek = 20–28 min) a CJ při mnoha dotazech
odpovídá chybou 403, takže by se do hodinového běhu (limit 50 min) nevešlo. Tenhle skript uloží každý obchod,
který má v config/shops.json "cjFile", do vlastního souboru (cache/cj/<obchod>.json.gz); workflow „CJ pro šup“
je dá do větve cj a hodinový běh (build_catalog.py) si je odtamtud jen načte.

- Do souboru jde jen to, co má šanci do šupu: skladem, nové, od MIN_PRICE Kč, bez erotiky (a jen vybrané značky, má-li obchod "brands").
- "cjMinDisc": N = jen zboží, které obchod sám hlásí zlevněné aspoň o N % (Allegro: celý katalog je obrovský).
- Když stažení obchodu selže, zůstane jeho předchozí soubor (workflow ho předem načte z větve cj).
- Když dojde čas (BUDGET), uloží se, co se stihlo, a další obchody se přeskočí.
- Vypnutý obchod ("off") se stahuje jen s "cjTry": true – na zkoušku, než se zapne (Allegro: čeká na kontrolu prodejců).
- Na začátku vypíše, jaké filtry a pole CJ API nabízí (např. jestli zná prodejce na Allegru) – podklad pro další úpravy.

Spuštění: CJ_TOKEN=… python scripts/fetch_cj.py [obchod …]   (bez jmen = všechny s "cjFile")
"""
import gzip, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'shops.json')
BUDGET = 150 * 60          # workflow má limit 180 min; po 150 min se uloží, co se stihlo
WAITS = (60, 180, 300)     # CJ při mnoha dotazech vrací 403/429: počkat a zkusit znovu
ADULT = re.compile(r'\b(eroti\w*|sex ?shop\w*|porno\w*|xxx|dorcel|vibrator\w*|dild\w*|bdsm|milfs?|penis\w*)\b')
SLEEP = time.sleep

def say(*a): print(*a, flush=True)

def call(token, variables):
    """bc.cj_call s delším čekáním při omezení počtu dotazů (403/429) a výpadku CJ (5xx)."""
    for wait in WAITS + (None,):
        try:
            return bc.cj_call(token, variables)
        except urllib.error.HTTPError as e:
            if wait is None or not (e.code in (403, 429) or e.code >= 500): raise
            say(f'  CJ vrátil {e.code}, čekám {wait} s'); SLEEP(wait)

def keep(x, min_disc=0, brands=None):
    """Záznam z bc.cj_items → patří do souboru? (stejná síta jako hodinový běh, ať soubor není zbytečně velký)"""
    if not x.get('price') or not x.get('url') or x['price'] < bc.MIN_PRICE or not x.get('stock'): return False
    if bc.condition_of(x) is None: return False                      # použité, bazar
    if ADULT.search(bc.norm((x.get('name') or '') + ' ' + (x.get('path') or ''))): return False
    if brands and bc.norm(x.get('brand')) not in brands: return False
    if min_disc and not (x.get('orig') and x['price'] <= x['orig'] * (1 - min_disc / 100)): return False
    return True

def schema(token):
    """Vypíše filtry dotazu shoppingProducts a pole produktu (jednorázový přehled do logu, chyba nevadí)."""
    q = '{ q: __type(name: "Query") { fields { name args { name } type { name ofType { name } } } } }'
    try:
        req = urllib.request.Request(bc.CJ_API, data=json.dumps({'query': q}).encode(),
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'User-Agent': bc.UA})
        f = next(x for x in json.load(urllib.request.urlopen(req, timeout=60))['data']['q']['fields'] if x['name'] == 'shoppingProducts')
        say('CJ shoppingProducts – filtry:', ', '.join(a['name'] for a in f['args']))
        t = f['type']['name'] or (f['type'].get('ofType') or {}).get('name')
        q2 = '{ t: __type(name: "%s") { fields { name type { name ofType { name } } } } }' % t
        req = urllib.request.Request(bc.CJ_API, data=json.dumps({'query': q2}).encode(), headers=dict(req.headers))
        lst = next(x for x in json.load(urllib.request.urlopen(req, timeout=60))['data']['t']['fields'] if x['name'] == 'resultList')
        t3 = lst['type']['name'] or (lst['type'].get('ofType') or {}).get('name')
        q3 = '{ t: __type(name: "%s") { name fields { name } ofType { name fields { name } ofType { name fields { name } } } } }' % t3
        req = urllib.request.Request(bc.CJ_API, data=json.dumps({'query': q3}).encode(), headers=dict(req.headers))
        r = json.load(urllib.request.urlopen(req, timeout=60))['data']['t']
        while r and not r.get('fields'): r = r.get('ofType')
        say('CJ produkt – pole:', ', '.join(x['name'] for x in (r or {}).get('fields') or []))
    except Exception as e:
        say('Přehled CJ API se nenačetl:', type(e).__name__, str(e)[:200])

def fetch(sh, token, deadline, items_fn=bc.cj_items):
    """Stáhne jeden obchod; vrátí (záznamy do souboru, položek v CJ, dočteno do konce?)."""
    brands = {bc.norm(b) for b in sh.get('brands') or []}
    out, n = [], 0
    for x in items_fn(str(sh['cj']), token, sh.get('cjAds'), call=call):
        n += 1
        if keep(x, sh.get('cjMinDisc') or 0, brands): out.append(x)
        if n % 10000 == 0 and time.time() > deadline: return out, n, False
    return out, n, True

def save(path, sh, out, n, whole):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump({'at': datetime.now(bc.TZ).isoformat(timespec='minutes'), 'shop': sh['shop'], 'total': n, 'whole': whole, 'items': out},
                  f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)

def main(argv):
    token = os.environ.get('CJ_TOKEN')
    if not token: say('Chybí CJ_TOKEN.'); sys.exit(1)
    with open(CONFIG, encoding='utf-8') as f: shops = json.load(f)
    shops = [s for s in shops if s.get('cjFile') and s.get('cj') and (not s.get('off') or s.get('cjTry')) and (not argv or s['shop'] in argv)]
    if not shops: say('Žádný obchod s "cjFile".'); sys.exit(1)
    t0 = time.time(); deadline = t0 + BUDGET; saved = 0
    schema(token)
    for sh in shops:
        name = sh['shop']
        if time.time() > deadline: say(f'{name}: došel čas, zůstává předchozí soubor'); continue
        t1 = time.time()
        try:
            out, n, whole = fetch(sh, token, deadline)
        except Exception as e:
            say(f'{name}: chyba ({type(e).__name__}: {str(e)[:200]}) – zůstává předchozí soubor'); continue
        if not out: say(f'{name}: {n} položek v CJ, do souboru nic – zůstává předchozí soubor'); continue
        save(sh['cjFile'], sh, out, n, whole); saved += 1
        say(f'{name}: {n} položek v CJ, do souboru {len(out)} ({time.time() - t1:.0f} s, {os.path.getsize(sh["cjFile"]) // 1024} kB)'
            + ('' if whole else ' – nedočteno, došel čas'))
    say(f'Hotovo za {time.time() - t0:.0f} s, uloženo {saved} z {len(shops)} obchodů.')
    if not saved: sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[1:])
