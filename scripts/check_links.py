#!/usr/bin/env python3
"""Denní kontrola odkazů a obrázků šupu (pravidlo „odkaz vždy otevře produkt“).

Každý den vezme od každého obchodu náhodný vzorek produktů z posledních zveřejněných dat (větev live, v2/p)
a ověří cílovou stránku produktu a obrázek:
  - stránka: 200 a stále stránka produktu → v pořádku; 404/410 nebo „stránka nenalezena“ → pryč;
    přesměrování na úvodní stránku obchodu → úvod; 401/403/429/5xx nebo přesměrování na cizí web → nelze ověřit
    (ochrana proti robotům, z GitHubu se často nedá projít – to obchod nevyřadí);
  - obrázek: 200 a opravdu obrázek → v pořádku; 404/410, HTML místo obrázku nebo zástupný obrázek („nenalezeno“) → špatný.
Partnerský odkaz (eHUB, Dognet, CJ) se NEotevírá – robot by dělal falešné prokliky. Kontroluje se adresa produktu,
kterou odkaz nese (desturl / url).

Obchod je špatný, když z ověřitelných vzorků (aspoň MIN_CHECKED) selže aspoň BAD_SHARE. Když je špatný dva dny po
sobě, dostane se do seznamu „auto_off“ a hodinový běh ho vynechá (v hlášení běhu je důvod). Ze seznamu ho dostane:
  - úspěšná kontrola stejných adres, na kterých selhal (opravený odkaz / feed), nebo
  - ruční odblokování: v config/shops.json "linkcheckOk": "<datum>" (rozhodnutí z dřívějška se ignorují),
    případně "linkcheck": false (obchod se nekontroluje vůbec, např. kvůli ochraně proti robotům).
Když vyjde špatně víc než MAX_BAD_SHOPS obchodů najednou, je chyba spíš u kontroly (síť runneru) a nikdo se nevyřadí.

Výstup: out/linkcheck.json (workflow „Kontrola odkazů“ ho uloží do větve kontrola) a čitelné shrnutí do logu.
"""
import json, os, random, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, date, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc

SAMPLE = 5             # produktů na obchod
MIN_CHECKED = 3        # aspoň tolik ověřitelných vzorků, jinak „nelze ověřit“
BAD_SHARE = 0.6        # podíl selhání, od kterého je obchod špatný
MAX_BAD_SHOPS = 0.3    # víc špatných obchodů najednou (podíl, aspoň 4) = podezřelá kontrola, nikoho nevyřazovat
DAYS_KEPT = 14
PAUSE = 1.0            # mezi dotazy na stejný obchod
UA = 'supdrop-linkcheck/1.0 (+https://supdrop.cz; info@supdrop.cz)'
SOFT404 = re.compile(r'(404|nenalezen|nebyla nalezena|neexistuje|not found|nenájden|produkt (?:již|uz|už) není|zboží (?:již|už) není)', re.I)
PLACEHOLDER = re.compile(r'(nenalezeno|no[-_]?image|noimage|placeholder|no[-_]?photo|default[-_]?product)', re.I)
TRACKERS = ('ehub.cz', 'go.dognet.com', 'tkqlhce.com', 'anrdoezrs.net', 'kqzyfj.com', 'dpbolvw.net', 'jdoqocy.com', 'prf.hn', 'awin1.com')
SLEEP = time.sleep

def say(*a): print(*a, flush=True)

def target(url):
    """Z partnerského odkazu vytáhne adresu produktu (i vnořenou, např. eHUB → prf.hn → obchod)."""
    for _ in range(4):
        p = urllib.parse.urlparse(url)
        host = p.netloc.lower()
        if not any(host == t or host.endswith('.' + t) for t in TRACKERS): return url
        q = urllib.parse.parse_qs(p.query)
        nxt = (q.get('desturl') or q.get('url') or q.get('ued') or q.get('p') or [None])[0]
        if not nxt:
            m = re.search(r'/destination:(.+)$', p.path + ('?' + p.query if p.query else ''))
            nxt = urllib.parse.unquote(m.group(1)) if m else None
        if not nxt or not nxt.startswith('http'): return None
        url = nxt
    return None

def site(host):
    """Zjednodušená „registrovaná doména“ (www.shop.cz → shop.cz) pro porovnání přesměrování."""
    parts = host.lower().split(':')[0].split('.')
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host

