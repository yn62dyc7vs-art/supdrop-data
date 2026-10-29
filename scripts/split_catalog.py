"""Rozdělí out/full.json (z build_catalog.py) na malé soubory, aby web a appka startovaly okamžitě.

out/v2/drops.json          start appky a webu: nejlepší nabídky z každé kategorie + top 12 (~40 kB po kompresi)
out/v2/c/<kat>/<k>.json    lehký index kategorie po stránkách (2 000 řádků, seřazeno jako p/); stejně c/<kat>~<podkat>/<k>.json a c/<kat>@<značka>/<k>.json
out/v2/i/<kat>/<b>.json    mapa id → stránka p/ (b = hash id % 256), detail produktu bez stahování indexu
out/v2/c/<kat>+<strop>/0.json  nejlepší kusy do ceny 300/500/1000/2000/5000 Kč (jen u indexů s víc stránkami)
out/v2/c/<kat>~brands.json seznam značek v kategorii
out/v2/p/<kat>-<n>.json    plné záznamy po 100 kusech ve stejném pořadí jako index
out/v2/s/<klíč>.json       vyhledávání podle začátku slova: 3 písmena, u velkých souborů delší (s/nike.json), přesné slovo s/<klíč>_.json;
                           přeplněný soubor má další stránky s/<klíč>~1.json… (podle skóre) a s/<klíč>~c.json (nejlevnější kusy)
out/v2/s/_n.json           počet produktů v každém vyhledávacím souboru + "*" rozdělené klíče, "#" počet stránek přeplněných souborů

Rozložení 3 (drops.json "layout": 3): žádný soubor, který appka stahuje, nemá víc než pár set kB.

Plný záznam: i id, c kategorie, sb podkategorie, ac příslušenství, gd pro koho (m/f/u), cn stav (z = zánovní), o počet obchodů se stejným EAN, nx druhá nejlepší nabídka, k druh, b značka, n název, s obchod, u odkaz, g obrázek, p cena, d sleva,
h hype, t skóre, e hotový partnerský odkaz, kb jiný kód partnera, w původní cena, v varianta, bs základ slevy, l nejnižší cena,
ld počet dní historie, hs změny ceny za 30 dní [dní zpět, cena, …], pv předchozí cena, dy dny ve slevě, r důvod, x neprošlo kontrolou.
"""
import json, os, re, shutil, unicodedata
from collections import Counter
from urllib.parse import unquote

SRC, OUT = 'out/full.json', 'out/v2'
CLICK = 'https://ehub.cz/system/scripts/click.php?a_aid='
PAGE, PER_CAT, PER_SUB, TREND = 100, 30, 8, 40
LAYOUT, CH, SPLIT = 3, 2000, 2000  # stránka indexu kategorie, max. řádků ve vyhledávacím souboru
BANDS = (300, 500, 1000, 2000, 5000)  # cenové stropy hlídače: c/<kat>+<strop>/0.json = nejlepší kusy do té ceny
STOP = set(('pro na s se v a the of and in for with by do od k ke z ze bez cerna cerny cerne bila bily bile modra modry '
            'seda sedy siva cervena cerveny zelena zeleny damske damska damsky panske panska pansky unisex detske '
            'velikost barva size black white blue grey gray red green navy pink').split())
SSTOP = STOP | {'skladem', 'eur', 'velikosti', 'variant', 'varianta'}  # slova z variant a velikostí do vyhledávání nepatří

def norm(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def slug(s): return norm(s).replace(' ', '-')[:40]

def common_prefix(xs):
    if not xs: return ''
    a, b = min(xs), max(xs); n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]: n += 1
    p = a[:n]
    return p[:p.rfind('/') + 1] if '/' in p else ''

def bucket(s):
    """Stejný výpočet má appka (sup-data.js): h = h * 31 + kód znaku, 32 bitů, % 256."""
    h = 0
    for ch in str(s): h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % 256

