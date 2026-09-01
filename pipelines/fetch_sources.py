"""Fetch the public sources this repository derives from, and pin them.

WHY THIS REPOSITORY EXISTS
--------------------------
The estate already owns prices (Ventusltd/data-gb-electricity). It did not
own the NETWORK, and the consequence showed up on the map: the Atlas drew
projects against OpenStreetMap substations, which know a name and a
location and nothing else. They do not know a circuit's impedance, a
node's fault level, a substation's seasonal rating, or which circuits the
system operator has already published a plan to change. Every one of those
is public, and NESO publishes them.

So this repository owns GB network data: it fetches the published sources,
pins them by SHA-256, derives clean products, and verifies them. Consumers
- the Atlas, Pipeline News - read the products and never the sources, the
same rule that keeps the price repository honest.

WHAT IT FETCHES
---------------
NESO's Electricity Ten Year Statement appendices, published annually:

  Appendix B  system technical data - the node/branch model itself:
              substation code indexes for all four transmission owners,
              circuits with R, X, B on a 100 MVA base and seasonal
              ratings, transformers, reactive compensation, and the
              CHANGES the operator plans out to 2033/34
  Appendix D  fault levels, peak and minimum, by node and demand year
  Appendix A  system schematics (reference only, not parsed)
  Appendix C  power flow diagrams (reference only, not parsed)

and the substation geometry published by the Atlas release, which is the
only place the estate holds coordinates for named substations. ETYS names
sites; it does not locate them. Joining the two is what makes the model
drawable, and the join is reported honestly rather than assumed.

    python pipelines/fetch_sources.py
"""

import hashlib
import io
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SOURCES = os.path.join(REPO, "sources")

# NESO document ids, read from the ETYS documents and appendices index.
# Pinned deliberately: an id is stable, a "latest" link is not, and a
# product whose inputs can silently change is not a product.
ETYS_EDITION = "2025"
ETYS_DOCUMENTS = {
    "appendix-b-system-technical-data": (383936, "xlsx"),
    "appendix-d-fault-levels-peak": (383951, "xlsx"),
    "appendix-d-fault-levels-minimum": (383961, "xlsx"),
}
# Kept for reference and provenance; large, and not parsed by any build.
ETYS_REFERENCE = {
    "appendix-a-system-schematics": (383931, "pdf"),
    "appendix-c-power-flow-diagrams": (383946, "pdf"),
}

SUBSTATION_GEOMETRY = (
    "https://ventusltd.github.io/gridatlas/atlas/releases/"
    "202608300453-atlas-v9/data/grid_substations.geojson"
)

UA = {"User-Agent": "Ventus data-grid-gb (public data fetch)"}


def get(url):
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(payload)
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def main(include_reference=False):
    manifest = {
        "schema": "data-grid-gb.sources.v1",
        "etys_edition": ETYS_EDITION,
        "why_pinned": (
            "document ids are stable and 'latest' links are not; a product "
            "whose inputs can change without notice is not a product"),
        "sources": {},
    }

    wanted = dict(ETYS_DOCUMENTS)
    if include_reference:
        wanted.update(ETYS_REFERENCE)

    for name, (document_id, extension) in sorted(wanted.items()):
        url = f"https://www.neso.energy/document/{document_id}/download"
        payload = get(url)
        record = write(os.path.join(SOURCES, f"etys-{ETYS_EDITION}-{name}.{extension}"), payload)
        record.update({"url": url, "document_id": document_id,
                       "publisher": "NESO", "extension": extension})
        manifest["sources"][name] = record
        print(f"  {name:<38} {record['bytes']:>9,} bytes  {record['sha256'][:16]}")

    payload = get(SUBSTATION_GEOMETRY)
    record = write(os.path.join(SOURCES, "grid_substations.geojson"), payload)
    record.update({
        "url": SUBSTATION_GEOMETRY,
        "publisher": "OpenStreetMap contributors, via the GridAtlas release",
        "note": ("ETYS names substations and does not locate them; this is "
                 "the only geometry the estate holds for named substations"),
    })
    manifest["sources"]["substation-geometry"] = record
    print(f"  {'substation-geometry':<38} {record['bytes']:>9,} bytes  {record['sha256'][:16]}")

    out = os.path.join(SOURCES, "sources-manifest.json")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"\nwrote sources/sources-manifest.json ({len(manifest['sources'])} sources)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--with-reference" in sys.argv))
