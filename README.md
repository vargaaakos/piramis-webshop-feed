# Piramis — webshop-feed szeletelő

A `piramisepitohaz.hu` kategóriaoldalain a sárga blokk termékkártyáit tölti fel adattal.

## Mit csinál

Az `epitoanyag.hu` Google Shopping feedjéből (36 MB, ~24 000 termék) kategóriánként
egy-egy kis JSON-t készít. A `kategoria/` mappában 47 fájl van, egyenként ~5 KB.

```
epitoanyag.hu feed  →  tools/webshop-feed.py  →  kategoria/*.json  →  GitHub Pages  →  a weboldal
```

## Miért kell ez egyáltalán

- A feed **36 MB** — oldalanként letölteni képtelenség.
- A feed **nem küld CORS fejlécet**, tehát a böngésző közvetlenül el sem érné.
- Viszont naponta frissül, így a másolás csak akkor elfogadható, ha automatikus.

**Ez egy átmeneti megoldás.** A végleges az volna, ha az `epitoanyag.hu` adna egy
kategóriánkénti végpontot — akkor ez az egész tároló törölhető.

## Frissülés

A `.github/workflows/frissites.yml` naponta 03:30 UTC-kor lefut, újragenerálja a
fájlokat, és **csak akkor commitol, ha tényleg változott valami**. Kézzel is
indítható az Actions fülön.

Ha egy futás elhasal, a tegnapi fájlok kint maradnak — a weboldal nem törik el,
csak egy nappal régebbi adatot mutat.

## A fájlok címe

```
https://<felhasznalo>.github.io/piramis-webshop-feed/kategoria/<slug>.json
```

A `slug` a kategória ékezet nélküli neve, például `szigeteles`. A teljes lista és a
darabszámok a `kategoria/index.json`-ban.

## Hogyan válogat

1. Csak **raktáron lévő**, képpel rendelkező termék. A ~24 000-ből mindössze ~3 900 az.
2. Egy termékcsaládból egy darab (`g:item_group_id` alapján) — enélkül ugyanaz a
   termék jönne ki négy méretben.
3. **Márkák körbeforgóan** — enélkül az ábécésorrend levágná a lista végét.

A sorrend determinisztikus: ami tegnap kint volt és ma is raktáron van, az marad.

## Helyi futtatás

```bash
python tools/webshop-feed.py --cel kategoria
python tools/webshop-feed.py --forras mentett.xml --cel kategoria   # letöltés nélkül
python tools/webshop-feed.py --cel kategoria --darab 20             # több termék
```
