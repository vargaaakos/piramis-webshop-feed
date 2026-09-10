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
    if not modositva:
        print("  FIGYELEM: a feed nem küldött Last-Modified fejlécet")
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


# Egy termékcsoport (a g:product_type utolsó szintje) kiegészítő-e. A kirakatba
# főterméket akarunk: szigetelőlapot, nem hajlaterősítő szalagot; csempét, nem élvédőt.
CSOPORT_KIEGESZITO = re.compile(
    r"kieg[eé]sz[ií]t|tartoz[eé]k|ragaszt|egy[eé]b|profil|szersz|alapoz|szeg[oő]l[eé]c",
    re.I,
)

# Néhány kiegészítő a webshopban fő csoport alá van sorolva (a „Kenhető vízszigetelés"
# alatt ott a hajlaterősítő szalag is). A NEVÜKBEN viszont látszik. Ezeket nem dobjuk
# ki, csak a saját csoportjukon belül hátrasoroljuk.
NEV_KIEGESZITO = re.compile(
    r"(szalag|cs[ií]k|t[aá]vtart[oó]|liszt|sapka|csavar|d[uü]bel|kupak|rozetta"
    r"|adapter|sarokelem|v[eé]gelem|be[uü]t[oő][eé]k)",
    re.I,
)


def csoportnev(t):
    """A termék legmélyebb webshop-csoportja. Ez az, ami tényleg megkülönbözteti
    az üveggyapotot a kőzetgyapottól — a márka nem."""
    reszek = [x.strip() for x in t["utvonal"].split(">")]
    if len(reszek) > 2:
        return reszek[-1]
    return reszek[1] if len(reszek) > 1 else ""


def csalad(t):
    """Egy termékcsalád kulcsa, hogy ne kerüljön ki ugyanaz négy méretben.

    Elsődlegesen a g:item_group_id. Sok tétel viszont nem kap ilyet, és a feed külön
    termékként viszi ugyanazt a cikket más színkóddal (a laminált szegőlécből így jött
    ki egyszerre öt). Tartalék kulcs: márka + a név első négy szava."""
    if t["csoport"]:
        return "g:" + t["csoport"]
    nev = re.sub(r"[^0-9a-zá-űA-ZÁ-Ű ]", " ", t["nev"].lower())
    return "n:" + t["marka"].lower() + "|" + " ".join(nev.split()[:4])


def _korbe(csoportok, kell, mar_kint):
    """Körbeforgó szedegetés a csoportokon: minden csoportból egy, aztán a második.

    A körön belül az a márka jön, amelyik eddig a legkevésbé szerepelt — így a kirakat
    márkában is sokszínű marad, de a SORREND alapja a termékcsoport, nem a márka."""
    ki = []
    kor = 0
    rend = sorted(csoportok, key=lambda kv: (-len(kv[1]), kv[0]))
    markak = collections.Counter(t["marka"] for t in mar_kint)
    while len(ki) < kell:
        hozzaadott = False
        for _nev, lista_ in rend:
            if kor >= len(lista_):
                continue
            jelolt = sorted(lista_[kor:kor + 4],
                            key=lambda t: (markak[t["marka"]], t["nev"].lower()))[0]
            lista_.remove(jelolt)
            lista_.insert(kor, jelolt)
            ki.append(lista_[kor])
            markak[lista_[kor]["marka"]] += 1
            hozzaadott = True
            if len(ki) >= kell:
                break
        if not hozzaadott:
            break
        kor += 1
    return ki


def valogat(termekek, darab):
    """Melyik termék kerüljön ki a kirakatba.

    2026-09-10-ig márkánként forgattunk körbe. Ákos szúrta ki, mi a baj vele: a Mapei,
    a Salag és a Viarprofil ugyanúgy kapott egy helyet az első körben, mint az Isover
    vagy a Knauf — pedig azok kiegészítő-márkák. A Szigetelésnél a 12 csempéből csak 4
    volt valódi szigetelőanyag, a Csempénél 5 volt élvédő és impregnálószer.

    Ezért most a TERMÉKCSOPORT a rendezőelv, két menetben:
      1. Csak raktáron lévő, képpel rendelkező termék, termékcsaládonként egy.
      2. Először a fő csoportokból töltünk körbeforgóan (üveggyapot, kőzetgyapot,
         polisztirol …), a márka csak a körön belüli döntőbíró.
      3. Kiegészítő csoport csak akkor kerül be, ha főtermékből nincs elég — a
         laminált padlónál például tényleg csak két padló van raktáron.

    A sorrend determinisztikus: ami tegnap kint volt és ma is raktáron van, marad."""
    jok = [t for t in termekek if t["kep"] and t["keszlet"] == "in_stock"]

    latott = set()
    egyedi = []
    for t in sorted(jok, key=lambda t: (bool(NEV_KIEGESZITO.search(t["nev"])),
                                        t["akcios_ar"] == "",
                                        t["nev"].lower())):
        kulcs = csalad(t)
        if kulcs in latott:
            continue
        latott.add(kulcs)
        egyedi.append(t)

    csoportok = collections.defaultdict(list)
    for t in egyedi:
        csoportok[csoportnev(t)].append(t)

    fo = [(k, v[:]) for k, v in csoportok.items() if not CSOPORT_KIEGESZITO.search(k)]
    ki = _korbe(fo, darab, [])
    if len(ki) < darab:
        kieg = [(k, v[:]) for k, v in csoportok.items() if CSOPORT_KIEGESZITO.search(k)]
        ki += _korbe(kieg, darab - len(ki), ki)
    return ki[:darab]


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
                    # Szándékosan NINCS itt generálási időbélyeg. Ha lenne, minden napi
                    # futás megváltoztatná mind a 47 fájlt, és a "csak akkor commitolj,
                    # ha változott" feltétel sosem teljesülne. A futás ideje a git
                    # commit dátumában amúgy is benne van.
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
        json.dumps({"feed_frissitve": modositva,
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
