import pandas as pd
import streamlit as st

GRAMS_PER_LB = 453.59237
KG_PER_LB = 0.45359237

DEFAULT_XLSX = "CSGG.xlsx"  # keep in repo root next to app.py


st.set_page_config(page_title="PET + PCR + CO₂ (Gigaton-style)", page_icon="♻️", layout="wide")
st.title("♻️ PET + PCR + CO₂ Calculator (Gigaton-style)")
if st.sidebar.button("Clear cache"):
    st.cache_data.clear()
    st.rerun()

st.caption("Totals in pounds, avoided CO₂ in metric tons. Batch upload supported.")


def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Map common column name variants into required canonical names."""
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]

    col_map_candidates = {
        "Vendor Part Number": [
            "Vendor Part Number", "Vendor Part #", "Vendor Part No", "Part Number",
            "Part #", "VendorPN", "Vendor PN"
        ],
        "Item Description": [
            "Item Description", "Description", "Item", "Item Desc"
        ],
        "Weight (g)": [
            "Weight (g)", "Gram Weight", "Gram Weight (g)", "Grams", "Weight Grams",
            "Weight_g", "Weight"
        ],
        "PCR %": [
            "PCR %", "PCR%", "PCR Content", "PCR Content %", "% PCR", "Post-Consumer %"
        ],
    }

    resolved = {}
    for target, options in col_map_candidates.items():
        for opt in options:
            if opt in df.columns:
                resolved[target] = opt
                break

    missing = [k for k in col_map_candidates.keys() if k not in resolved]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    df = df.rename(columns={resolved[k]: k for k in resolved})
    return df


@st.cache_data
def load_xlsx(path_or_file) -> pd.DataFrame:
    df = pd.read_excel(path_or_file)
    df = normalize_cols(df)

    df["Vendor Part Number"] = df["Vendor Part Number"].astype(str).str.strip()
    df["Item Description"] = df["Item Description"].astype(str).str.strip()

    df["Weight (g)"] = pd.to_numeric(df["Weight (g)"], errors="coerce")
    df["PCR %"] = pd.to_numeric(df["PCR %"], errors="coerce")

    df = df.dropna(subset=["Vendor Part Number", "Weight (g)", "PCR %"])
    df["PCR %"] = df["PCR %"].clip(lower=0, upper=100)

    # Default quantity column
    df["Quantity"] = 0
    return df


def load_purchase_csv(file) -> pd.DataFrame:
    """Expect columns: Vendor Part Number, Quantity (case-insensitive tolerant)."""
    df = pd.read_csv(file)
    df.columns = [c.strip() for c in df.columns]

    # Tolerant mapping
    pn_candidates = ["Vendor Part Number", "Part Number", "Part #", "VendorPN", "Vendor PN"]
    qty_candidates = ["Quantity", "Qty", "Quantity Purchased", "Units", "Count"]

    pn = next((c for c in pn_candidates if c in df.columns), None)
    qty = next((c for c in qty_candidates if c in df.columns), None)

    if pn is None or qty is None:
        raise ValueError(
            "Purchase CSV must include columns for part number and quantity.\n"
            f"Found columns: {list(df.columns)}\n"
            "Accepted part number headers: " + ", ".join(pn_candidates) + "\n"
            "Accepted quantity headers: " + ", ".join(qty_candidates)
        )

    df = df.rename(columns={pn: "Vendor Part Number", qty: "Quantity"})
    df["Vendor Part Number"] = df["Vendor Part Number"].astype(str).str.strip()
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
    df = df.groupby("Vendor Part Number", as_index=False)["Quantity"].sum()
    return df


with st.sidebar:
    st.header("Data + Factors")

    data_source = st.radio("Database source", ["Use repo file (CSGG.xlsx)", "Upload Excel"], index=0)

    if data_source == "Upload Excel":
        upload_xlsx = st.file_uploader("Upload database (.xlsx)", type=["xlsx"])
        if upload_xlsx is None:
            st.info("Upload your database Excel to continue.")
            st.stop()
        db = load_xlsx(upload_xlsx)
    else:
        try:
            db = load_xlsx(DEFAULT_XLSX)
        except Exception as e:
            st.error(f"Could not load {DEFAULT_XLSX}. Put it in the repo root. Details: {e}")
            st.stop()

    st.divider()
    st.subheader("Project Gigaton inputs (PET)")
    st.caption("Units are kg CO₂e per kg PET. Set these to the Gigaton PET values you use internally.")

    # You said: “PET virgin” and “benefit converting to PET PCR”
    pet_virgin_ef = st.number_input("PET virgin EF (kg CO₂e / kg PET)", value=2.15, min_value=0.0, step=0.01)
    pet_pcr_benefit = st.number_input(
        "Benefit: convert PET virgin → PET PCR (kg CO₂e avoided / kg converted)",
        value=1.70,
        min_value=0.0,
        step=0.01,
        help="This is the delta (virgin EF − PCR EF) expressed directly as an avoided amount per kg converted."
    )

    st.divider()
    st.subheader("Customer baseline")
    current_pcr_pct = st.number_input("Current packaging PCR% (baseline)", value=0.0, min_value=0.0, max_value=100.0, step=1.0)

    st.divider()
    st.subheader("Batch purchases (optional)")
    purchase_csv = st.file_uploader("Upload purchases CSV (Part Number + Quantity)", type=["csv"])

    # Provide a template CSV download
    template = pd.DataFrame({"Vendor Part Number": ["EXAMPLE-123"], "Quantity": [1000]})
    st.download_button(
        "Download purchase CSV template",
        data=template.to_csv(index=False).encode("utf-8"),
        file_name="purchase_template.csv",
        mime="text/csv",
    )


# Apply purchases CSV if provided
work = db.copy()
csv_unmatched = None

if purchase_csv is not None:
    try:
        purchases = load_purchase_csv(purchase_csv)
        work = work.merge(purchases, on="Vendor Part Number", how="left", suffixes=("", "_csv"))
        # prefer CSV qty if present
        work["Quantity"] = work["Quantity_csv"].fillna(work["Quantity"])
        work = work.drop(columns=[c for c in work.columns if c.endswith("_csv")])

        # Identify unmatched part numbers
        db_pns = set(db["Vendor Part Number"].tolist())
        purchase_pns = set(purchases["Vendor Part Number"].tolist())
        missing = sorted(list(purchase_pns - db_pns))
        if missing:
            csv_unmatched = missing
    except Exception as e:
        st.error(f"Could not process purchase CSV: {e}")
        st.stop()


left, right = st.columns([1.35, 1])

with left:
    st.subheader("1) Select parts + quantities")

    if csv_unmatched:
        st.warning(
            "Some part numbers in your purchase CSV were not found in the database. "
            "They are ignored until added to the Excel database."
        )
        st.code("\n".join(csv_unmatched[:50]) + ("\n..." if len(csv_unmatched) > 50 else ""))

    search = st.text_input("Search part number / description", value="")

    view = work
    if search.strip():
        s = search.strip().lower()
        view = view[
            view["Vendor Part Number"].str.lower().str.contains(s, na=False)
            | view["Item Description"].str.lower().str.contains(s, na=False)
        ]

    selected = st.multiselect(
        "Select Vendor Part Numbers",
        options=view["Vendor Part Number"].tolist(),
    )

    if not selected:
        st.info("Select at least one part number.")
        st.stop()

    calc = work[work["Vendor Part Number"].isin(selected)].copy().sort_values("Vendor Part Number")

    edited = st.data_editor(
        calc[["Vendor Part Number", "Item Description", "Weight (g)", "PCR %", "Quantity"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Quantity": st.column_config.NumberColumn("Quantity purchased", min_value=0, step=1, format="%d"),
            "PCR %": st.column_config.NumberColumn("PCR %", format="%.0f"),
            "Weight (g)": st.column_config.NumberColumn("Weight (g)", format="%.3f"),
        },
    )

with right:
    st.subheader("2) Results")

    edited = edited.copy()
    edited["Quantity"] = pd.to_numeric(edited["Quantity"], errors="coerce").fillna(0)
    edited["Weight (g)"] = pd.to_numeric(edited["Weight (g)"], errors="coerce").fillna(0)
    edited["PCR %"] = pd.to_numeric(edited["PCR %"], errors="coerce").fillna(0).clip(0, 100)

    total_grams = (edited["Weight (g)"] * edited["Quantity"]).sum()
    total_lbs = total_grams / GRAMS_PER_LB
    total_kg = total_lbs * KG_PER_LB

    # “Converted to PCR” mass = PCR content mass
    pcr_grams = (edited["Weight (g)"] * edited["Quantity"] * (edited["PCR %"] / 100.0)).sum()
    pcr_lbs = pcr_grams / GRAMS_PER_LB
    pcr_kg = pcr_lbs * KG_PER_LB

    # Gigaton-style avoided CO2:
    # avoided_kg = converted_mass_kg * benefit_kgCO2_per_kg
    avoided_kg = pcr_kg * pet_pcr_benefit
    avoided_metric_tons = avoided_kg / 1000.0

    # Baseline avoided (customer current PCR%)
    baseline_pcr_kg = total_kg * (current_pcr_pct / 100.0)
    baseline_avoided_metric_tons = (baseline_pcr_kg * pet_pcr_benefit) / 1000.0

    advantage_metric_tons = avoided_metric_tons - baseline_avoided_metric_tons

    st.metric("Total pounds of plastic used", f"{total_lbs:,.2f} lb")
    st.metric("Total pounds of PCR content used", f"{pcr_lbs:,.2f} lb")
    st.metric("CO₂ avoided (metric tons)", f"{avoided_metric_tons:,.4f} t")

    st.divider()
    st.metric("D6 Advantage: CO₂ avoided vs current (metric tons)", f"{advantage_metric_tons:,.4f} t")

    st.caption(
        "Avoided CO₂ is calculated as: (PCR mass converted, kg) × (benefit, kg CO₂e avoided per kg converted). "
        "Set PET Virgin EF + conversion benefit to match your Walmart Project Gigaton methodology inputs."
    )

st.divider()
st.subheader("3) Detail table")
detail = edited.copy()
detail["Total Weight (lb)"] = (detail["Weight (g)"] * detail["Quantity"]) / GRAMS_PER_LB
detail["PCR Weight (lb)"] = detail["Total Weight (lb)"] * (detail["PCR %"] / 100.0)
detail["PCR Weight (kg)"] = detail["PCR Weight (lb)"] * KG_PER_LB
detail["Avoided CO₂ (metric tons)"] = (detail["PCR Weight (kg)"] * pet_pcr_benefit) / 1000.0

st.dataframe(
    detail[
        [
            "Vendor Part Number",
            "Item Description",
            "Quantity",
            "Weight (g)",
            "PCR %",
            "Total Weight (lb)",
            "PCR Weight (lb)",
            "Avoided CO₂ (metric tons)",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)
