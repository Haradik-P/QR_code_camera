import redis
import pandas as pd
import streamlit as st

from streamlit_autorefresh import st_autorefresh


# --------------------------------------------------
# PAGE CONFIG
# --------------------------------------------------

st.set_page_config(
    page_title="Smart Camera Dashboard",
    page_icon="📦",
    layout="wide"
)


# Refresh dashboard every 2 seconds
st_autorefresh(
    interval=2000,
    key="dashboard_refresh"
)


# --------------------------------------------------
# REDIS CONNECTION
# --------------------------------------------------

r = redis.Redis(
    host="127.0.0.1",
    port=6379,
    decode_responses=True
)

st.sidebar.subheader("System Controls")

confirm_reset = st.sidebar.checkbox(
    "I confirm I want to delete all stored data"
)

if st.sidebar.button(
    "🗑️ Reset Redis Data",
    type="primary",
    disabled=not confirm_reset
):
    r.flushdb()

    st.sidebar.success(
        "Redis data cleared successfully."
    )

    st.rerun()

# --------------------------------------------------
# TITLE
# --------------------------------------------------

st.title("Smart Camera Product Dashboard")

st.caption(
    "Live product counting and duplicate detection"
)


# --------------------------------------------------
# GET STATISTICS
# --------------------------------------------------

total_scans = int(
    r.get("stats:total_scans") or 0
)

valid_count = int(
    r.get("stats:valid_count") or 0
)

duplicate_count = int(
    r.get("stats:duplicate_count") or 0
)


# --------------------------------------------------
# TOP CARDS
# --------------------------------------------------

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Total Scans",
        total_scans
    )

with col2:
    st.metric(
        "Valid Products",
        valid_count
    )

with col3:
    st.metric(
        "Duplicate Detected",
        duplicate_count
    )


st.divider()


# --------------------------------------------------
# PRODUCT COUNT BY PART NUMBER
# --------------------------------------------------

st.subheader("Product Count by Part No.")

part_counts = r.hgetall("part_counts")

if part_counts:

    product_data = []

    for part_no, count in part_counts.items():

        product_data.append({
            "Part No": part_no,
            "Product Count": int(count)
        })

    df_products = pd.DataFrame(product_data)

    df_products = df_products.sort_values(
        by="Product Count",
        ascending=False
    )
    
    st.dataframe(
    df_products,
    width="stretch",
    hide_index=True
    )

else:

    st.info("No product data available yet.")


st.divider()


# --------------------------------------------------
# RECENT DUPLICATES
# --------------------------------------------------

st.subheader("Recent Duplicate Alerts")

events = r.xrevrange(
    "scan_events",
    count=200
)

duplicate_rows = []

for event_id, event in events:

    if event.get("status") == "INVALID":

        duplicate_rows.append({
            "Part No": event.get("part_no"),
            "Serial No": event.get("serial_no"),
            "Date & Time": event.get("date_time"),
            "Status": "DUPLICATE"
        })


if duplicate_rows:

    df_duplicates = pd.DataFrame(
        duplicate_rows
    )

    st.error(
        f"{len(duplicate_rows)} duplicate scan(s) found "
        f"in recent history."
    )

    st.dataframe(
        df_duplicates,
        use_container_width=True,
        hide_index=True
    )

else:

    st.success(
        "No duplicate products detected."
    )