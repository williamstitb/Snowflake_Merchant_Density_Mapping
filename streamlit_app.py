# ═══════════════════════════════════════════════════════════════════════
# POI EXPLORER — Streamlit in Snowflake
# Left: Location filters · Right: Category filters
# Checkbox lists with select-all + counts · Three-metric summary
# Map: 2D HexagonLayer on raw points (res 9 ≈ 130 m hexagons)
# Global exclusion: POI_CATEGORY_L1 = 'Residential' removed everywhere.
# Sub-districts are DISTRICT-QUALIFIED pairs (no cross-district collisions).
# Cache flushed once at startup; exclusion config is part of cache keys.
# Uses width='stretch' (use_container_width is removed after 2025-12-31).
# ═══════════════════════════════════════════════════════════════════════

import streamlit as st
import os
import pandas as pd
import pydeck as pdk

# ── ONE-TIME CACHE FLUSH (before any query — no stale results) ─────
st.cache_data.clear()

conn = st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))
session = conn.session()

TABLE = "GRABMAPS_SOUTHEAST_ASIA_PLACES_DATA_POI_SAMPLE.MAPS.SAMPLE_POI_DATA"

LOCATION_LEVELS = [("POI_L3", "District"), ("POI_L4", "Sub-district")]
CATEGORY_LEVELS = [
    ("POI_CATEGORY_L1", "Category L1"),
    ("POI_CATEGORY_L2", "Category L2"),
    ("POI_CATEGORY_L3", "Category L3"),
]
ALL_KEYS = [k for k, _ in LOCATION_LEVELS + CATEGORY_LEVELS]

HEX_RADIUS_M = 130   # ≈ H3 resolution 9

# ── GLOBAL EXCLUSION ───────────────────────────────────────────────
EXCLUDE = [("POI_CATEGORY_L1", "residential")]   # case-insensitive
EXCL_KEY = str(EXCLUDE)   # in cache keys → edits auto-bust cache

st.set_page_config(layout="wide", page_title="POI Explorer", page_icon="🗺️")

try:
    session.sql("USE WAREHOUSE POI_APP_WH").collect()
except Exception:
    pass

# ── STATE ──────────────────────────────────────────────────────────
if "f" not in st.session_state:
    st.session_state.f = {k: [] for k in ALL_KEYS}
    st.session_state.f["country"] = None
    st.session_state.f["city"] = None
if "computed" not in st.session_state:
    st.session_state.computed = False
if "sig" not in st.session_state:
    st.session_state.sig = None


def invalidate():
    st.session_state.computed = False


def clear_level_keys(keys: list):
    for k in keys:
        st.session_state.f[k] = []
        for sk in list(st.session_state.keys()):
            if sk.startswith(f"cb_{k}_") or sk in (f"sa_{k}", f"q_{k}"):
                del st.session_state[sk]
    invalidate()


def filter_signature() -> str:
    f = st.session_state.f
    parts = [str(f["country"]), str(f["city"])]
    parts += [",".join(map(str, f[k])) for k in ALL_KEYS]
    return "|".join(parts)


# ── DATA ───────────────────────────────────────────────────────────
def _where(filters: dict):
    """WHERE conditions + qmark params. Global exclusions always applied,
    so every count, option list, and map point is Residential-free."""
    conds, params = [], []
    for col, val in EXCLUDE:
        conds.append(f"UPPER({col}) != UPPER(?)")
        params.append(val)
    for c, v in filters.items():
        if isinstance(v, (list, tuple)):
            if not v:
                continue
            conds.append(f"{c} IN ({', '.join(['?'] * len(v))})")
            params.extend(v)
        else:
            conds.append(f"{c} = ?")
            params.append(v)
    return conds, params


@st.cache_data(ttl=600, show_spinner=False)
def q_groupby(col: str, filters: dict, _excl: str = EXCL_KEY) -> pd.DataFrame:
    conds, params = _where(filters)
    conds.append(f"{col} IS NOT NULL")
    sql = (f"SELECT {col} AS VALUE, COUNT(*) AS CNT FROM {TABLE} "
           f"WHERE {' AND '.join(conds)} GROUP BY 1 ORDER BY CNT DESC")
    return session.sql(sql, params=params).to_pandas()


@st.cache_data(ttl=600, show_spinner=False)
def q_subdistricts(filters: dict, _excl: str = EXCL_KEY) -> pd.DataFrame:
    """(District, Sub-district) pairs — every sub-district labeled with and
    constrained to its real parent district. NULL parents excluded."""
    conds, params = _where(filters)
    conds += ["POI_L3 IS NOT NULL", "POI_L4 IS NOT NULL"]
    sql = (f"SELECT POI_L3 AS DIST, POI_L4 AS VALUE, COUNT(*) AS CNT "
           f"FROM {TABLE} WHERE {' AND '.join(conds)} "
           f"GROUP BY 1, 2 ORDER BY 1, CNT DESC")
    return session.sql(sql, params=params).to_pandas()