ROOTS = re.compile(r'^/(?:(?:cs|cz|sk|en|cs-cz|cz-cs)/?)?(?:index\.(?:php|html?))?$', re.I)
def is_home(orig, final):
    a, b = urllib.parse.urlparse(orig), urllib.parse.urlparse(final)
    return bool(ROOTS.match(b.path or '/')) and not ROOTS.match(a.path or '/')

def http_get(url, limit, headers=None, timeout=20):
    """GET s následováním přesměrování. Vrátí (kód, konečná adresa, typ obsahu, začátek těla)."""
    req = urllib.request.Request(url, headers=dict({'User-Agent': UA, 'Accept-Language': 'cs-CZ,cs;q=0.9'}, **(headers or {})))
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.geturl(), r.headers.get('Content-Type') or '', r.read(limit)
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, 'geturl') else url, (e.headers or {}).get('Content-Type') or '', b''

def check_page(url, get=http_get):
    try: code, final, ctype, body = get(url, 300000)
    except Exception as e: return 'chyba', type(e).__name__
    if code in (404, 410): return 'pryč', str(code)
    if code != 200: return 'nelze', str(code)
    if site(urllib.parse.urlparse(final).netloc) != site(urllib.parse.urlparse(url).netloc): return 'nelze', 'jiný web ' + urllib.parse.urlparse(final).netloc
    if is_home(url, final): return 'úvod', final
    m = re.search(rb'<title[^>]*>(.*?)</title>', body or b'', re.I | re.S)
    title = m.group(1).decode('utf-8', 'replace').strip() if m else ''
    if title and SOFT404.search(title): return 'pryč', 'titulek: ' + title[:60]
    return 'ok', ''

def check_img(url, get=http_get):
    if not url: return 'špatný', 'chybí'
    if PLACEHOLDER.search(urllib.parse.urlparse(url).path): return 'špatný', 'zástupný obrázek'
    try: code, final, ctype, body = get(url, 2048, {'Range': 'bytes=0-2047'})
    except Exception as e: return 'chyba', type(e).__name__
    if code in (404, 410): return 'špatný', str(code)
    if code not in (200, 206): return 'nelze', str(code)
    if 'html' in ctype.lower(): return 'špatný', 'HTML místo obrázku'
    if PLACEHOLDER.search(urllib.parse.urlparse(final).path): return 'špatný', 'zástupný obrázek'
    return 'ok', ''

def verdict(rows):
    """rows: [{'page': (stav, detail), 'img': (stav, detail)}] → (stav obchodu, důvod)"""
    pg = [r['page'][0] for r in rows]; im = [r['img'][0] for r in rows]
    pc = [s for s in pg if s in ('ok', 'pryč', 'úvod')]; ic = [s for s in im if s in ('ok', 'špatný')]
    pbad = sum(s != 'ok' for s in pc); ibad = sum(s != 'ok' for s in ic)
    why = []
    if len(pc) >= MIN_CHECKED and pbad / len(pc) >= BAD_SHARE:
        k = max(('pryč', 'úvod'), key=pg.count)
        why.append(f'{pbad} z {len(pc)} odkazů ' + ('vede na úvodní stránku' if k == 'úvod' else 'nefunguje (404 / nenalezeno)'))
    if len(ic) >= MIN_CHECKED and ibad / len(ic) >= BAD_SHARE:
        why.append(f'{ibad} z {len(ic)} obrázků nefunguje')
    if why: return 'špatný', '; '.join(why)
    if len(pc) < MIN_CHECKED and len(ic) < MIN_CHECKED: return 'nelze', 'web nejde z kontroly ověřit (ochrana proti robotům)'
    return 'ok', ''

def products_of(shop, prev_fn):
    recs, meta = prev_fn(shop)
    out = []
    for r in recs or []:
        try: out.append(bc.restore_prev(r, meta))
        except Exception: pass   # poškozený záznam nesmí shodit kontrolu
    return out

