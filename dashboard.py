import io
import os
import csv
import html
import math

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import redis
import pandas as pd

from redis.retry import Retry
from redis.backoff import NoBackoff
import altair as alt
import streamlit as st

from streamlit_autorefresh import st_autorefresh

# Needed for the Excel download (duplicates highlighted).
# Installed in the Docker image via requirements.txt.
try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


# ==================================================
# PAGE CONFIG
# ==================================================

st.set_page_config(
    page_title="Smart Camera Line Monitor",
    page_icon="📷",
    layout="wide"
)

# Refresh dashboard every 2 seconds
st_autorefresh(
    interval=2000,
    key="dashboard_refresh"
)


# ==================================================
# REDIS CONNECTION
# ==================================================

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Fail fast: redis-py otherwise retries for several seconds,
# longer than the 2 second refresh, and the page stays blank.
r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    retry=Retry(NoBackoff(), 1)
)

CSV_FILE = Path(
    os.getenv("SCAN_LOG_DIR", str(Path(__file__).resolve().parent))
) / "scan_log.csv"


# Saved here and published on the same key, so
# camera_redis_live.py applies changes instantly.
COOLDOWN_REDIS_KEY = "settings:cooldown_seconds"

# Written by camera_redis_live.py
CAMERA_STATUS_KEY = "camera:status"
LISTENER_HEARTBEAT_KEY = "camera:listener_heartbeat"

# Scan data deleted by "Reset scan data"
# (settings and camera status are kept)
SCAN_DATA_KEYS = [
    "stats:total_scans",
    "stats:valid_count",
    "stats:duplicate_count",
    "part_counts",
    "scan_events"
]

RECENT_SCAN_LIMIT = 200

# A duplicate keeps flashing for this long
ALARM_FLASH_SECONDS = 10


# ==================================================
# COLOURS
#
# ISA-101 style: neutral greys for normal operation,
# colour only where the operator must pay attention.
# ==================================================

INK = "#1b2229"
INK_2 = "#4b5663"
MUTED = "#78828e"
RULE = "#c8ced5"
GRID = "#e3e7eb"

VALID_COLOR = "#3b73ad"
DUPLICATE_COLOR = "#d03b3b"

CHART_FONT = "IBM Plex Sans, system-ui, Segoe UI, sans-serif"


def _local_timezone():
    # Docker sets TZ (Asia/Kolkata in compose.yaml)
    name = os.getenv("TZ")

    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass

    return datetime.now().astimezone().tzinfo


LOCAL_TZ = _local_timezone()


# ==================================================
# STYLES
# ==================================================

