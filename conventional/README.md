# conventional/

UK conventional generation, from fully open sources only (GridAtlas issue #73,
step 5). It covers gas, coal, oil, nuclear, biomass, energy from waste and diesel.
Renewables are covered by REPD and are excluded here.

The method is described in [`docs/METHODS.md`](../docs/METHODS.md), and the licences
in [`NOTICE`](NOTICE). Each `capacity_mw` states its basis, for example DUKES 5.11
installed capacity. Unknown capacity is `null`, not zero.

```bash
python pipelines/fetch_assets.py list conventional
python ci/verify.py --domain conventional
```
