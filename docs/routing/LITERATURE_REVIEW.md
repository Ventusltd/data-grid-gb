# Civil evidence for indicative electricity-cable corridors

Status: research basis, 2026-09-03. This review does not activate a dataset or
authorise a product claim. Source integrity and intended use are recorded in
[`../../sources/routing-sources-manifest.json`](../../sources/routing-sources-manifest.json).

## Finding

GridAtlas can credibly add a route-screening feature, but a road satnav is the
wrong engineering model. Real transmission cable routes combine open-cut
sections across suitable land, short or long trenchless crossings, access and
jointing constraints, and negotiated avoidance or mitigation of environmental
and infrastructure constraints. Roads, railways, rivers, flood defences and
other utilities are not uniformly barriers and are not uniformly corridors.

The honest output is therefore a set of **indicative screening corridors** with
separate measurements and uncertainty, not a single "best cable route". The
existing geodesic straight line must remain visible as a useful lower-bound
baseline.

## Definitive GB engineering evidence

### 1. National Grid: technical issues for underground HVAC transmission

[Undergrounding high voltage electricity transmission lines: the technical
issues](https://www.nationalgrid.com/sites/default/files/documents/45349-Undergrounding_high_voltage_electricity_transmission_lines_The_technical_issues_INT.pdf)
is the general engineering reference. Its pp. 5, 8-9 and 11-12 explain that
route, terrain, capacity and installation conditions drive design and cost;
joint bays are commonly required at intervals of roughly 500-1,000 m; a typical
trench is about 1.5 m wide and 1.2 m deep; and a 400 kV double-circuit
construction swathe can be about 40-65 m. It also describes direct burial as
normally the lowest-cost approach where land constraints allow it.

**Engine consequence:** use off-road land as part of the search space. Add
joint/access burden and construction width as metrics. Do not force a cable to
follow a road simply because the road graph is easy to obtain.

### 2. National Grid: River Ouse installation options

[River Ouse - Possible Installation Methods for Underground
Cables](https://www.nationalgrid.com/document/348826/download) (June 2023),
Appendix A pp. A1-A4, evaluates a real 400 kV double-circuit river crossing.
It explains why trenchless methods are used at watercourses, roads, railways,
flood defences and utilities; considers HDD, microtunnelling/pipejacking and
conventional tunnelling; and identifies heat dissipation, physical protection,
access, ground investigation, bore stability and drilling-fluid release as
design issues. Preliminary rating studies indicated that eighteen transmission
cables were likely to be required.

**Engine consequence:** a river intersection is a typed crossing event, not an
infinite wall or a generic distance penalty. The engine may screen whether a
trenchless connector could be considered, but must not select a construction
method or claim feasibility without site investigation and engineering design.

### 3. National Grid: a developed 400 kV route at Glaslyn

[Pentir to Trawsfynydd Reinforcement Project, Environmental Statement Volume
4: Glaslyn Cables Works](https://www.nationalgrid.com/document/563931/download)
(September 2025), especially sections 2.3.35-2.3.46 and 2.4.7-2.4.16, is the
best initial GB benchmark. The approximately 6 km scheme is predominantly
through agricultural land, with roughly half installed by open-cut methods and
half by HDD. A curved HDD of about 850 m combines an A-road, railway and river
crossing. The scheme records eighteen ditch/watercourse crossings, a working
corridor around 65 m, open-cut formation up to 23 m, and HDD depth and spacing
requirements. A heritage-rail crossing is treated differently elsewhere in the
same scheme, demonstrating that feature class alone does not determine method.

**Engine consequence:** model compound crossings and allow one connector to
cross several obstacles. Retain crossing identity and proposed method as
separate fields. Use this project as a geometry-and-event regression fixture,
not as a universal parameter table.

### 4. DESNZ/Ramboll: current UK cost and construction study

The Department for Energy Security and Net Zero commissioned
[Undergrounding transmission cables: study of costs of innovative
methods](https://www.gov.uk/government/publications/undergrounding-transmission-cables-study-of-costs-of-innovative-methods)
(Ramboll, 2026) for 275 kV and 400 kV rural routes of 20 km and 50 km. Its
comparative rural cost scenarios use study-level average intervals of 5 km for
major crossings, 1.5 km for minor roads and 3 km for minor watercourses or
ditches. These inputs are not universal frequencies for factual map features.

**Engine consequence:** never turn those intervals into factual map features.
They are useful for sensitivity and missing-data tests only. Cost cannot be
derived from route length alone.

### 5. IET/Mott MacDonald: comparative transmission technology evidence

[A comparison of electricity transmission technologies: costs and
characteristics](https://www.theiet.org/impact-society/sustainability-and-climate-change/iet-electricity-transmission-technologies-report)
(IET with Mott MacDonald, 2025) compares overhead, underground and subsea
technologies on a common whole-life basis. It reports much higher average
life-cycle cost per MW-km for directly buried underground transmission than for
overhead line, while stressing sensitivity to capacity, terrain, soil, rock,
environmental constraints and supply-chain conditions.

**Engine consequence:** a geometrically shorter corridor is not necessarily
cheaper, easier, lower-impact or more likely to be consented. Until a separately
validated cost model exists, publish physical measurements and constraints,
not a currency estimate.

## Rules and permissions around crossings

- [Environment Agency FRA3](https://www.gov.uk/government/publications/environmental-permitting-regulations-exempt-flood-risk-activities/exempt-flood-risk-activities-environmental-permits#fra3-service-crossings-below-the-bed-of-a-main-river)
  gives conditions under which an England main-river service crossing may be
  exempt: the alignment is close to perpendicular to flow, burial is at least
  1.5 m below the bed and maintained for at least 5 m beyond both banks, and
  pits and sensitive structures have stated offsets. Failure to meet FRA3 means only that the
  exemption is unavailable; it does not prove that a permitted crossing is
  impossible. Wales and Scotland require their own rules and datasets.
- [Network Rail utilities and infrastructure
  requirements](https://property.networkrail.co.uk/land-and-station-opportunities/utilities-infrastructure/)
  require early Asset Protection involvement, an accepted design and
  installation method, property clearance and normally a legal agreement
  before a third-party cable crosses railway property. A railway is therefore
  a consent, engineering and schedule event, not a no-go line.
- [Eastern Green Link 5 Strategic Options
  Report](https://www.nationalgrid.com/document/558946/download), section 5.6.8
  and the option appraisals on pp. 98-108, uses high-level straight-line circuit
  lengths with a 20% route-deviation tolerance. Some offshore components use
  preliminary cable-routing studies; it does not present a preliminary routed
  onshore path as a comparison to the straight line. The appraisals distinguish
  avoidable constraints, unavoidable flood-zone exposure, and impacts that HDD
  may reduce. This supports graded alternatives rather than blanket exclusion.

## Computational research and reproducibility

[Versleijen et al., "An open-source benchmark of the state-of-the-art in
electrical cable routing"](https://doi.org/10.1007/s12667-026-00827-x)
compares least-cost routing with human reference routes across five real cases.
Its [reference implementation](https://github.com/alliander-opensource/utility-route-planner)
supports using a transparent least-cost model for early planning. The journal
article reports limitations around complex constraints; the repository README
separately lists desired support for alternatives, maximum length,
perpendicular crossings and infrastructure alignment, and says the code is not
for production use. [PYORPS](https://github.com/marhofmann/pyorps) implements
rasterisation and Dijkstra/A* backends. Using it as an independent test oracle
is a proposal here, not a sourced validation: pin a release/commit and qualify
its results independently before relying on it.

## Data consequences for GridAtlas

The retained v8 files are map-display geometry, not yet a defensible route
network. The current set covers motorways, trunk and primary roads, but not the
complete classified/unclassified road network; it has no river constraint
layer; and the retained feature properties omit routing facts such as direction,
bridge, tunnel, layer and road reference. The data-plane quarantine also marks
authority and licence unverified.

The primary road-network candidate should be [OS Open
Roads](https://www.ordnancesurvey.co.uk/products/os-open-roads): a GB-wide,
link-and-node network of classified and unclassified roads published under the
Open Government Licence and updated every six months. Its feature identifiers
are unique within a release but the technical specification says they are not
persistent between versions and no change history is supplied; pin every
release digest and maintain an explicit crosswalk rather than treating those
identifiers as durable. England main rivers can begin with the Environment
Agency's [Statutory Main River
Map](https://www.data.gov.uk/dataset/4ae8ba46-f9a4-47d0-8d93-0f93eb494540/statutory-main-river-map),
but ordinary watercourses and the separate Welsh and Scottish regimes remain
required. Railways also need owner, operational status, bridge/tunnel/layer and
crossing authority rather than a display-only centreline.

No source in this review proves land rights, a suitable cable system, thermal
rating, ground conditions, constructability, environmental acceptability,
planning consent, a grid connection, available capacity, queue position,
commitment, commercial terms or a connection date.