st.html(
    """
<style>
:root {
  --plane: #e8ebee;
  --panel: #fafbfc;
  --ink: #1b2229;
  --ink-2: #4b5663;
  --muted: #78828e;
  --rule: #c8ced5;
  --rule-soft: #dfe3e8;
  --ok: #0ca30c;
  --ok-ink: #1d6b2b;
  --ok-tint: #e3f1e5;
  --warn: #fab219;
  --warn-ink: #6b4a00;
  --alarm: #d03b3b;
  --alarm-ink: #8f1d1d;
  --alarm-tint: #fbe5e5;
}

[data-testid="stMainBlockContainer"] {
  padding-top: 1.25rem;
  padding-bottom: 2.5rem;
  max-width: 1680px;
}

/* ---------- header ---------- */

.lm-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px 32px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--rule);
}

.lm-title {
  margin: 0;
  font-size: 1.625rem;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: -0.01em;
  color: var(--ink);
}

.lm-subtitle {
  margin-top: 2px;
  font-size: 0.875rem;
  color: var(--ink-2);
}

.lm-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.lm-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 11px;
  border: 1px solid var(--rule);
  border-radius: 4px;
  background: var(--panel);
  font-size: 0.8125rem;
  color: var(--ink);
  white-space: nowrap;
}

.lm-chip-detail {
  color: var(--ink-2);
}

.lm-chip.is-alarm {
  border-color: var(--alarm);
  background: var(--alarm-tint);
  color: var(--alarm-ink);
}

.lm-chip.is-warn {
  border-color: #e0a419;
  background: #fff4d9;
  color: var(--warn-ink);
}

.lm-dot {
  width: 9px;
  height: 9px;
  flex: none;
  border-radius: 50%;
  background: var(--muted);
}

.lm-dot.ok { background: var(--ok); }
.lm-dot.warn { background: var(--warn); }
.lm-dot.alarm { background: var(--alarm); }

/* ---------- last scan annunciator ---------- */

/* Stretch the panel to the height of the right column */
[data-testid="stColumn"]:has(.lm-ann) > [data-testid="stVerticalBlock"] { height: 100%; }
[data-testid="stElementContainer"]:has(> [data-testid="stHtml"] > .lm-ann) { flex: 1 1 auto; }
[data-testid="stHtml"]:has(> .lm-ann) { height: 100%; }

.lm-ann {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 396px;
  border: 1px solid var(--rule);
  border-radius: 6px;
  background: var(--panel);
  overflow: hidden;
}

.lm-ann-band {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 22px;
  border-bottom: 1px solid var(--rule-soft);
}

.lm-ann-icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: none;
  border-radius: 50%;
  font-size: 1.125rem;
  font-weight: 600;
}

.lm-ann-state {
  font-size: 1.375rem;
  font-weight: 600;
  line-height: 1.2;
}

.lm-ann-note {
  margin-top: 2px;
  font-size: 0.875rem;
  color: var(--ink-2);
}

.lm-ann-body {
  display: grid;
  flex: 1;
  align-content: start;
  gap: 20px;
  padding: 20px 22px 22px;
}

.lm-ann-section {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--ink-2);
}

.lm-field-label {
  font-size: 0.8125rem;
  color: var(--ink-2);
}

.lm-field-code {
  margin-top: 2px;
  font-size: 2.625rem;
  font-weight: 600;
  line-height: 1.05;
  letter-spacing: 0.01em;
  color: var(--ink);
  overflow-wrap: anywhere;
}

.lm-field-text {
  margin-top: 2px;
  font-size: 1rem;
  font-weight: 500;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.lm-field-age {
  font-size: 0.8125rem;
  color: var(--ink-2);
}

.lm-field-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px 16px;
}

.lm-ann.is-valid .lm-ann-icon { background: var(--ok-tint); color: var(--ok-ink); }
.lm-ann.is-valid .lm-ann-state { color: var(--ok-ink); }

.lm-ann.is-idle .lm-ann-icon { background: #e4e8ec; color: var(--ink-2); }
.lm-ann.is-idle .lm-ann-state { color: var(--ink); }

.lm-ann.is-duplicate { border-color: var(--alarm); }
.lm-ann.is-duplicate .lm-ann-band { background: var(--alarm); border-bottom-color: var(--alarm); }
.lm-ann.is-duplicate .lm-ann-state,
.lm-ann.is-duplicate .lm-ann-note { color: #ffffff; }
.lm-ann.is-duplicate .lm-ann-icon { background: #ffffff; color: var(--alarm); }
.lm-ann.is-duplicate .lm-ann-body { background: var(--alarm-tint); }
.lm-ann.is-duplicate .lm-field-code { color: var(--alarm-ink); }

/* Unacknowledged-alarm style flash, only for a fresh duplicate */
.lm-ann.is-fresh { animation: lm-alarm-pulse 1s ease-in-out infinite; }

@keyframes lm-alarm-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(208, 59, 59, 0); }
  50% { box-shadow: 0 0 0 5px rgba(208, 59, 59, 0.45); }
}

@media (prefers-reduced-motion: reduce) {
  .lm-ann.is-fresh { animation: none; box-shadow: 0 0 0 4px rgba(208, 59, 59, 0.45); }
}

.lm-empty-text {
  font-size: 0.9375rem;
  color: var(--ink-2);
  max-width: 34ch;
}

/* ---------- KPI strip ---------- */

.lm-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  border: 1px solid var(--rule);
  border-radius: 6px;
  background: var(--panel);
  overflow: hidden;
}

/* Hairlines between tiles; the outer ones are clipped */
.lm-kpi {
  padding: 14px 16px 15px;
  box-shadow: 1px 0 0 var(--rule-soft), 0 1px 0 var(--rule-soft);
}

.lm-kpi-label {
  font-size: 0.8125rem;
  color: var(--ink-2);
}

.lm-kpi-value {
  margin-top: 4px;
  font-size: 1.875rem;
  font-weight: 600;
  line-height: 1.15;
  color: var(--ink);
}

.lm-kpi-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 0.75rem;
  color: var(--muted);
}

.lm-kpi-note.is-alarm {
  color: var(--alarm-ink);
  font-weight: 500;
}

/* ---------- panels (keyed Streamlit containers) ---------- */

.st-key-panel_throughput,
.st-key-panel_tables {
  padding: 16px 20px 12px;
  border: 1px solid var(--rule);
  border-radius: 6px;
  background: var(--panel);
}

.lm-panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px 20px;
}

.lm-panel-title {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--ink);
}

.lm-legend {
  display: flex;
  gap: 16px;
  font-size: 0.8125rem;
  color: var(--ink-2);
}

.lm-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.lm-swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.lm-chart-empty {
  display: grid;
  place-items: center;
  height: 214px;
  font-size: 0.9375rem;
  color: var(--ink-2);
}

/* ---------- sidebar ---------- */

[data-testid="stSidebar"] h3 {
  font-size: 0.9375rem;
}
</style>
"""
)