@st.cache_data(ttl=600, show_spinner=False)
def q_count(filters: dict, _excl: str = EXCL_KEY) -> int:
    conds, params = _where(filters)
    conds += ["POI_LATITUDE IS NOT NULL", "POI_LONGITUDE IS NOT NULL"]
    sql = f"SELECT COUNT(*) AS N FROM {TABLE} WHERE {' AND '.join(conds)}"
    df = session.sql(sql, params=params).to_pandas()
    return int(df["N"].iloc[0]) if not df.empty else 0


@st.cache_data(ttl=600, show_spinner=False)
def q_points(filters: dict, _excl: str = EXCL_KEY) -> pd.DataFrame:
    conds, params = _where(filters)
    conds += ["POI_LATITUDE IS NOT NULL", "POI_LONGITUDE IS NOT NULL",
              "POI_LATITUDE BETWEEN -90 AND 90",
              "POI_LONGITUDE BETWEEN -180 AND 180"]
    sql = (f"SELECT POI_LATITUDE AS LATITUDE, POI_LONGITUDE AS LONGITUDE "
           f"FROM {TABLE} WHERE {' AND '.join(conds)} LIMIT 50000")
    return session.sql(sql, params=params).to_pandas()


def geo_base() -> dict:
    f = st.session_state.f
    out = {}
    if f["country"]: out["POI_COUNTRY_NAME"] = f["country"]
    if f["city"]:    out["POI_CITY_NAME"]    = f["city"]
    for col, _ in LOCATION_LEVELS:
        if f[col]: out[col] = f[col]
    return out


def cat_base() -> dict:
    f = st.session_state.f
    out = {}
    for col, _ in CATEGORY_LEVELS:
        if f[col]: out[col] = f[col]
    return out


def combined_filters() -> dict:
    out = geo_base()
    for col, v in cat_base().items():
        if col in out and isinstance(out[col], list):
            inter = list(set(out[col]) & set(v))
            if inter:
                out[col] = inter
        else:
            out[col] = v
    return out


# ── FILTER WIDGET (flat levels) ────────────────────────────────────
def level_filter(label: str, col: str, upstream: dict) -> bool:
    df = q_groupby(col, upstream)
    if df.empty:
        st.caption("— no options at this level —")
        return False
    count_map = dict(zip(df["VALUE"], df["CNT"]))

    search = st.text_input("Search", key=f"q_{col}", label_visibility="collapsed",
                           placeholder=f"Search {label.lower()}…")
    shown = [o for o in df["VALUE"].tolist()
             if search.lower() in str(o).lower()] if search else df["VALUE"].tolist()

    select_all = st.checkbox("Select all", key=f"sa_{col}")

    chosen = []
    with st.container(height=220):
        for o in shown:
            lbl = f"{o}  ·  {int(count_map.get(o, 0)):,}"
            if select_all:
                st.checkbox(lbl, value=True, key=f"cb_{col}_{o}", disabled=True)
                chosen.append(o)
            elif st.checkbox(lbl, key=f"cb_{col}_{o}"):
                chosen.append(o)

    if search:  # keep hidden-but-ticked values
        chosen += [v for v in st.session_state.f[col]
                   if v not in shown and v in count_map]
        chosen = list(dict.fromkeys(chosen))

    if sorted(chosen) != sorted(st.session_state.f[col]):
        st.session_state.f[col] = chosen
        invalidate()
    return bool(chosen)


# ── SUB-DISTRICT WIDGET (district-qualified pairs) ─────────────────
def subdistrict_filter(upstream: dict) -> bool:
    """Lists 'Subdistrict · District' pairs; selecting applies BOTH
    POI_L4 IN (…) and POI_L3 IN (…), so no cross-district leakage."""
    pairs = q_subdistricts(dict(upstream))
    if pairs.empty:
        st.caption("— no sub-districts —")
        return False

    pairs["LABEL"] = pairs["VALUE"] + " · " + pairs["DIST"]
    count_map = dict(zip(pairs["LABEL"], pairs["CNT"]))

    search = st.text_input("Search", key="q_POI_L4", label_visibility="collapsed",
                           placeholder="Search sub-district…")
    shown = [l for l in pairs["LABEL"].tolist()
             if search.lower() in l.lower()] if search else pairs["LABEL"].tolist()

    select_all = st.checkbox("Select all", key="sa_POI_L4")

    chosen = []
    with st.container(height=220):
        for lbl in shown:
            text = f"{lbl}  ·  {int(count_map.get(lbl, 0)):,}"
            if select_all:
                st.checkbox(text, value=True, key=f"cb_POI_L4_{lbl}", disabled=True)
                chosen.append(lbl)
            elif st.checkbox(text, key=f"cb_POI_L4_{lbl}"):
                chosen.append(lbl)

    if search:  # keep hidden-but-ticked pairs
        chosen += [l for l in st.session_state.f.get("POI_L4_PAIRS", [])
                   if l not in shown]
        chosen = list(dict.fromkeys(chosen))

    sel_dists  = sorted({c.split(" · ")[1] for c in chosen})
    sel_subdis = sorted({c.split(" · ")[0] for c in chosen})

    if (sel_dists, sel_subdis) != (f["POI_L3"], f["POI_L4"]):
        f["POI_L3"] = sel_dists       # constrain BOTH parent and child
        f["POI_L4"] = sel_subdis
        invalidate()
    return bool(chosen)