def skey(t, split):
    """Do kterého vyhledávacího souboru patří slovo t (appka počítá stejně)."""
    k = t[:3]
    while k in split and len(t) > len(k): k = t[:len(k) + 1]
    return k + '_' if k in split else k

def pages(base, rows):
    for k in range(0, len(rows), CH): dump(f'{base}/{k // CH}.json', rows[k:k + CH])

def bands(base, rows):
    """Jen u indexů s víc stránkami: nejlepší kusy pod cenovým stropem (jedna stránka), ať hlídač „do 300 Kč“ neprochází celou kategorii."""
    if len(rows) <= CH: return
    for b in BANDS:
        rs = [r for r in rows if r[4] <= b and not r[7]][:CH]
        if rs: dump(f'{base}+{b}/0.json', rs)

def write_index(C, meta, out):
    """p/ plné stránky, c/ indexy po stránkách, i/ mapa id → stránka, s/ vyhledávání. Vrací (bycat, where)."""
    prio = lambda r: (0 if r.get('x') else 1, 0 if r.get('lp') else 1, r['d'] * 1000 + r['t'])
    bycat, where = {}, {}
    for r in C: bycat.setdefault(r['c'], []).append(r)
    for c in bycat:
        items = sorted(bycat[c], key=prio, reverse=True); bycat[c] = items
        for n in range(0, len(items), PAGE):
            dump(f'{out}/p/{c}-{n // PAGE}.json', dict(meta, items=items[n:n + PAGE]))
        for j, r in enumerate(items): where[r['i']] = j // PAGE
        rows = [[r['i'], r['b'], r['n'], r.get('v') or '', r['p'], r['d'], r['t'], r.get('x', 0), r['s'], r['sb'], r.get('ac', 0), j // PAGE, r.get('gd', '')] for j, r in enumerate(items)]
        pages(f'{out}/c/{c}', rows)
        bands(f'{out}/c/{c}', rows)
        # menší indexy: podle podkategorie a podle značky (hlídač „Oblečení · Mikiny“ nebo „Nike“ stáhne jen svůj kousek)
        bysub, bybrand = {}, {}
        for row in rows:
            if row[9]: bysub.setdefault(row[9], []).append(row)
            if row[1]: bybrand.setdefault(slug(row[1]), []).append(row)
        for sb, rs in bysub.items(): pages(f'{out}/c/{c}~{sb}', rs); bands(f'{out}/c/{c}~{sb}', rs)
        for bs, rs in bybrand.items(): pages(f'{out}/c/{c}@{bs}', rs)
        names = {}
        for row in rows:
            if row[1] and not row[10]: names.setdefault(slug(row[1]), [row[1], 0])[1] += 1
        dump(f'{out}/c/{c}~brands.json', sorted(names.values(), key=lambda x: -x[1]))
        ix = {}
        for row in rows: ix.setdefault(bucket(row[0]), {})[row[0]] = row[11]
        for b, m in ix.items(): dump(f'{out}/i/{c}/{b}.json', m)

    # vyhledávání: soubor podle začátku slova; přeplněné soubory se dělí o písmeno dál (nik → nike → nike_ pro přesné slovo)
    light, tok = [], {}
    for r in C:
        light.append([r['i'], r['c'], where[r['i']], r['b'], r['n'], '', r['p'], r['d'], r['s'], r['t'], r.get('x', 0), r['sb'], r.get('ac', 0), r.get('gd', '')])
        for t in {t for t in norm(r['b'] + ' ' + r['n']).split() if len(t) >= 3 and t not in SSTOP}:
            tok.setdefault(t, []).append(len(light) - 1)
    split = set()
    while True:
        sizes = Counter()
        for t, ix in tok.items(): sizes[skey(t, split)] += len(ix)  # horní odhad (řádek se může opakovat přes slova)
        grow = {k for k, n in sizes.items() if n > SPLIT and not k.endswith('_') and len(k) < 12}
        if not grow: break
        split |= grow
    files = {}
    for t, ix in tok.items(): files.setdefault(skey(t, split), set()).update(ix)
    counts, multi = {}, {}
    for k, ix in files.items():
        rs = sorted((light[i] for i in ix), key=lambda r: -r[9])
        counts[k] = len(rs)
        # přeplněný soubor (jen přesné slovo jako nike_ nebo nejdelší klíč): stránky podle skóre + zvlášť nejlevnější kusy
        dump(f'{out}/s/{k}.json', rs[:SPLIT])
        if len(rs) > SPLIT:
            multi[k] = -(-len(rs) // SPLIT)
            for j in range(1, multi[k]): dump(f'{out}/s/{k}~{j}.json', rs[j * SPLIT:(j + 1) * SPLIT])
            dump(f'{out}/s/{k}~c.json', sorted((r for r in rs if not r[10]), key=lambda r: r[6])[:SPLIT])
    dump(f'{out}/s/_n.json', dict(counts, **{'*': sorted(split), '#': multi}))
    return bycat, where

def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))

def main():
    d = json.load(open(SRC, encoding='utf-8'))
    deals = [x for x in d['deals'] if x.get('img') and x.get('stock', True)]
    aid, bids, dest, ibid = None, {}, {}, {}
    for x in deals:  # partnerský odkaz eHUB rozložíme na obchod + cílovou adresu, appka ho složí zpět
        u = x.get('url') or ''
        m = re.match(re.escape(CLICK) + r'([^&]+)&a_bid=([^&]+)&desturl=(.*)$', u)
        if m and (aid is None or m.group(1) == aid):
            aid = m.group(1); b = m.group(2)
            bids.setdefault(x['shop'], b)
            if b != bids[x['shop']]: ibid[x['id']] = b
            dest[x['id']] = unquote(m.group(3))
    pre = {}
    for s in sorted({x['shop'] for x in deals}):
        xs = [x for x in deals if x['shop'] == s][:400]
        pre[s] = [common_prefix([dest.get(x['id'], x['url']) for x in xs]), common_prefix([x['img'] for x in xs])]
    kinds = {}
    for c in {x['cat'] for x in deals}:
        kinds[c] = Counter(x.get('kind') for x in deals if x['cat'] == c).most_common(1)[0][0]

    def comp(x):
        s = x['shop']; pu, pg = pre[s]
        u = dest.get(x['id'], x['url']); g = x['img']
        r = {'i': x['id'], 'c': x['cat'], 'sb': x.get('sub') or '', 'b': x.get('brand') or '', 'n': x['name'], 's': s,
             'u': u[len(pu):] if pu and u.startswith(pu) else '~' + u, 'g': g[len(pg):] if pg and g.startswith(pg) else '~' + g,
             'p': x['now'], 'd': x.get('disc') or 0, 't': (x.get('score') or {}).get('total', 0)}
        if x.get('kind') != kinds[x['cat']]: r['k'] = x.get('kind')
        if (x.get('hype') or 5) != 5: r['h'] = x.get('hype')
        if x['id'] not in dest: r['e'] = 1 if x.get('aff') else 0
        if x['id'] in ibid: r['kb'] = ibid[x['id']]
        if x.get('was') not in (None, x['now']): r['w'] = x['was']
        if x.get('variant'): r['v'] = x['variant']
        if x.get('basis') not in (None, 'historie'): r['bs'] = x['basis']
        if x.get('lowDays'):
            r['ld'] = x['lowDays']
            if x.get('low') not in (None, x['now']): r['l'] = x['low']
        if x.get('prev') not in (None, ''): r['pv'] = x['prev']
        hs = x.get('hist') or []
        if len(hs) > 1: r['hs'] = [v for pt in hs for v in pt]  # graf ceny: [dní zpět, cena, dní zpět, cena…], jen změny
        if x.get('days'): r['dy'] = x['days']
        if x.get('reason'): r['r'] = x['reason']
        if not x.get('pass'): r['x'] = 1
        if x.get('acc'): r['ac'] = 1
        if x.get('gender'): r['gd'] = x['gender']
        if x.get('cond'): r['cn'] = x['cond']  # 'z' = zánovní
        if (x.get('offers') or 1) > 1: r['o'] = x['offers']  # stejný výrobek ve více obchodech
        if x.get('next'): r['nx'] = x['next']  # [obchod, cena] druhé nejlepší nabídky
        if x.get('lp'): r['lp'] = 1  # obchod s nižší prioritou (např. fan merch) – v přehledech až za ostatními
        return r

    C = [comp(x) for x in deals]
    meta = {'v': 2, 'batch': d.get('batch'), 'checkedAt': d.get('checkedAt'), 'aid': aid, 'bids': bids, 'pre': pre, 'kinds': kinds}
    shutil.rmtree(OUT, ignore_errors=True)
    meta['layout'], meta['ch'], meta['bands'] = LAYOUT, CH, list(BANDS)
    bycat, where = write_index(C, meta, OUT)

    pick, seen = [], set()
    def add(rs):
        for r in rs:
            if r['i'] not in seen: seen.add(r['i']); pick.append(r)
    subc = {}
    for c, items in bycat.items():
        add([r for r in items if not r.get('ac')][:PER_CAT])
        bysub = {}
        for r in items: bysub.setdefault(r['sb'], []).append(r)
        for sb, rs in bysub.items():
            if sb: add(rs[:PER_SUB])
        subc[c] = {sb: len(rs) for sb, rs in bysub.items() if sb}
        # u módy i nejlepší kusy pro kluky a pro holky (hlídač „Mikiny · holky“ se pak ukáže hned)
        if any(r.get('gd') for r in items):
            for g in ('m', 'f'):
                gi = [r for r in items if r.get('gd', '') in (g, 'u', '') and not r.get('ac')]
                add(gi[:PER_CAT])
                for sb in bysub:
                    if sb: add([r for r in gi if r['sb'] == sb][:PER_SUB])
    add(sorted([r for r in C if r.get('h', 5) >= 8 and not r.get('x') and not r.get('ac')], key=lambda r: (0 if r.get('lp') else 1, r['t']), reverse=True)[:TREND])
    top_ids = d.get('top') or []
    add([r for r in C if r['i'] in set(top_ids)])
    byid = {x['id']: x for x in deals}
    K = ['id', 'cat', 'sub', 'kind', 'brand', 'name', 'variant', 'shop', 'url', 'now', 'was', 'disc', 'basis', 'hype', 'img', 'imgby', 'checked']
    top_deals = []
    for i in top_ids:
        x = byid.get(i)
        if x:
            o = {k: x[k] for k in K if x.get(k) not in (None, '')}
            o.update(score={'total': (x.get('score') or {}).get('total', 0)}, stock=True, **{'pass': bool(x.get('pass'))}); top_deals.append(o)
    dump(f'{OUT}/drops.json', dict(meta, prevAt=d.get('prevAt'), affiliate=d.get('affiliate'), shops=d.get('shops'), note=d.get('note'),
                                    top=top_ids, topDeals=top_deals, counts={c: len([r for r in v if not r.get('ac')]) for c, v in bycat.items()}, subCounts=subc, total=len(C), items=pick))

    os.remove(SRC)  # plný soubor nezveřejňujeme, stačí rozdělené části
    kb = lambda p: os.path.getsize(p) // 1024
    total = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(OUT) for f in fs) // 1024
    print(f'v2: drops {kb(OUT + "/drops.json")} kB ({len(pick)} nabídek), {len(bycat)} kategorií, {len(os.listdir(OUT + "/s"))} vyhledávacích souborů, celkem {total} kB')

if __name__ == '__main__':
    main()