# ==================================================
# HELPERS
# ==================================================

def esc(value):
    return html.escape(str(value), quote=True)


def to_local(ms):
    # Naive local time (Vega shows it exactly as given)
    return datetime.fromtimestamp(ms / 1000, LOCAL_TZ).replace(tzinfo=None)


def format_age(seconds):

    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds} s ago"

    if seconds < 3600:
        return f"{seconds // 60} min ago"

    if seconds < 86400:
        return f"{seconds // 3600} h ago"

    return f"{seconds // 86400} d ago"


def format_scan_time(moment, today):

    if moment.date() == today:
        return moment.strftime("%H:%M:%S")

    return moment.strftime("%d-%m-%Y %H:%M:%S")


def parse_cooldown_seconds(raw):
    # None = not set yet (or invalid value in Redis)

    try:
        value = float(raw)

    except (TypeError, ValueError):
        return None

    if value < 0 or not math.isfinite(value):
        return None

    return value


def parse_events(events):

    rows = []

    for event_id, fields in events:

        ms = int(event_id.split("-")[0])

        rows.append({
            "ms": ms,
            "scanned_at": to_local(ms),
            "part_no": fields.get("part_no", ""),
            "serial_no": fields.get("serial_no", ""),
            "code_time": fields.get("date_time", ""),
            "duplicate": fields.get("status") == "INVALID"
        })

    return rows


# ==================================================
# CAMERA COOLDOWN SETTING
# ==================================================

def save_cooldown_seconds():
    # Runs the moment the cooldown input is changed
    # (Enter, +/- buttons, or clicking away).
    value = st.session_state["cooldown_input"]

    if value is None:
        # Field cleared - put the saved value back
        st.session_state["cooldown_input"] = st.session_state.get(
            "cooldown_synced"
        )
        st.toast("⚠️ Enter a cooldown in seconds")
        return

    value = float(value)

    try:
        r.set(COOLDOWN_REDIS_KEY, value)

        # Notify the camera right away (real-time update)
        r.publish(COOLDOWN_REDIS_KEY, value)

    except redis.RedisError as e:
        st.toast(f"⚠️ Cooldown not saved - Redis error: {e}")
        return

    st.session_state["cooldown_synced"] = value

    st.toast(f"⚙️ Scan cooldown set to {value:g} seconds")


# ==================================================
# RESET SCAN DATA
# ==================================================

def reset_scan_data():

    try:
        keys = SCAN_DATA_KEYS + list(
            r.scan_iter("product:*", count=1000)
        )

        pipe = r.pipeline(transaction=True)

        for start in range(0, len(keys), 1000):
            pipe.delete(*keys[start:start + 1000])

        pipe.execute()

    except redis.RedisError as e:
        st.toast(f"⚠️ Reset failed - Redis error: {e}")
        return

    st.session_state["confirm_reset"] = False

    st.toast("🗑️ Scan data cleared")


# ==================================================
# SCAN LOG (CSV / EXCEL EXPORT)
# ==================================================

SCAN_LOG_COLUMNS = ["Part No", "Date & Time", "Serial No", "Status"]


def read_scan_log():
    """
    Returns rows as [Part No, Date & Time, Serial No, Status].

    Rows from older logs without a Status column get it
    rebuilt with the same rule as the camera:
    first Part No + Serial No = VALID, repeats = DUPLICATE.
    """

    rows = []
    seen = set()

    with open(CSV_FILE, newline="", encoding="utf-8") as f:

        for row in csv.reader(f):

            if len(row) < 3 or row[0].strip() == "Part No":
                continue

            part_no, date_time, serial_no = (
                value.strip() for value in row[:3]
            )

            key = (part_no, serial_no)

            status = row[3].strip().upper() if len(row) >= 4 else ""

            if status not in ("VALID", "DUPLICATE"):
                status = "DUPLICATE" if key in seen else "VALID"

            seen.add(key)

            rows.append([part_no, date_time, serial_no, status])

    return rows


@st.cache_data(show_spinner=False, max_entries=4)
def scan_log_summary(mtime_ns, size):
    # Cached per file version, so the 2 second refresh
    # only re-reads the CSV when a new scan was written.
    rows = read_scan_log()

    duplicates = sum(
        1 for row in rows if row[3] == "DUPLICATE"
    )

    return len(rows), duplicates


