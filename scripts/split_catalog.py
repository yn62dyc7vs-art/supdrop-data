"""Rozdělí out/full.json (z build_catalog.py) na malé soubory, aby web a appka startovaly okamžitě.

out/v2/drops.json          start appky a webu: nejlepší nabídky z každé kategorie + top 12 (~40 kB po kompresi)
out/v2/c/<kat>.json        lehký index celé kategorie (filtry značek, cen, podkategorií)
out/v2/p/<kat>-<n>.json    plné záznamy po 100 kusech ve stejném pořadí jako index
out/v2/s/<abc>.json        vyhledávání podle prvních 3 písmen slova (lehké záznamy + číslo stránky)
out/v2/s/_n.json           počet produktů v každém vyhledávacím souboru (appka si vybere ten nejmenší)

Plný záznam: i id, c kategorie, sb podkategorie, ac příslušenství, k druh, b značka, n název, s obchod, u odkaz, g obrázek, p cena, d sleva,
h hype, t skóre, e hotový partnerský odkaz, kb jiný kód partnera, w původní cena, v varianta, bs základ slevy, l nejnižší cena,
ld počet dní historie, pv předchozí cena, dy dny ve slevě, r důvod, x neprošlo kontrolou.
"""
import json, os, re, shutil, unicodedata
from collections import Counter
from urllib.parse import unquote

SRC, OUT = 'out/full.json', 'out/v2'
CLICK = 'https://ehub.cz/system/scripts/click.php?a_aid='
PAGE, PER_CAT, PER_SUB, TREND = 100, 30, 8, 40
STOP = set(('pro na s se v a the of and in for with by do od k ke z ze bez cerna cerny cerne bila bily bile modra modry '
            'seda sedy siva cervena cerveny zelena zeleny damske damska damsky panske panska pansky unisex detske '
            'velikost barva size black white blue grey gray red green navy pink').split())

def norm(s):
    s = unicodedata.normalize('NFD', str(s or '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()

def common_prefix(xs):
    if not xs: return ''
    a, b = min(xs), max(xs); n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]: n += 1
    p = a[:n]
    return p[:p.rfind('/') + 1] if '/' in p else ''

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
        if x.get('days'): r['dy'] = x['days']
        if x.get('reason'): r['r'] = x['reason']
        if not x.get('pass'): r['x'] = 1
        if x.get('acc'): r['ac'] = 1
        return r

    C = [comp(x) for x in deals]
    meta = {'v': 2, 'batch': d.get('batch'), 'checkedAt': d.get('checkedAt'), 'aid': aid, 'bids': bids, 'pre': pre, 'kinds': kinds}
    shutil.rmtree(OUT, ignore_errors=True)

    prio = lambda r: (0 if r.get('x') else 1, r['d'] * 1000 + r['t'])
    bycat, where = {}, {}
    for r in C: bycat.setdefault(r['c'], []).append(r)
    for c in bycat:
        items = sorted(bycat[c], key=prio, reverse=True); bycat[c] = items
        for n in range(0, len(items), PAGE):
            dump(f'{OUT}/p/{c}-{n // PAGE}.json', dict(meta, items=items[n:n + PAGE]))
        for j, r in enumerate(items): where[r['i']] = j // PAGE
        dump(f'{OUT}/c/{c}.json', [[r['i'], r['b'], r['n'], r.get('v') or '', r['p'], r['d'], r['t'], r.get('x', 0), r['s'], r['sb'], r.get('ac', 0)] for r in items])

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
    add(sorted([r for r in C if r.get('h', 5) >= 8 and not r.get('x') and not r.get('ac')], key=lambda r: r['t'], reverse=True)[:TREND])
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

    sh = {}
    for r in C:
        toks = {t for t in norm(r['b'] + ' ' + r['n'] + ' ' + str(r.get('v') or '')).split() if len(t) >= 3 and t not in STOP}
        light = [r['i'], r['c'], where[r['i']], r['b'], r['n'], r.get('v') or '', r['p'], r['d'], r['s'], r['t'], r.get('x', 0), r['sb'], r.get('ac', 0)]
        for p in {t[:3] for t in toks}: sh.setdefault(p, []).append(light)
    for p, items in sh.items(): dump(f'{OUT}/s/{p}.json', items)
    dump(f'{OUT}/s/_n.json', {p: len(v) for p, v in sh.items()})

    os.remove(SRC)  # plný soubor nezveřejňujeme, stačí rozdělené části
    kb = lambda p: os.path.getsize(p) // 1024
    total = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(OUT) for f in fs) // 1024
    print(f'v2: drops {kb(OUT + "/drops.json")} kB ({len(pick)} nabídek), {len(bycat)} kategorií, {len(sh)} vyhledávacích souborů, celkem {total} kB')

if __name__ == '__main__':
    main()
