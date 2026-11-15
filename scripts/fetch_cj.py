#!/usr/bin/env python3
"""Velké katalogy z CJ (Answear, Allegro) pro šup: stahují se zvlášť dvakrát denně.

Stažení přes CJ Product Feed API trvá desítky minut (Answear ~100 000 položek = 20–28 min) a CJ při mnoha dotazech
odpovídá chybou 403, takže by se do hodinového běhu (limit 50 min) nevešlo. Tenhle skript uloží každý obchod,
který má v config/shops.json "cjFile", do vlastního souboru (cache/cj/<obchod>.json.gz); workflow „CJ pro šup“
je dá do větve cj a hodinový běh (build_catalog.py) si je odtamtud jen načte.

- Do souboru jde jen to, co má šanci do šupu: skladem, nové, od MIN_PRICE Kč, bez erotiky (a jen vybrané značky, má-li obchod "brands").
- "cjMinDisc": N = jen zboží, které obchod sám hlásí zlevněné aspoň o N % (Allegro: celý katalog je obrovský).
- Když stažení obchodu selže, zůstane jeho předchozí soubor (workflow ho předem načte z větve cj).
- Když dojde čas (BUDGET, hlídá se před každým dotazem i čekáním), uloží se, co se stihlo, a další obchody se přeskočí.
  Workflow tak vždy doběhne do uložení (dřív ho limit 180 min při pomalém Allegru zrušil a neuložilo se nic).
- Vypnutý obchod ("off") se stahuje jen s "cjTry": true – na zkoušku, než se zapne (Allegro: čeká na kontrolu prodejců).
- Na začátku vypíše, jaké filtry a pole CJ API nabízí (např. jestli zná prodejce na Allegru) – podklad pro další úpravy.

Spuštění: CJ_TOKEN=… python scripts/fetch_cj.py [obchod …]   (bez jmen = všechny s "cjFile")

Paralelní stahování (workflow cj.yml s maticí úloh; bez přepínačů se chová jako dřív):
  --slice K/N obchod   stáhne jen K-té z N cenových pásem (pásma podle minulého souboru, funkce bands) do
                       CJ_PARTS/<soubor>.K.json.gz – hlavní soubor obchodu se nemění.
  --assemble obchod    poskládá pásma z CJ_PARTS do cjFile; chybějící/nedočtené pásmo doplní z minulého souboru,
                       pokud ta data nejsou starší než 36 h (CJ_MAX_AGE). Nevolá CJ API, token netřeba.
  Proměnné: CJ_BUDGET_MIN (celkový limit běhu v min, výchozí 140), CJ_SHOP_BUDGET_MIN (strop obchodu bez cjBudget, výchozí 40),
            CJ_JITTER (náhodné prodloužení čekání po 403/429 v s, výchozí 0), CJ_SCHEMA=0 (nevypisovat přehled API), CJ_PARTS (adresář pásem, výchozí cache/cj-parts).
"""
import gzip, json, os, random, re, sys, time, urllib.error, urllib.request
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'shops.json')
BUDGET = int(os.environ.get('CJ_BUDGET_MIN') or 140) * 60   # workflow má limit 180 min; po 140 min se uloží, co se stihlo
SHOP_BUDGET = int(os.environ.get('CJ_SHOP_BUDGET_MIN') or 40) * 60      # strop na jeden obchod (klíč "cjBudget" v minutách ho mění); pomalý obchod tak nespotřebuje čas ostatním
WAITS = (60, 180, 300)     # CJ při mnoha dotazech vrací 403/429: počkat a zkusit znovu
ADULT = re.compile(r'\b(eroti\w*|sex ?shop\w*|porno\w*|xxx|dorcel|vibrator\w*|dild\w*|bdsm|milfs?|penis\w*)\b')
SLEEP = time.sleep
JITTER = float(os.environ.get('CJ_JITTER') or 0)   # souběžné úlohy: ať se po 403/429 neozvou všechny ve stejnou chvíli
PARTS = os.environ.get('CJ_PARTS') or os.path.join('cache', 'cj-parts')
FALLBACK_EDGES = (0, 300, 500, 700, 1000, 1400, 2000, 3000, 5000)   # pásma, když minulý soubor chybí (Answear: móda)

def say(*a): print(*a, flush=True)

DEADLINE = [None]          # čas, kdy se má stahování ukončit (nastavuje fetch); hlídá se před každým dotazem na CJ
class OutOfTime(Exception): pass