def build_scan_log_excel():
    """
    Builds the Excel file when the download button is
    clicked. Duplicate rows are highlighted red.
    """

    rows = read_scan_log()

    output = io.BytesIO()

    workbook = xlsxwriter.Workbook(
        output,
        {"in_memory": True}
    )

    sheet = workbook.add_worksheet("Scan Log")

    header_format = workbook.add_format({
        "bold": True,
        "font_color": "#FFFFFF",
        "bg_color": "#1F4E78",
        "border": 1,
        "align": "center"
    })

    date_format = workbook.add_format({
        "num_format": "dd-mm-yyyy hh:mm:ss",
        "align": "left"
    })

    # Excel's standard "Light Red Fill with Dark Red Text"
    duplicate_format = workbook.add_format({
        "bold": True,
        "font_color": "#9C0006",
        "bg_color": "#FFC7CE"
    })

    duplicate_date_format = workbook.add_format({
        "bold": True,
        "font_color": "#9C0006",
        "bg_color": "#FFC7CE",
        "num_format": "dd-mm-yyyy hh:mm:ss",
        "align": "left"
    })

    sheet.write_row(0, 0, SCAN_LOG_COLUMNS, header_format)

    for row_index, (part_no, date_time, serial_no, status) in enumerate(rows, start=1):

        is_duplicate = status == "DUPLICATE"

        text_format = duplicate_format if is_duplicate else None

        # Part No / Serial No stay text so leading zeros are kept
        sheet.write_string(row_index, 0, part_no, text_format)

        try:
            sheet.write_datetime(
                row_index,
                1,
                datetime.strptime(date_time, "%d-%m-%Y %H:%M:%S"),
                duplicate_date_format if is_duplicate else date_format
            )

        except ValueError:
            sheet.write_string(row_index, 1, date_time, text_format)

        sheet.write_string(row_index, 2, serial_no, text_format)

        sheet.write_string(row_index, 3, status, text_format)

    sheet.set_column(0, 0, 14)
    sheet.set_column(1, 1, 22)
    sheet.set_column(2, 2, 14)
    sheet.set_column(3, 3, 13)

    sheet.freeze_panes(1, 0)

    sheet.autofilter(0, 0, max(len(rows), 1), len(SCAN_LOG_COLUMNS) - 1)

    if rows:
        # No green "number stored as text" triangles
        sheet.ignore_errors({
            "number_stored_as_text": f"A2:C{len(rows) + 1}"
        })

    workbook.close()

    return output.getvalue()


# ==================================================
# LOAD LIVE DATA (one round trip to Redis)
# ==================================================

def load_live_data():

    pipe = r.pipeline(transaction=False)

    pipe.time()
    pipe.mget(
        "stats:total_scans",
        "stats:valid_count",
        "stats:duplicate_count"
    )
    pipe.hgetall("part_counts")
    pipe.hgetall(CAMERA_STATUS_KEY)
    pipe.exists(LISTENER_HEARTBEAT_KEY)
    pipe.get(COOLDOWN_REDIS_KEY)
    pipe.xrevrange("scan_events", count=RECENT_SCAN_LIMIT)

    (
        server_time,
        stats,
        part_counts,
        camera_status,
        heartbeat,
        cooldown_raw,
        recent_events
    ) = pipe.execute()

    # Redis server clock = same clock as the scan timestamps
    now_ms = server_time[0] * 1000 + server_time[1] // 1000

    hour_events = r.xrange(
        "scan_events",
        min=str(now_ms - 3_600_000),
        max="+"
    )

    recent = parse_events(recent_events)

    # Repeat count for each duplicate in the recent list
    duplicate_keys = list(dict.fromkeys(
        (row["part_no"], row["serial_no"])
        for row in recent if row["duplicate"]
    ))

    repeats = {}

    if duplicate_keys:

        pipe = r.pipeline(transaction=False)

        for part_no, serial_no in duplicate_keys:
            pipe.hget(f"product:{part_no}:{serial_no}", "duplicate_count")

        for key, count in zip(duplicate_keys, pipe.execute()):
            repeats[key] = int(count or 0)

    return {
        "now_ms": now_ms,
        "total": int(stats[0] or 0),
        "valid": int(stats[1] or 0),
        "duplicates": int(stats[2] or 0),
        "part_counts": {k: int(v) for k, v in part_counts.items()},
        "camera_status": camera_status,
        "listener_online": bool(heartbeat),
        "cooldown": parse_cooldown_seconds(cooldown_raw),
        "recent": recent,
        "hour": parse_events(hour_events),
        "repeats": repeats
    }


