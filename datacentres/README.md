# datacentres/

UK data-centre sites and pipeline entries, from fully open sources only (GridAtlas
issue #73, steps 2–4). The method, the schema and the open-data rule are described in
[`docs/METHODS.md`](../docs/METHODS.md), and the licences in [`NOTICE`](NOTICE).

- IT load MW and grid connection MW are separate fields and are never merged.
- Unknown is `null`, not zero.
- A pipeline entry is not a commitment.
- Proximity is not connection, capacity or feasibility.

```bash
python pipelines/fetch_assets.py list datacentres
python pipelines/fetch_assets.py fetch datacentres && python pipelines/fetch_assets.py normalise datacentres
python ci/verify.py --domain datacentres
```