# ── MAP: 2D HEXAGONAL HEATMAP ──────────────────────────────────────
def hex_heatmap(points: pd.DataFrame):
    layer = pdk.Layer(
        "HexagonLayer",
        data=points,
        get_position="[LONGITUDE, LATITUDE]",
        radius=HEX_RADIUS_M,        # 130 m ≈ H3 res 9
        extruded=False,             # 2D — flat filled hexagons
        elevation_range=[0, 0],     # zero height; color carries density
        coverage=0.9,
        pickable=True,
    )
    view = pdk.ViewState(
        latitude=float(points["LATITUDE"].mean()),
        longitude=float(points["LONGITUDE"].mean()),
        zoom=12, pitch=0,
    )
    st.pydeck_chart(
        pdk.Deck(layers=[layer], initial_view_state=view,
                 tooltip={"html": "<b>POIs:</b> {elevationValue}"}),
        width="stretch",            # ← replaces use_container_width=True
    )


# ── UI ─────────────────────────────────────────────────────────────
st.title("🗺️ POI Explorer")
st.caption("Set filters on both sides, then press **Compute**. "
           "Residential POIs are excluded everywhere.")

f = st.session_state.f
left, right = st.columns(2, gap="large")

with left:
    with st.container(border=True):
        st.subheader("📍 Location")
        countries = q_groupby("POI_COUNTRY_NAME", {})
        cmap = dict(zip(countries["VALUE"], countries["CNT"]))
        st.metric("Total POIs",
                  f"{int(countries['CNT'].sum()) if not countries.empty else 0:,}")

        country = st.selectbox(
            "Country", countries["VALUE"].tolist(), index=None,
            placeholder="Choose a country…",
            format_func=lambda c: f"{c}  ·  {int(cmap.get(c, 0)):,}")
        if country != f["country"]:
            f["country"] = country
            f["city"] = None
            clear_level_keys([k for k, _ in LOCATION_LEVELS])

        if f["country"]:
            cities = q_groupby("POI_CITY_NAME",
                               {"POI_COUNTRY_NAME": f["country"]})
            citymap = dict(zip(cities["VALUE"], cities["CNT"]))
            city = st.selectbox(
                "City", cities["VALUE"].tolist(), index=None,
                placeholder="Choose a city…",
                format_func=lambda c: f"{c}  ·  {int(citymap.get(c, 0)):,}")
            if city != f["city"]:
                f["city"] = city
                clear_level_keys([k for k, _ in LOCATION_LEVELS])

            if f["city"]:
                upstream = {"POI_COUNTRY_NAME": f["country"],
                            "POI_CITY_NAME": f["city"]}
                if level_filter("District", "POI_L3", dict(upstream)):
                    upstream["POI_L3"] = f["POI_L3"]
                    subdistrict_filter(upstream)

with right:
    with st.container(border=True):
        st.subheader("🏷️ Category")
        st.caption("Counts reflect your location filters · Residential excluded.")
        upstream = geo_base()
        for col, label in CATEGORY_LEVELS:
            if not level_filter(label, col, dict(upstream)):
                break
            upstream[col] = f[col]

st.divider()
b1, b2, _ = st.columns([3, 1, 3])
if b1.button("⚙️ Compute", type="primary", width="stretch"):
    st.session_state.computed = True
    st.session_state.sig = filter_signature()
if b2.button("🔄 Reset"):
    f["country"] = None
    f["city"] = None
    clear_level_keys(ALL_KEYS)
    st.session_state.computed = False
    st.rerun()

if st.session_state.computed and st.session_state.sig != filter_signature():
    st.session_state.computed = False
    st.warning("Filters changed — press **Compute** to refresh.")

# ── RESULTS ────────────────────────────────────────────────────────
if st.session_state.computed:
    n_geo = q_count(geo_base())
    n_cat = q_count(cat_base())
    n_all = q_count(combined_filters())

    st.markdown("### 📊 Summary")
    m1, m2, m3 = st.columns(3)
    m1.metric("Location filter count", f"{n_geo:,}")
    m2.metric("Category filter count", f"{n_cat:,}")
    m3.metric("Combined filter count", f"{n_all:,}")

    points = q_points(combined_filters())
    if points.empty or n_all == 0:
        st.warning("No POIs match the combined filters. "
                   "Loosen a filter and recompute.")
    else:
        st.markdown("### 🗺️ POI Density — Hexagonal Heatmap")
        try:
            hex_heatmap(points)
            st.caption(f"{n_all:,} POIs (Residential excluded) · "
                       f"{HEX_RADIUS_M} m flat hexagons · color = density · "
                       "hover a hexagon for its count")
        except Exception as e:
            st.error(f"Hexagon layer failed: {type(e).__name__}: {e}")
            st.map(points)
            st.caption(f"{n_all:,} POIs plotted as points")
else:
    st.info("No result queries run until you press **Compute**.")