# ==================================================
# HEADER
# ==================================================

def chip(text, detail="", dot="", state=""):

    dot_html = f'<span class="lm-dot {dot}"></span>' if dot else ""

    detail_html = (
        f' <span class="lm-chip-detail">{esc(detail)}</span>'
        if detail else ""
    )

    return (
        f'<span class="lm-chip {state}">{dot_html}'
        f"{esc(text)}{detail_html}</span>"
    )


def render_header(chips_html):

    st.html(
        f"""
<div class="lm-header">
  <div>
    <h1 class="lm-title">Smart Camera Line Monitor</h1>
    <div class="lm-subtitle">Data Matrix duplicate detection</div>
  </div>
  <div class="lm-chips">{chips_html}</div>
</div>
"""
    )


try:
    live = load_live_data()

except redis.RedisError as e:

    render_header(
        chip("Redis unreachable", dot="alarm", state="is-alarm")
    )

    st.error(
        f"Cannot reach Redis at {REDIS_HOST}:{REDIS_PORT}. "
        f"Check that the Redis container is running. "
        f"The dashboard reconnects automatically.\n\n{e}"
    )

    st.stop()


now_local = to_local(live["now_ms"])
today = now_local.date()

camera_status = live["camera_status"]

if not live["listener_online"]:
    camera_chip = chip(
        "Listener offline",
        dot="alarm",
        state="is-alarm"
    )

elif camera_status.get("state") == "connected":
    camera_chip = chip(
        "Camera connected",
        detail=camera_status.get("address", ""),
        dot="ok"
    )

else:
    camera_chip = chip(
        "Camera disconnected",
        dot="warn",
        state="is-warn"
    )

if live["cooldown"] is None:
    cooldown_chip = chip("Cooldown not set", dot="warn", state="is-warn")
else:
    cooldown_chip = chip("Cooldown", detail=f"{live['cooldown']:g} s")

last_scan = live["recent"][0] if live["recent"] else None

if last_scan:
    last_scan_chip = chip(
        "Last scan",
        detail=format_age((live["now_ms"] - last_scan["ms"]) / 1000)
    )
else:
    last_scan_chip = chip("No scans yet")

clock_chip = chip("Updated", detail=now_local.strftime("%H:%M:%S"))

render_header(camera_chip + cooldown_chip + last_scan_chip + clock_chip)


# ==================================================
# LAST SCAN + KPIs + THROUGHPUT
# ==================================================

left, right = st.columns([1, 2], gap="medium")

with left:

    if last_scan is None:

        st.html(
            """
<div class="lm-ann is-idle">
  <div class="lm-ann-band">
    <div class="lm-ann-icon">…</div>
    <div>
      <div class="lm-ann-state">Waiting for the first scan</div>
    </div>
  </div>
  <div class="lm-ann-body">
    <div class="lm-empty-text">
      Scans appear here as soon as the camera reads a Data Matrix code.
    </div>
  </div>
</div>
"""
        )

    else:

        age_seconds = (live["now_ms"] - last_scan["ms"]) / 1000

        if last_scan["duplicate"]:
            state_class = "is-duplicate"
            if age_seconds <= ALARM_FLASH_SECONDS:
                state_class += " is-fresh"
            icon = "!"
            state_text = "Duplicate detected"
            note = "Already scanned before. Remove this part from the line."
        else:
            state_class = "is-valid"
            icon = "✓"
            state_text = "Valid product"
            note = "Last scan"

        st.html(
            f"""
<div class="lm-ann {state_class}" role="status" aria-live="polite">
  <div class="lm-ann-band">
    <div class="lm-ann-icon" aria-hidden="true">{icon}</div>
    <div>
      <div class="lm-ann-state">{esc(state_text)}</div>
      <div class="lm-ann-note">{esc(note)}</div>
    </div>
  </div>
  <div class="lm-ann-body">
    <div>
      <div class="lm-field-label">Part No</div>
      <div class="lm-field-code">{esc(last_scan["part_no"])}</div>
    </div>
    <div>
      <div class="lm-field-label">Serial No</div>
      <div class="lm-field-code">{esc(last_scan["serial_no"])}</div>
    </div>
    <div class="lm-field-grid">
      <div>
        <div class="lm-field-label">Code date &amp; time</div>
        <div class="lm-field-text">{esc(last_scan["code_time"])}</div>
      </div>
      <div>
        <div class="lm-field-label">Scanned</div>
        <div class="lm-field-text">{esc(format_scan_time(last_scan["scanned_at"], today))}</div>
        <div class="lm-field-age">{esc(format_age(age_seconds))}</div>
      </div>
    </div>
  </div>
</div>
"""
        )


