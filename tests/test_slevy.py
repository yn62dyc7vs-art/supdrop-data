"""Kontrola poctivých slev: python3 tests/test_slevy.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import build_catalog as bc

def run(days_prices, orig=None, vid='v1'):
    """days_prices = [(den, cena, varianta?), …] po kontrolách; vrátí (původní, sleva %)."""
    h = []
    for x in days_prices:
        d, p = x[0], x[1]; v = x[2] if len(x) > 2 else vid
        bc.h_add(h, d, p, v)
    today, now_p = days_prices[-1][0], days_prices[-1][1]
    was, basis = bc.fair_was(now_p, orig, h, today, days_prices[-1][2] if len(days_prices[-1]) > 2 else vid)
    return was, (round((1 - now_p / was) * 100) if was > now_p else 0), basis, h

ok = True
def check(name, got, want):
    global ok
    res = 'OK ' if got == want else 'CHYBA'
    if got != want: ok = False
    print(f'{res} {name}: {got} (čekáno {want})')

# 1) jednorázový výkyv ve feedu (iPhone 321 509 Kč) slevu nedělá
d = [(100 + i, 56449) for i in range(10)]; d.insert(5, (105, 321509))
check('jednorázový výkyv', run(d)[1], 0)
# 2) opravdové zlevnění po týdnu stabilní ceny
d = [(100 + i, 3898) for i in range(10)] + [(110, 2464)]
check('opravdové zlevnění', run(d)[1], 37)
# 3) přeskakování mezi variantami (256 GB / 1 TB) slevu nedělá
d = [(100 + i, 52990, '1tb') for i in range(10)] + [(110, 34990, '256')]
check('jiná varianta', run(d)[1], 0)
# 4) málo historie (3 dny) → bez slevy z historie
d = [(100, 2000), (101, 2000), (102, 2000), (103, 1500)]
check('krátká historie', run(d)[1], 0)
# 5) původní cena obchodu dává smysl → sleva obchodu
check('sleva obchodu', run([(100, 1516)], orig=1746)[1], 13)
# 6) nesmyslná původní cena (víc než 3×) → bez slevy
check('nesmysl ve feedu', run([(100, 56449)], orig=321509)[1], 0)
# 7) „věčná sleva“: 20 dní stejná cena, obchod pořád hlásí slevu → bez slevy
d = [(100 + i, 999) for i in range(20)]
check('věčná sleva', run(d, orig=1999)[1], 0)
# 8) víc kontrol za den se slučuje, historie zůstává malá
h = run([(100, 500), (100, 500), (101, 500), (102, 500), (103, 480), (104, 480)])[3]
check('sloučená historie', h, [[100, 102, 500, 500, 'v1'], [103, 104, 480, 480, 'v1']])
# 9) starý formát historie se převede
check('starý formát', bc.h_norm([[304, 56449.0, 321509.0]]), [[304, 304, 56449.0, 321509.0, '']])
# 10) výkyv během dne (cena chvíli vyšší) se nebere – rozhoduje denní minimum
d = [(100 + i, 1000) for i in range(8)] + [(108, 1400), (108, 1000), (109, 1000)]
check('výkyv během dne', run(d)[1], 0)
print('\nVŠE V POŘÁDKU' if ok else '\nNĚCO NESEDÍ'); sys.exit(0 if ok else 1)
