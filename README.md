# 🗺️ Commercial Density Mapping — Streamlit in Snowflake

A governed location-intelligence app for **go-to-market targeting**: filter Southeast
Asia POI data by location and merchant category, read three live counts, and explore
merchant density on a **2D hexagonal heatmap at ~130 m resolution** — finer than a
subdistrict.

Built entirely on [Streamlit in Snowflake](https://www.snowflake.com/en/data-cloud/overview/streamlit-in-snowflake/):
the data never leaves the platform, and access is governed by Snowflake RBAC.

---

## ✨ Features

| Area | Capability |
|---|---|
| **Location filters** | Country → City → District → Sub-district, cascading with live counts |
| **Category filters** | 3-level taxonomy (L1 → L2 → L3) with search, select-all, and per-option counts |
| **Qualified sub-districts** | Listed as `Subdistrict · District` pairs — no cross-district name collisions (e.g. "Maphar" appears once, under its real parent) |
| **Three-metric summary** | Location filter count · Category filter count · Combined filter count |
| **Hexagonal heatmap** | Pydeck `HexagonLayer`, 2D flat hexagons, ~130 m radius (≈ H3 resolution 9), hover tooltip with per-hexagon POI count |
| **Compute gating** | No result queries run until **Compute** is pressed; stale results are flagged when filters change |
| **Safety net** | Automatic fallback to `st.map` point plot if the hexagon layer fails |

---

## 🧠 Design decisions worth knowing

### Global exclusion, applied everywhere
`POI_CATEGORY_L1 = 'residential'` is excluded in **every** query — option lists,
counts, and map points — via a case-insensitive `WHERE` clause injected by a single
`_where()` helper. The exclusion config is also part of every cache key, so editing
`EXCLUDE` automatically invalidates all cached results.

### Cache correctness
- `st.cache_data.clear()` runs **once at startup**, before any query — stale
  pre-exclusion results can never be served.
- All query functions are cached (`ttl=600`) with the exclusion config baked into
  the cache signature.

### NULL handling
- Option lists explicitly exclude NULLs (`IS NOT NULL`).
- Rows with NULL in a *filtered* column are dropped (SQL three-valued logic).
- The sub-district query requires **both** parent and child to be non-NULL,
  eliminating orphaned rows.

### Sub-districts as (district, sub-district) pairs
Sub-district names are not globally unique in real POI data. The app queries the
**pair** and selecting a pair constrains **both** `POI_L4` and `POI_L3`, so counts
can never leak across districts.

### Modern Streamlit API
Uses `width="stretch"` — `use_container_width` is removed after **2025-12-31**.

---

## 📦 Data

Uses the **GrabMaps Southeast Asia Places** sample dataset from the
**Snowflake Marketplace**:

```
GRABMAPS_SOUTHEAST_ASIA_PLACES_DATA_POI_SAMPLE.MAPS.SAMPLE_POI_DATA
```

Key columns used:

| Column | Role |
|---|---|
| `POI_COUNTRY_NAME`, `POI_CITY_NAME` | Location filters (single-select) |
| `POI_L3`, `POI_L4` | District / Sub-district (multi-select, qualified pairs) |
| `POI_CATEGORY_L1/L2/L3` | Category taxonomy (multi-select) |
| `POI_LATITUDE`, `POI_LONGITUDE` | Hexbin positions |

The data layer is **swappable** — point `TABLE` at Google Maps Platform exports,
a licensed POI feed, or your own proprietary dataset.

---

## 🚀 Deployment (Streamlit in Snowflake)

1. In Snowsight, create a new **Streamlit app** (in any database/schema you can write to).
2. Grant the app's warehouse (default in code: `POI_APP_WH` — change to yours):

   ```sql
   USE WAREHOUSE <your_warehouse>;
   ```

3. Ensure your role has access to the sample dataset:

   ```sql
   -- Import the listing from the Snowflake Marketplace first, then:
   GRANT IMPORTED PRIVILEGES ON DATABASE GRABMAPS_SOUTHEAST_ASIA_PLACES_DATA_POI_SAMPLE
       TO ROLE <your_role>;
   ```

4. Paste the full `poi_explorer.py` source into the app editor and **Run**.

> **Note:** The app expects an active Snowflake connection named `snowflake`
> (the SiS default). `SNOWFLAKE_CONNECTION_TTL` is optional and read from the
> environment.

---

## 🏗️ Architecture

```
Streamlit UI (2-column filters)
        │
        ├── geo_base() / cat_base() / combined_filters()   ← filter dicts
        │
        ├── _where()                                       ← WHERE builder
        │       • global exclusion (EXCLUDE)               always applied
        │       • equality / IN clauses with qmark params  SQL-injection safe
        │
        ├── Cached queries (st.cache_data, ttl = 10 min)
        │       • q_groupby(col, filters)      → option lists + counts
        │       • q_subdistricts(filters)      → (district, subdistrict) pairs
        │       • q_count(filters)             → metric counts
        │       • q_points(filters)            → map points (LIMIT 50,000)
        │
        └── pydeck HexagonLayer (2D, 130 m, hover counts)
                └── fallback: st.map
```

Key components:

- **`_where(filters)`** — single source of truth for the `WHERE` clause. Builds
  parameterized conditions (qmark style) and injects the global exclusion list.
- **`combined_filters()`** — intersects location and category selections when they
  overlap on the same column, so the combined count is always consistent.
- **`subdistrict_filter()`** — renders `Subdistrict · District` labels and derives
  the district set from the ticked pairs, keeping parent and child in lockstep.
- **`filter_signature()` / Compute gating** — results only render after an explicit
  **Compute**; any filter change invalidates them with a visible warning.

---

## 🗺️ Why hexagons at 130 m?

A 130 m hexagon approximates **H3 resolution 9** — small enough to distinguish
neighborhood blocks, large enough to produce stable density estimates from sample
data. `HexagonLayer` aggregates raw points client-side, so the app ships
coordinates (not pre-aggregated bins) and density recomputes on every pan/zoom.

---

## ⚠️ Known limitations

- Map points are capped at **50,000** per compute (`LIMIT` in `q_points`); counts
  are always exact from SQL.
- Category L2/L3 use flat lists — a same-named L3 under two L2s would appear once
  per parent (the qualified-pair pattern in `subdistrict_filter` transfers directly
  if needed).
- First page load is slower by design: the startup cache flush forces fresh queries
  so no pre-exclusion results can be served.

---

## 📄 License & data attribution

Code: MIT (adjust to your needs).

POI data: **GrabMaps Southeast Asia Places sample** via the Snowflake Marketplace —
usage is subject to the listing's terms. Verify licensing before commercial use.

---

## 🙋 About

Built as a demonstration of governed location intelligence on Snowflake:
**segment × area → countable targets**, without data egress, wrapped in
enterprise-grade access control.

Demo available upon request.