with right:

    # ---------- KPI strip ----------

    total = live["total"]
    duplicates = live["duplicates"]
    duplicate_rate = (duplicates / total * 100) if total else 0.0

    hour_rows = live["hour"]
    hour_count = len(hour_rows)
    hour_duplicates = sum(1 for row in hour_rows if row["duplicate"])

    ten_min_count = sum(
        1 for row in hour_rows
        if live["now_ms"] - row["ms"] <= 600_000
    )

    if hour_duplicates:
        duplicate_note = (
            f'<div class="lm-kpi-note is-alarm">'
            f'<span class="lm-dot alarm"></span>'
            f"{hour_duplicates:,} in last hour</div>"
        )
    else:
        duplicate_note = '<div class="lm-kpi-note">None in last hour</div>'

    st.html(
        f"""
<div class="lm-kpis">
  <div class="lm-kpi">
    <div class="lm-kpi-label">Total scans</div>
    <div class="lm-kpi-value">{total:,}</div>
    <div class="lm-kpi-note">Since last reset</div>
  </div>
  <div class="lm-kpi">
    <div class="lm-kpi-label">Valid products</div>
    <div class="lm-kpi-value">{live["valid"]:,}</div>
    <div class="lm-kpi-note">{len(live["part_counts"]):,} part numbers</div>
  </div>
  <div class="lm-kpi">
    <div class="lm-kpi-label">Duplicates</div>
    <div class="lm-kpi-value">{duplicates:,}</div>
    {duplicate_note}
  </div>
  <div class="lm-kpi">
    <div class="lm-kpi-label">Duplicate rate</div>
    <div class="lm-kpi-value">{duplicate_rate:.1f}%</div>
    <div class="lm-kpi-note">Of all scans</div>
  </div>
  <div class="lm-kpi">
    <div class="lm-kpi-label">Last hour</div>
    <div class="lm-kpi-value">{hour_count:,}</div>
    <div class="lm-kpi-note">{ten_min_count / 10:.1f} per min now</div>
  </div>
</div>
"""
    )

    # ---------- Throughput chart ----------

    with st.container(key="panel_throughput"):

        st.html(
            f"""
<div class="lm-panel-head">
  <h2 class="lm-panel-title">Scans per minute, last 60 minutes</h2>
  <div class="lm-legend">
    <span><i class="lm-swatch" style="background:{VALID_COLOR}"></i>Valid</span>
    <span><i class="lm-swatch" style="background:{DUPLICATE_COLOR}"></i>Duplicate</span>
  </div>
</div>
"""
        )

        window_end = now_local.replace(second=0, microsecond=0) + timedelta(minutes=1)
        window_start = window_end - timedelta(minutes=60)

        if not hour_rows:

            st.html(
                '<div class="lm-chart-empty">'
                "No scans in the last 60 minutes."
                "</div>"
            )

        else:

            df_hour = pd.DataFrame(hour_rows)

            df_hour["minute"] = df_hour["scanned_at"].dt.floor("min")
            df_hour["Status"] = df_hour["duplicate"].map(
                {True: "Duplicate", False: "Valid"}
            )

            df_minutes = (
                df_hour.groupby(["minute", "Status"])
                .size()
                .reset_index(name="Scans")
            )

            # Valid at the bottom of each column
            df_minutes["stack_order"] = df_minutes["Status"].map(
                {"Valid": 0, "Duplicate": 1}
            )

            throughput_chart = (
                alt.Chart(df_minutes)
                .mark_bar(
                    stroke="#fafbfc",
                    strokeWidth=1,
                    cornerRadiusTopLeft=2,
                    cornerRadiusTopRight=2,
                    # One minute wide (the 1px stroke leaves a 2px gap),
                    # so bars scale with the screen
                    width={"band": 1}
                )
                .encode(
                    x=alt.X(
                        "yearmonthdatehoursminutes(minute):T",
                        title=None,
                        scale=alt.Scale(
                            domain=[
                                window_start.isoformat(),
                                window_end.isoformat()
                            ]
                        ),
                        axis=alt.Axis(
                            format="%H:%M",
                            tickCount=12,
                            grid=False,
                            labelAngle=0
                        )
                    ),
                    y=alt.Y(
                        "Scans:Q",
                        title=None,
                        stack="zero",
                        axis=alt.Axis(tickMinStep=1, tickCount=4)
                    ),
                    color=alt.Color(
                        "Status:N",
                        scale=alt.Scale(
                            domain=["Valid", "Duplicate"],
                            range=[VALID_COLOR, DUPLICATE_COLOR]
                        ),
                        legend=None
                    ),
                    order=alt.Order("stack_order:Q"),
                    tooltip=[
                        alt.Tooltip(
                            "yearmonthdatehoursminutes(minute):T",
                            title="Minute",
                            format="%H:%M"
                        ),
                        alt.Tooltip("Status:N"),
                        alt.Tooltip("Scans:Q")
                    ]
                )
                .properties(height=214)
                .configure(font=CHART_FONT, background="transparent")
                .configure_view(stroke=None)
                .configure_axis(
                    labelColor=MUTED,
                    labelFontSize=11,
                    gridColor=GRID,
                    domainColor=RULE,
                    tickColor=RULE,
                    labelPadding=6
                )
            )

            st.altair_chart(
                throughput_chart,
                theme=None,
                width="stretch"
            )