def run(shops, prev_fn, old, today, page_fn=check_page, img_fn=check_img, log=say):
    """Zkontroluje obchody a vrátí nový obsah linkcheck.json."""
    hist = {k: v for k, v in (old.get('history') or {}).items()}
    auto_off = dict(old.get('auto_off') or {})
    day = today.isoformat()
    res = {}
    for sh in shops:
        name = sh['shop']
        if sh.get('linkcheck') is False: auto_off.pop(name, None); continue
        if name in auto_off and str(sh.get('linkcheckOk') or '') >= auto_off[name].get('od', ''):
            log(f'{name}: ručně odblokováno (linkcheckOk), kontroluji znovu'); auto_off.pop(name)
        retry = (auto_off.get(name) or {}).get('urls')
        if retry:   # vyřazený obchod: znovu ty adresy, na kterých selhal
            sample = [{'url': u, 'img': g} for u, g in retry]
        else:
            prods = [p for p in products_of(name, prev_fn) if p.get('url')]
            if not prods: continue
            rnd = random.Random(day + name)
            sample = [{'url': target(p['url']) or '', 'img': p.get('img') or ''} for p in rnd.sample(prods, min(SAMPLE, len(prods)))]
        rows = []
        for s in sample:
            page = page_fn(s['url']) if s['url'] else ('nelze', 'odkaz bez adresy produktu')
            SLEEP(PAUSE)
            img = img_fn(s['img']); SLEEP(PAUSE)
            rows.append({'url': s['url'], 'img_url': s['img'], 'page': list(page), 'img': list(img)})
        st, why = verdict([{'page': r['page'], 'img': r['img']} for r in rows])
        res[name] = {'stav': st, 'proc': why, 'vzorky': rows}
        log(f'{name}: {st}' + (f' – {why}' if why else '') + ('  (kontrola vyřazeného)' if retry else ''))
    # rozhodnutí o vyřazení
    bad_now = [n for n, r in res.items() if r['stav'] == 'špatný' and n not in auto_off]
    checked = [n for n, r in res.items() if r['stav'] != 'nelze']
    suspicious = len(bad_now) > max(3, MAX_BAD_SHOPS * len(checked))
    if suspicious: log(f'Pozor: špatně vyšlo {len(bad_now)} z {len(checked)} obchodů – spíš chyba kontroly, nikoho nevyřazuji.')
    yesterday = (today - timedelta(days=1)).isoformat()
    byname = {sh['shop']: sh for sh in shops}
    for n, r in res.items():
        hist.setdefault(n, {})[day] = r['stav']
        if n in auto_off:
            if r['stav'] == 'ok': log(f'{n}: odkazy zase fungují, vracím do šupu'); auto_off.pop(n)
            continue
        ok_since = str(byname.get(n, {}).get('linkcheckOk') or '')   # po ručním odblokování se počítají jen nové dny
        if r['stav'] == 'špatný' and not suspicious and (hist.get(n) or {}).get(yesterday) == 'špatný' and yesterday > ok_since:
            auto_off[n] = {'od': day, 'proc': r['proc'],
                           'urls': [[x['url'], x['img_url']] for x in r['vzorky'] if x['page'][0] != 'ok' or x['img'][0] != 'ok'][:SAMPLE]}
            log(f'{n}: VYŘAZEN – {r["proc"]} (druhý den po sobě)')
    for n in list(hist):
        hist[n] = {d: s for d, s in hist[n].items() if d >= (today - timedelta(days=DAYS_KEPT)).isoformat()}
    return {'at': datetime.now(bc.TZ).isoformat(timespec='minutes'), 'day': day, 'auto_off': auto_off, 'history': hist, 'last': res}

def main():
    shops = bc.load('config/shops.json', [])
    shops = [s for s in shops if not s.get('off')]
    old = bc.load('out/linkcheck.json', {})
    today = datetime.now(bc.TZ).date()
    t0 = time.time()
    out = run(shops, bc.prev_from_git(), old, today)
    os.makedirs('out', exist_ok=True)
    with open('out/linkcheck.json', 'w', encoding='utf-8') as f: json.dump(out, f, ensure_ascii=False, indent=1)
    c = {}
    for r in out['last'].values(): c[r['stav']] = c.get(r['stav'], 0) + 1
    say(f'Hotovo za {time.time() - t0:.0f} s: ' + ', '.join(f'{k} {v}' for k, v in sorted(c.items())) +
        f'; vyřazené: {", ".join(out["auto_off"]) or "žádné"}')
    summ = os.environ.get('GITHUB_STEP_SUMMARY')
    if summ:
        with open(summ, 'a', encoding='utf-8') as f:
            f.write('## Kontrola odkazů ' + out['day'] + '\n\n| Obchod | Stav | Proč |\n|---|---|---|\n')
            for n, r in sorted(out['last'].items(), key=lambda x: (x[1]['stav'] == 'ok', x[0])):
                f.write(f'| {n} | {r["stav"]} | {r["proc"]} |\n')
            f.write('\nVyřazené: ' + (', '.join(f'{n} (od {v["od"]}: {v["proc"]})' for n, v in out['auto_off'].items()) or 'žádné') + '\n')

if __name__ == '__main__':
    main()
