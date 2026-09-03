# Az epitoanyag.hu Google Shopping feedjéből kategóriánkénti JSON-szeleteket készít,
# amelyeket a kategóriaoldal sárga blokkja tölt be böngészőből.
#
# Miért kell egyáltalán:
#   - a feed 36 MB, oldalanként letölteni képtelenség
#   - a feed NEM küld CORS fejlécet, tehát a böngésző közvetlenül el sem érné
#   - viszont naponta frissül, így a másolás csak akkor elfogadható, ha automatikus
#
# Futtatás:
#   python tools/webshop-feed.py                 # letölt és feldolgoz
#   python tools/webshop-feed.py --forras f.xml  # helyi fájlból dolgozik
#
# Kimenet a --cel mappába:
#   index.json          — kategórialista + darabszámok + a feed frissülési ideje
#   <slug>.json         — kategóriánként legfeljebb --darab termék
#
# Ezt a scriptet ütemezve kell futtatni (naponta egyszer elég, a feed sem frissül sűrűbben),
# és a kimenetet olyan helyre tenni, ami CORS-szal szolgálja ki. Lásd docs/11-webshop-feed.md.

import argparse
import collections
import datetime
import html
import json
import pathlib
import re
import sys
import unicodedata
import urllib.request

FEED_URL = "https://export.epitoanyag.hu/product_google.xml"

# A feedben ezek a mezők érdekelnek. A kulcs a mi nevünk, az érték a feed g: mezője.
MEZOK = {
    "id": "id",
    "nev": "title",
    "kep": "image_link",
    "link": "link",
    "ar": "price",
    "akcios_ar": "sale_price",
    "marka": "brand",
    "keszlet": "availability",
    "utvonal": "product_type",
    "csoport": "item_group_id",
}


def slugify(szoveg):
    """Ékezet nélküli, kötőjeles azonosító — ennek egyeznie kell a Termékkategória slugjával."""
    s = unicodedata.normalize("NFKD", szoveg.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ő", "o").replace("ű", "u")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def betolt(forras):
    if forras:
        p = pathlib.Path(forras)
        print(f"Helyi fájl: {p} ({p.stat().st_size:,} bájt)")
        return p.read_text(encoding="utf-8", errors="replace"), None
    print(f"Letöltés: {FEED_URL}")
    with urllib.request.urlopen(FEED_URL, timeout=180) as r:
        modositva = r.headers.get("last-modified")
        nyers = r.read()
    print(f"  {len(nyers):,} bájt, a feed frissítve: {modositva}")
    return nyers.decode("utf-8", errors="replace"), modositva


def darabol(xml):
    """Az <item> blokkokból szótárakat gyárt. Regexszel, mert 36 MB-ot nem érdemes DOM-ba tölteni."""
    ki = []
    for blokk in re.findall(r"<item>(.*?)</item>", xml, re.S):
        rec = {}
        for nev, mezo in MEZOK.items():
            m = re.search(r"<g:%s>(.*?)</g:%s>" % (mezo, mezo), blokk, re.S)
            rec[nev] = html.unescape(m.group(1)).strip() if m else ""
        reszek = [x.strip() for x in rec["utvonal"].split(">")]
        rec["fokategoria"] = reszek[0] if reszek else ""
        rec["alkategoria"] = reszek[1] if len(reszek) > 1 else ""
        ki.append(rec)
    return ki


def valogat(termekek, darab):
    """Melyik termék kerüljön ki a kirakatba.

    Három szabály, ebben a sorrendben:
      1. Csak raktáron lévő, képpel rendelkező termék.
      2. Egy termékcsaládból egy darab. A feed minden méretváltozatot külön tételként
         visz (pl. ugyanaz a profil 6/12/16/20 cm-ben), ezeket a g:item_group_id fogja
         össze — enélkül a kirakat négyszer ugyanazt mutatná.
      3. Márkák körbeforgóan, hogy a kirakat sokszínű legyen. A nevesített márkák
         előrébb kerülnek, az "Egyéb" jellemzően kiegészítő.

    A sorrend determinisztikus, hogy a napi újrafuttatás ne kavarja meg ok nélkül
    a kirakatot: ami tegnap kint volt és ma is raktáron van, az marad."""
    jok = [t for t in termekek if t["kep"] and t["keszlet"] == "in_stock"]

    # Márkánként csoportosítunk, majd körbeforgóan szedegetünk: minden márkából egyet,
    # aztán a másodikat, és így tovább. Enélkül az ábécésorrend levágná a lista végét —
    # a Szigetelésnél például az Isover, Knauf, Rockwool és URSA sosem jutna ki.
    markak = collections.OrderedDict()
    latott = set()
    for t in sorted(jok, key=lambda t: (t["marka"].lower() in ("", "egyéb", "egyeb"),
                                        t["akcios_ar"] == "",
                                        t["nev"].lower())):
        kulcs = t["csoport"] or t["id"]
        if kulcs in latott:
            continue
        latott.add(kulcs)
        markak.setdefault(t["marka"], []).append(t)

    egyedi = []
    kor = 0
    while len(egyedi) < darab:
        hozzaadott = False
        for lista_ in markak.values():
            if kor < len(lista_):
                egyedi.append(lista_[kor])
                hozzaadott = True
                if len(egyedi) >= darab:
                    break
        if not hozzaadott:
            break
        kor += 1
    return egyedi[:darab]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forras", help="helyi XML fájl a letöltés helyett")
    ap.add_argument("--cel", default="feed-out", help="kimeneti mappa")
    ap.add_argument("--darab", type=int, default=12, help="kategóriánkénti termékszám")
    a = ap.parse_args()

    xml, modositva = betolt(a.forras)
    termekek = darabol(xml)
    print(f"Termék a feedben: {len(termekek):,}")
    if not termekek:
        print("HIBA: egyetlen terméket sem sikerült kiolvasni — változott a feed szerkezete?")
        return 1

    csoportok = collections.defaultdict(list)
    for t in termekek:
        if t["alkategoria"]:
            csoportok[(t["fokategoria"], t["alkategoria"])].append(t)

    cel = pathlib.Path(a.cel)
    cel.mkdir(parents=True, exist_ok=True)
    keszult = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    index = []
    ures = []
    for (fo, al), lista in sorted(csoportok.items()):
        slug = slugify(al)
        kivalasztott = valogat(lista, a.darab)
        if not kivalasztott:
            ures.append(al)
        (cel / f"{slug}.json").write_text(
            json.dumps(
                {
                    "kategoria": al,
                    "fokategoria": fo,
                    "slug": slug,
                    "keszult": keszult,
                    "feed_frissitve": modositva,
                    "osszes_termek": len(lista),
                    "raktaron": len([t for t in lista if t["keszlet"] == "in_stock"]),
                    "termekek": [
                        {k: t[k] for k in ("id", "nev", "kep", "link", "ar", "akcios_ar", "marka")}
                        for t in kivalasztott
                    ],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        index.append({"kategoria": al, "fokategoria": fo, "slug": slug,
                      "osszes": len(lista), "kirakva": len(kivalasztott)})

    (cel / "index.json").write_text(
        json.dumps({"keszult": keszult, "feed_frissitve": modositva,
                    "termek_osszesen": len(termekek), "kategoriak": index},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"Kiírva: {len(index)} kategória a(z) {cel}/ mappába")
    if ures:
        print(f"FIGYELEM — nincs raktáron lévő, képes termék ezekben: {', '.join(ures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