# ==================================================
# TABLES
# ==================================================

PANEL_CELL = "background-color: #fafbfc"


def highlight_duplicates(row):

    if row["Status"].endswith("Duplicate"):
        return ["background-color: #fbe5e5; color: #8f1d1d"] * len(row)

    return [PANEL_CELL] * len(row)


def panel_table(df):
    # White rows to match the panel background
    return df.style.map(lambda _: PANEL_CELL)


st.write("")

with st.container(key="panel_tables"):

    recent_tab, duplicates_tab, parts_tab = st.tabs(
        ["Recent scans", "Duplicate alerts", "Parts"]
    )

    with recent_tab:

        if live["recent"]:

            df_recent = pd.DataFrame([
                {
                    "Scanned": format_scan_time(row["scanned_at"], today),
                    "Part No": row["part_no"],
                    "Serial No": row["serial_no"],
                    "Code date & time": row["code_time"],
                    "Status": "✕ Duplicate" if row["duplicate"] else "✓ Valid"
                }
                for row in live["recent"][:50]
            ])

            st.dataframe(
                df_recent.style.apply(highlight_duplicates, axis=1),
                hide_index=True,
                width="stretch",
                height=420
            )

            st.caption(
                f"Latest {len(df_recent)} scans, newest first. "
                f"The full history is in Reports in the sidebar."
            )

        else:
            st.info("No scans yet. Scans appear here as they happen.")

    with duplicates_tab:

        duplicate_rows = [
            row for row in live["recent"] if row["duplicate"]
        ]

        if duplicate_rows:

            df_dup = pd.DataFrame([
                {
                    "Scanned": format_scan_time(row["scanned_at"], today),
                    "Part No": row["part_no"],
                    "Serial No": row["serial_no"],
                    "Code date & time": row["code_time"],
                    "Times repeated": live["repeats"].get(
                        (row["part_no"], row["serial_no"]), 0
                    )
                }
                for row in duplicate_rows
            ])

            st.dataframe(
                panel_table(df_dup),
                hide_index=True,
                width="stretch",
                height=min(420, 38 + 35 * len(df_dup))
            )

            st.caption(
                f"{len(df_dup)} duplicate(s) in the latest "
                f"{len(live['recent'])} scans. Times repeated counts "
                f"every repeat of that Part No + Serial No since the last reset."
            )

        else:
            st.success("No duplicates in the latest scans.")

    with parts_tab:

        if live["part_counts"]:

            df_parts = (
                pd.DataFrame(
                    live["part_counts"].items(),
                    columns=["Part No", "Valid products"]
                )
                .sort_values("Valid products", ascending=False)
                .reset_index(drop=True)
            )

            df_parts["Share"] = (
                df_parts["Valid products"]
                / df_parts["Valid products"].sum()
                * 100
            ).round(1)

            chart_col, table_col = st.columns([3, 2], gap="large")

            with chart_col:

                top_parts = df_parts.head(12)

                parts_chart = (
                    alt.Chart(top_parts)
                    .mark_bar(
                        color=VALID_COLOR,
                        cornerRadiusTopRight=3,
                        cornerRadiusBottomRight=3,
                        height={"band": 0.7}
                    )
                    .encode(
                        y=alt.Y(
                            "Part No:N",
                            sort="-x",
                            title=None,
                            axis=alt.Axis(
                                labelColor=INK,
                                labelFontSize=12,
                                ticks=False,
                                domain=False
                            )
                        ),
                        x=alt.X(
                            "Valid products:Q",
                            title=None,
                            axis=alt.Axis(tickMinStep=1, tickCount=5)
                        ),
                        tooltip=[
                            alt.Tooltip("Part No:N"),
                            alt.Tooltip("Valid products:Q"),
                            alt.Tooltip("Share:Q", title="Share (%)")
                        ]
                    )
                    .properties(height=max(140, 32 * len(top_parts)))
                    .configure(font=CHART_FONT, background="transparent")
                    .configure_view(stroke=None)
                    .configure_axis(
                        labelColor=MUTED,
                        labelFontSize=11,
                        gridColor=GRID,
                        domainColor=RULE,
                        tickColor=RULE,
                        labelPadding=6
                    )
                )

                st.altair_chart(parts_chart, theme=None, width="stretch")

                if len(df_parts) > len(top_parts):
                    st.caption(
                        f"Top {len(top_parts)} of {len(df_parts)} part numbers. "
                        f"All are listed in the table."
                    )

            with table_col:

                st.dataframe(
                    panel_table(df_parts).format({"Share": "{:.1f} %"}),
                    hide_index=True,
                    width="stretch",
                    height=min(420, 38 + 35 * len(df_parts))
                )

        else:
            st.info("No valid products counted yet.")


