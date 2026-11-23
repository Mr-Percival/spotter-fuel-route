"""Build data/us_places.csv from the Census Gazetteer files (public domain).

Usage:
    python scripts/build_places.py 2025_Gaz_place_national.txt 2025_Gaz_cousubs_national.txt

Source: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/
- "Places" (cities, towns, CDPs) is the primary source.
- "County subdivisions" adds townships and New England / New York towns,
  where many truck stops are listed (e.g. Mahwah, NJ; Canaan, NY).

Output columns: state, name, lat, lon, land_sqmi, source.
The legal suffix ("city", "town", "CDP", ...) is stripped once, so
"Oklahoma City city" -> "Oklahoma City". Consolidated governments such as
"Nashville-Davidson metropolitan government (balance)" also get an alias with
the first part ("Nashville").
"""

import csv
import re
import sys
from pathlib import Path

SUFFIX = re.compile(
    r"\s+(city \(balance\)|city and borough|(?:consolidated|metropolitan|unified|metro) government(?: \(balance\))?|"
    r"\(balance\)|urban county|city|town|village|borough|CDP|CCD|municipality|comunidad|"
    r"zona urbana|corporation|plantation|township|charter township|UT|gore|grant|location|"
    r"purchase|reservation|unorganized territory|precinct|district)$",
    re.IGNORECASE,
)


def names_for(raw: str) -> list[str]:
    name = SUFFIX.sub("", raw).strip()
    names = [name]
    if "-" in name:  # Nashville-Davidson, Augusta-Richmond County, Macon-Bibb County
        names.append(name.split("-")[0].strip())
    if name.endswith(" City") and name.count(" ") >= 1:  # "Boise City" is listed as "Boise"
        names.append(name[: -len(" City")])
    return names


def main(places_src: str, cousubs_src: str) -> None:
    out = Path(__file__).resolve().parent.parent / "data" / "us_places.csv"
    rows = 0
    with open(out, "w", newline="", encoding="utf-8") as g:
        writer = csv.writer(g)
        writer.writerow(["state", "name", "lat", "lon", "land_sqmi", "source"])
        for src, label in ((places_src, "place"), (cousubs_src, "cousub")):
            with open(src, encoding="utf-8") as f:
                for r in csv.DictReader(f, delimiter="|"):
                    r = {k.strip(): v.strip() for k, v in r.items()}
                    for name in names_for(r["NAME"]):
                        if not name:
                            continue
                        writer.writerow([r["USPS"], name, r["INTPTLAT"], r["INTPTLONG"],
                                         r["ALAND_SQMI"], label])
                        rows += 1
    print(f"wrote {rows} rows to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