def call(token, variables):
    """bc.cj_call s delším čekáním při omezení počtu dotazů (403/429), výpadku CJ (5xx) a vypršení spojení.
    Když by čekání nebo další dotaz přesáhly DEADLINE, skončí výjimkou OutOfTime (fetch pak uloží, co má)."""
    for wait in WAITS + (None,):
        if DEADLINE[0] and time.time() > DEADLINE[0]: raise OutOfTime()
        try:
            return bc.cj_call(token, variables)
        except urllib.error.HTTPError as e:
            if wait is None or not (e.code in (403, 429) or e.code >= 500): raise
            why = str(e.code)
        except (TimeoutError, urllib.error.URLError, ConnectionError) as e:
            if wait is None: raise
            why = type(e).__name__
        if DEADLINE[0] and time.time() + wait > DEADLINE[0]: raise OutOfTime()
        wait += random.uniform(0, JITTER) if JITTER else 0
        say(f'  CJ: {why}, čekám {wait:.0f} s'); SLEEP(wait)

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
    DEADLINE[0] = deadline
    try:
        for x in items_fn(str(sh['cj']), token, sh.get('cjAds'), call=call):
            n += 1
            if keep(x, sh.get('cjMinDisc') or 0, brands): out.append(x)
            if n % 10000 == 0 and time.time() > deadline: return out, n, False
    except OutOfTime:
        return out, n, False
    finally:
        DEADLINE[0] = None
    return out, n, True

def save(path, sh, out, n, whole, extra=None, at=None):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    data = {'at': at or datetime.now(bc.TZ).isoformat(timespec='minutes'), 'shop': sh['shop'], 'total': n, 'whole': whole, 'items': out}
    data.update(extra or {})
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)

def order(shops):
    """Pořadí stahování: obchody s vyšším "cjPrio" první (stabilně), ostatní v pořadí z shops.json."""
    return sorted(shops, key=lambda s: -(s.get('cjPrio') or 0))

def shop_deadline(sh, now, deadline):
    """Konec stahování jednoho obchodu: jeho strop (cjBudget v minutách, jinak SHOP_BUDGET), nejdéle celkový limit."""
    return min(deadline, now + (sh.get('cjBudget') * 60 if sh.get('cjBudget') else SHOP_BUDGET))

def keeps_old(path, out, whole):
    """Nedočtený výsledek nesmí přepsat existující soubor, který má víc položek (jinak by zmizela část nabídky)."""
    if whole or not os.path.exists(path): return False
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f: old = len(json.load(f).get('items') or [])
    except Exception:
        return False
    return old > len(out)

