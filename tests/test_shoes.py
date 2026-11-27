# boty zařazené obchodem do oblečení nebo sportu jdou do „Boty a tenisky“; lyžařské boty a věci k botám ne
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from build_catalog import shoe_fix
C = [(('obleceni', None, False), 'Pánské basketbalové boty Air Jordan 1 Mid DQ8426-102 vel.', 'tenisky'),
     (('sport', 'micove', False), 'Nike Basketbal Baskets one take 5 blanc Bílá', 'tenisky'),
     (('obleceni', None, False), 'Pánské pantofle Boston Bruins NHL Team Logo Staycation Slipper', 'tenisky'),
     (('sport', 'lyze', False), 'K2 Minaret 100 235 mm lyžařské boty', 'sport'),
     (('obleceni', 'mikiny', False), 'Nike Mikiny Baskets club fleece rouge uni Červená', 'obleceni'),
     (('obleceni', 'pradlo', False), 'Puma Ponožky SNEAKER PLAIN ECOM Pack de 6', 'obleceni'),
     (('sport', 'skate', False), 'Breezy Rollers - Splash White - boty s kolečkem Velikost boty:', 'sport')]
bad = [(n, shoe_fix(r, n)[0]) for r, n, want in C if shoe_fix(r, n)[0] != want]
assert not bad, bad
print('ok', len(C))