# ==================================================
# SIDEBAR
# ==================================================

# ---------- Camera settings ----------

st.sidebar.subheader("Camera settings")

current_cooldown = live["cooldown"]

# Keep the input in sync with Redis, e.g. when the
# cooldown was changed from another browser.
if st.session_state.get("cooldown_synced", "unset") != current_cooldown:
    st.session_state["cooldown_input"] = current_cooldown
    st.session_state["cooldown_synced"] = current_cooldown

st.sidebar.number_input(
    "Scan cooldown (seconds)",
    min_value=0.0,
    # None lets the field be empty (not set yet);
    # the real value comes from session state above
    value=None,
    placeholder="Not set",
    step=0.5,
    format="%.1f",
    key="cooldown_input",
    on_change=save_cooldown_seconds,
    help=(
        "After a scan is accepted, every other camera "
        "reading is ignored for this many seconds. "
        "0 = no cooldown. Applied instantly when you "
        "press Enter or use the +/- buttons."
    )
)

if current_cooldown is None:

    st.sidebar.warning(
        "Cooldown not set — the camera accepts every "
        "reading until you enter a value."
    )

else:

    st.sidebar.caption(
        f"Active cooldown: **{current_cooldown:g} s** — "
        f"applied to the camera instantly."
    )

st.sidebar.divider()

# ---------- Reports ----------

st.sidebar.subheader("Reports")

csv_bytes = b""
csv_read_error = False

if CSV_FILE.exists():

    try:
        csv_bytes = CSV_FILE.read_bytes()

    except OSError as e:
        csv_read_error = True
        st.sidebar.warning(f"Could not read {CSV_FILE.name}: {e}")

if csv_bytes:

    csv_stat = CSV_FILE.stat()

    record_count, duplicate_scans = scan_log_summary(
        csv_stat.st_mtime_ns,
        csv_stat.st_size
    )

    st.sidebar.caption(
        f"Full scan log: {record_count:,} scans, "
        f"{duplicate_scans:,} duplicates. "
        f"Duplicates are highlighted red in Excel."
    )

    if xlsxwriter is not None:

        # Built only when clicked, not on every refresh
        st.sidebar.download_button(
            label="Download Excel",
            data=build_scan_log_excel,
            file_name=f"scan_log_{datetime.now():%Y-%m-%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            icon=":material/table_view:",
            on_click="ignore",
            key="download_scan_log_excel",
            width="stretch"
        )

    else:

        st.sidebar.warning(
            "Excel download needs XlsxWriter: "
            "pip install XlsxWriter"
        )

    st.sidebar.download_button(
        label="Download CSV",
        data=csv_bytes,
        file_name=f"scan_log_{datetime.now():%Y-%m-%d}.csv",
        mime="text/csv",
        icon=":material/download:",
        on_click="ignore",
        key="download_scan_log",
        width="stretch"
    )

elif not csv_read_error:

    st.sidebar.caption(
        "No scans logged yet. The scan log is created "
        "when the camera accepts its first scan."
    )

st.sidebar.divider()

# ---------- Maintenance ----------

st.sidebar.subheader("Maintenance")

confirm_reset = st.sidebar.checkbox(
    "I confirm I want to delete all scan data",
    key="confirm_reset"
)

st.sidebar.button(
    "Reset scan data",
    icon=":material/delete:",
    disabled=not confirm_reset,
    on_click=reset_scan_data,
    width="stretch"
)

st.sidebar.caption(
    "Clears counts, recent scans and duplicate history. "
    "The cooldown setting and the CSV scan log are kept."
)