def bands(items, n):
    """Rozdělí obchod na n cenových pásem se zhruba stejným počtem položek (podle předchozího souboru): [(od, do), …]."""
    prices = sorted(x['price'] for x in items if x.get('price'))
    edges = [0] + [int(prices[len(prices) * i // n]) for i in range(1, n)] + [1000000]
    edges = sorted(set(edges))
    return [(float(a), float(b) - 0.01 if b != 1000000 else float(b)) for a, b in zip(edges, edges[1:])]

def slice_plan(sh, now, load=None):
    """Obchod s "cjSlices": n → v tomto běhu se stáhne jen jedno cenové pásmo (střídají se po 12 h), zbytek zůstane z minulého souboru.
    Vrací (pásmo, staré položky), nebo None = stáhnout celý obchod (bez dělení nebo bez předchozího souboru)."""
    n = sh.get('cjSlices') or 1
    if n < 2 or not os.path.exists(sh['cjFile']): return None
    try:
        with gzip.open(sh['cjFile'], 'rt', encoding='utf-8') as f: old = json.load(f).get('items') or []
    except Exception:
        return None
    if len(old) < 1000: return None
    bs = bands(old, n)
    return bs[int(now // (12 * 3600)) % len(bs)], old

def merge(old, new, rng):
    """Staré položky mimo stažené pásmo a bez nového záznamu + nově stažené."""
    ids = {x['id'] for x in new}
    return [x for x in old if x['id'] not in ids and not (rng[0] <= (x.get('price') or 0) <= rng[1])] + new

# ---------------------------------------------------------------- paralelní stahování po cenových pásmech
def read(path):
    """Obsah souboru z fetch_cj (dict), nebo None (chybí, poškozený)."""
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f: return json.load(f)
    except Exception:
        return None

def slice_bands(sh, n, old=None):
    """N cenových pásem pro paralelní stažení: podle minulého souboru (stejně položek v každém pásmu = podobně dlouhé úlohy),
    bez něj pevné hranice FALLBACK_EDGES. Všechny úlohy čtou stejný soubor z větve cj, takže pásma vyjdou stejně."""
    if old is None: old = (read(sh['cjFile']) or {}).get('items') or []
    if len(old) >= 1000: return bands(old, n)
    e = FALLBACK_EDGES
    edges = sorted({e[len(e) * i // n] for i in range(n)}) + [1000000]
    return [(float(a), float(b) - 0.01 if b != 1000000 else float(b)) for a, b in zip(edges, edges[1:])]

def parse_slice(text):
    """"2/4" → (2, 4); chybný zápis → ValueError."""
    k, n = (int(x) for x in text.split('/'))
    if not 1 <= k <= n: raise ValueError(text)
    return k, n

def part_path(sh, k, parts=None):
    base = os.path.basename(sh['cjFile']).replace('.json.gz', '')
    return os.path.join(parts or PARTS, f'{base}.{k}.json.gz')

def fetch_slice(sh, token, k, n, deadline, parts=None, items_fn=None):
    """Stáhne K-té z N pásem obchodu do souboru pásma. Vrátí cestu, nebo None (pásmo neexistuje, chyba, nic) – pak
    --assemble nechá tohle pásmo z minulého souboru."""
    bs = slice_bands(sh, n)
    if k > len(bs): say(f'{sh["shop"]}: pásmo {k}/{n} neexistuje (málo různých cen), nic nestahuji'); return None
    rng = bs[k - 1]
    say(f'{sh["shop"]}: pásmo {k}/{n}: {rng[0]:.0f}–{rng[1]:.0f} Kč')
    fn = items_fn or (lambda adv, tok, ads, call=call: bc.cj_items(adv, tok, ads, call=call, rng=rng))
    t1 = time.time()
    try:
        out, cnt, whole = fetch(sh, token, deadline, fn)
    except Exception as e:
        say(f'{sh["shop"]} {k}/{n}: chyba ({type(e).__name__}: {str(e)[:200]}) – pásmo zůstane z minulého souboru'); return None
    if not out: say(f'{sh["shop"]} {k}/{n}: {cnt} položek v CJ, do souboru nic – pásmo zůstane z minulého souboru'); return None
    path = part_path(sh, k, parts)
    save(path, sh, out, cnt, whole, {'lo': rng[0], 'hi': rng[1], 'k': k, 'of': n})
    say(f'{sh["shop"]} {k}/{n}: {cnt} položek v CJ, do pásma {len(out)} ({time.time() - t1:.0f} s)' + ('' if whole else ' – nedočteno, došel čas'))
    return path

def _age_of(data):
    """Funkce cena → čas (datetime), kdy byla data v minulém souboru naposledy celá stažená (podle "parts", jinak "at")."""
    at0 = datetime.fromisoformat(data.get('at') or '2000-01-01T00:00+00:00')
    ps = [(p['lo'], p['hi'], datetime.fromisoformat(p['at'])) for p in data.get('parts') or []]
    def f(price):
        return next((t for lo, hi, t in ps if lo <= (price or 0) <= hi), at0)
    return f

def assemble(sh, now, parts=None, max_age=None):
    """Poskládá pásma (soubory z --slice) do jednoho cjFile ve stejném tvaru, jaký čte build_catalog (at, shop, total, whole, items)
    + "parts" (rozsah a stáří každého pásma). Pravidla:
      - dočtené pásmo nahradí staré položky ve svém cenovém rozsahu (jako merge),
      - nedočtené pásmo: nové položky + staré z rozsahu, které nepřišly (nic neztratit),
      - chybějící pásmo (chyba, 403…): staré položky z rozsahu zůstanou – ale jen nejsou-li starší než max_age (36 h),
      Pravidlo keeps_old („nedočtené nepřepíše větší“) tak platí po pásmech: ubrat staré položky smí jen dočtené pásmo
      (zboží opravdu zmizelo) nebo stáří nad 36 h; celkový počet proto bez kontroly velikosti souboru.
    Vrátí True, když soubor uložil."""
    max_age = max_age or bc.CJ_MAX_AGE
    base = os.path.basename(sh['cjFile']).replace('.json.gz', '')
    d = parts or PARTS
    got = sorted((p for p in (read(os.path.join(d, f)) for f in (os.listdir(d) if os.path.isdir(d) else [])
                              if f.startswith(base + '.') and f.endswith('.json.gz') and f[len(base) + 1:-8].isdigit()) if p),
                 key=lambda p: p['lo'])
    name = sh['shop']
    if not got: say(f'{name}: žádné pásmo nedorazilo – zůstává předchozí soubor'); return False
    old = read(sh['cjFile']) or {}
    age = _age_of(old)
    new, ids = [], set()
    for p in got:
        for x in p.get('items') or []:
            if x['id'] not in ids: ids.add(x['id']); new.append(x)
    def part_of(price):
        return next((p for p in got if p['lo'] <= (price or 0) <= p['hi']), None)
    carried, gap_at, part_old = [], {}, {}
    for x in old.get('items') or []:
        if x['id'] in ids: continue
        p = part_of(x.get('price'))
        if p and p.get('whole'): continue                     # dočtené pásmo: stará položka už v nabídce není
        t = age(x.get('price'))
        if now - t > max_age: continue                        # staré pásmo by ukazovalo neplatné ceny
        carried.append(x)
        if p: part_old[p['k']] = min(t, part_old.get(p['k'], t))
        else: gap_at.setdefault(t.isoformat(timespec='minutes'), []).append(x.get('price') or 0)
    meta = [{'lo': p['lo'], 'hi': p['hi'], 'whole': bool(p.get('whole')),
             'at': min(datetime.fromisoformat(p['at']), part_old.get(p['k'], datetime.fromisoformat(p['at']))).isoformat(timespec='minutes')}
            for p in got]
    meta += [{'lo': min(v), 'hi': max(v), 'whole': False, 'at': t} for t, v in sorted(gap_at.items())]   # zbytky chybějících pásem
    of = max(p.get('of') or 0 for p in got)
    whole = len(got) >= of and all(p.get('whole') for p in got) and not gap_at
    items = new + carried
    total = sum(p.get('total') or 0 for p in got) + sum(len(v) for v in gap_at.values())
    save(sh['cjFile'], sh, items, total, whole, {'parts': meta}, at=max(datetime.fromisoformat(p['at']) for p in got).isoformat(timespec='minutes'))
    say(f'{name}: poskládáno z {len(got)}/{of} pásem: {len(new)} nových + {len(carried)} z minulého souboru'
        + ('' if whole else ' – neúplné, chybějící část z minulého souboru'))
    return True

def main(argv):
    argv = list(argv); sl = mode = None
    if '--assemble' in argv: argv.remove('--assemble'); mode = 'assemble'
    if '--slice' in argv:
        i = argv.index('--slice'); sl = parse_slice(argv[i + 1]); del argv[i:i + 2]; mode = 'slice'
    token = os.environ.get('CJ_TOKEN')
    if not token and mode != 'assemble': say('Chybí CJ_TOKEN.'); sys.exit(1)
    with open(CONFIG, encoding='utf-8') as f: shops = json.load(f)
    shops = [s for s in shops if s.get('cjFile') and s.get('cj') and (not s.get('off') or s.get('cjTry')) and (not argv or s['shop'] in argv)]
    if not shops: say('Žádný obchod s "cjFile".'); sys.exit(1)
    shops = order(shops)
    t0 = time.time(); deadline = t0 + BUDGET; saved = 0
    if mode == 'assemble':
        now = datetime.now(bc.TZ)
        for sh in shops:
            try: saved += assemble(sh, now)
            except Exception as e: say(f'{sh["shop"]}: skládání selhalo ({type(e).__name__}: {str(e)[:200]}) – zůstává předchozí soubor')
        return
    if mode == 'slice':
        for sh in shops: saved += bool(fetch_slice(sh, token, sl[0], sl[1], deadline))
        say(f'Hotovo za {time.time() - t0:.0f} s, uloženo {saved} pásem.')
        if not saved: sys.exit(1)
        return
    if os.environ.get('CJ_SCHEMA') != '0': schema(token)
    for sh in shops:
        name = sh['shop']
        if time.time() > deadline: say(f'{name}: došel čas, zůstává předchozí soubor'); continue
        t1 = time.time()
        plan = slice_plan(sh, t1)
        items_fn = bc.cj_items
        if plan:
            rng = plan[0]; say(f'{name}: dnes jen cenové pásmo {rng[0]:.0f}–{rng[1]:.0f} Kč, zbytek zůstává z minulého souboru')
            items_fn = lambda adv, tok, ads, call=call: bc.cj_items(adv, tok, ads, call=call, rng=rng)
        try:
            out, n, whole = fetch(sh, token, shop_deadline(sh, t1, deadline), items_fn)
        except Exception as e:
            say(f'{name}: chyba ({type(e).__name__}: {str(e)[:200]}) – zůstává předchozí soubor'); continue
        if not out: say(f'{name}: {n} položek v CJ, do souboru nic – zůstává předchozí soubor'); continue
        if plan: out = merge(plan[1], out, plan[0]); n = len(out)
        if keeps_old(sh['cjFile'], out, whole):
            say(f'{name}: {n} položek v CJ, nedočteno ({len(out)} do souboru) – zůstává předchozí, větší soubor'); continue
        save(sh['cjFile'], sh, out, n, whole); saved += 1
        say(f'{name}: {n} položek v CJ, do souboru {len(out)} ({time.time() - t1:.0f} s, {os.path.getsize(sh["cjFile"]) // 1024} kB)'
            + ('' if whole else ' – nedočteno, došel čas'))
    say(f'Hotovo za {time.time() - t0:.0f} s, uloženo {saved} z {len(shops)} obchodů.')
    if not saved: sys.exit(1)

if __name__ == '__main__':
    main(sys.argv[1:])
