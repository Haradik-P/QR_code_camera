import socket
import redis
# import winsound
import threading
import time
import re
import csv
import math
from pathlib import Path
from datetime import datetime
import os

try:
    import winsound
except ImportError:
    winsound = None

# ==================================================
# SETTINGS
# ==================================================

HOST = "0.0.0.0"
PORT = 5000

# After accepting one Data Matrix, ignore every
# other reading for the cooldown time.
#
# The cooldown is SET ON THE DASHBOARD and saved in
# Redis under this key. Every change on the dashboard
# is also published on this key, and the camera picks
# it up instantly (real time, no restart).
#
# Until it has been set on the dashboard,
# no cooldown is applied.
COOLDOWN_REDIS_KEY = "settings:cooldown_seconds"

# Live cooldown value from the dashboard
# (None = not set yet)
current_cooldown_seconds = None

cooldown_lock = threading.Lock()

# Live status shown on the dashboard header
CAMERA_STATUS_KEY = "camera:status"
LISTENER_HEARTBEAT_KEY = "camera:listener_heartbeat"

last_accepted_scan_time = 0

# Every accepted scan (VALID + DUPLICATE) is appended here.
# Saved in the same folder as this script, so dashboard.py
# can find it for the download button.

# CSV_FILE = Path(__file__).resolve().parent / "scan_log.csv"

CSV_FILE = Path(
    os.getenv("SCAN_LOG_DIR", str(Path(__file__).resolve().parent))
) / "scan_log.csv"

CSV_FILE.parent.mkdir(parents=True, exist_ok=True)

CSV_HEADER = ["Part No", "Date & Time", "Serial No", "Status"]

# Header used before the Status column was added
OLD_CSV_HEADER = ["Part No", "Date & Time", "Serial No"]


# ==================================================
# REDIS CONNECTION
# ==================================================

# r = redis.Redis(
#     host="127.0.0.1",
#     port=6379,
#     decode_responses=True
# )

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "127.0.0.1"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True
)

try:
    print("Redis connected:", r.ping())

except redis.ConnectionError:
    print("ERROR: Redis is not running.")
    print("Start Redis Docker container first.")
    raise SystemExit


# ==================================================
# CSV LOG
# ==================================================

# Rows that could not be written yet.
#
# On Windows, Excel locks a CSV while it is open,
# so writing fails. Instead of losing the scan
# (or dropping the camera connection), the row is
# kept here and written on the next scan.
pending_csv_rows = []


def save_to_csv(part_no, date_time, serial_no, status):

    pending_csv_rows.append(
        [part_no, date_time, serial_no, status]
    )

    try:

        write_header = (
            not CSV_FILE.exists()
            or CSV_FILE.stat().st_size == 0
        )

        with open(
            CSV_FILE,
            "a",
            newline="",
            encoding="utf-8"
        ) as f:

            writer = csv.writer(f)

            if write_header:
                writer.writerow(CSV_HEADER)

            writer.writerows(pending_csv_rows)

        pending_csv_rows.clear()

    except OSError as e:

        print(
            f"⚠️ CSV not updated ({e}). "
            f"{len(pending_csv_rows)} row(s) will be "
            f"written on the next scan. "
            f"Close {CSV_FILE.name} if it is open in Excel."
        )


def upgrade_old_csv():
    """
    Logs created before the Status column existed
    have only 3 columns. Add Status to them once,
    so old and new rows line up.

    Same rule as the Redis check:
    first Part No + Serial No = VALID,
    every repeat              = DUPLICATE
    """

    try:

        if (
            not CSV_FILE.exists()
            or CSV_FILE.stat().st_size == 0
        ):
            return

        with open(
            CSV_FILE,
            newline="",
            encoding="utf-8"
        ) as f:
            rows = list(csv.reader(f))

        if not rows or rows[0] != OLD_CSV_HEADER:
            return

        seen = set()
        upgraded = [CSV_HEADER]

        for row in rows[1:]:

            if len(row) < 3:
                continue

            part_no, date_time, serial_no = row[:3]
            key = (part_no, serial_no)

            if len(row) >= 4 and row[3] in ("VALID", "DUPLICATE"):
                status = row[3]
            elif key in seen:
                status = "DUPLICATE"
            else:
                status = "VALID"

            seen.add(key)

            upgraded.append(
                [part_no, date_time, serial_no, status]
            )

        tmp_file = CSV_FILE.with_suffix(".tmp")

        with open(
            tmp_file,
            "w",
            newline="",
            encoding="utf-8"
        ) as f:
            csv.writer(f).writerows(upgraded)

        os.replace(tmp_file, CSV_FILE)

        print(
            f"CSV log upgraded with Status column "
            f"({len(upgraded) - 1} existing rows)."
        )

    except OSError as e:

        print(
            f"⚠️ Could not add Status column to "
            f"existing {CSV_FILE.name}: {e}"
        )


upgrade_old_csv()


# ==================================================
# VIRTUAL BUZZER
# ==================================================
def virtual_buzzer():
    if winsound is None:
        print("🚨 DUPLICATE ALERT: Buzzer unavailable in Linux container")
        return

    try:
        start_time = time.time()

        while time.time() - start_time < 5:
            winsound.Beep(2200, 250)
            winsound.Beep(1400, 250)

    except Exception as e:
        print("Buzzer error:", e)

# ==================================================
# CAMERA COOLDOWN (SET ON DASHBOARD)
# ==================================================

def read_cooldown_seconds():
    """
    Cooldown saved from the dashboard.
    Returns None when it has not been set yet
    (or the saved value is invalid).
    """

    try:
        value = float(r.get(COOLDOWN_REDIS_KEY))

    except (TypeError, ValueError):
        # TypeError = key not set yet
        return None

    except redis.RedisError:
        # Keep working with the last known value
        return current_cooldown_seconds

    if value < 0 or not math.isfinite(value):
        return None

    return value


def get_cooldown_seconds():
    """
    Reads the cooldown and prints a line in the
    terminal whenever it was changed on the dashboard.
    """

    # Called from the scan loop and the watcher thread
    with cooldown_lock:
        return _update_cooldown_seconds()


def _update_cooldown_seconds():

    global current_cooldown_seconds

    value = read_cooldown_seconds()

    if value != current_cooldown_seconds:

        if value is None:

            print(
                "\n⚠️ Scan cooldown is not set on the "
                "dashboard - no cooldown applied."
            )

        else:

            old_value = (
                "not set"
                if current_cooldown_seconds is None
                else f"{current_cooldown_seconds:g} sec"
            )

            print(
                f"\n⚙️ Scan cooldown changed on dashboard: "
                f"{old_value} → {value:g} sec"
            )

        current_cooldown_seconds = value

    return value


def watch_cooldown_changes():
    """
    Real-time cooldown updates.

    The dashboard publishes on COOLDOWN_REDIS_KEY every
    time the cooldown is changed. This background thread
    listens and applies the new value immediately,
    without waiting for the next scan.
    """

    while True:

        try:

            pubsub = r.pubsub(
                ignore_subscribe_messages=True
            )

            pubsub.subscribe(COOLDOWN_REDIS_KEY)

            # Pick up any change made while
            # not subscribed (startup / reconnect)
            get_cooldown_seconds()

            for _ in pubsub.listen():
                get_cooldown_seconds()

        except redis.RedisError as e:

            print(
                f"⚠️ Cooldown watcher lost Redis "
                f"connection ({e}) - retrying..."
            )

            time.sleep(2)


# ==================================================
# LIVE STATUS FOR THE DASHBOARD
# ==================================================

def set_camera_status(state, address=""):
    """
    state = "connected" or "disconnected"
    """

    try:
        r.hset(
            CAMERA_STATUS_KEY,
            mapping={
                "state": state,
                "address": address,
                "since": int(time.time())
            }
        )

    except redis.RedisError as e:
        print("Could not update camera status:", e)


def send_listener_heartbeat():
    """
    Tells the dashboard this script is running.
    The key expires after 10 seconds, so if the
    script stops, the dashboard shows it as offline.
    """

    while True:

        try:
            r.set(
                LISTENER_HEARTBEAT_KEY,
                int(time.time()),
                ex=10
            )

        except redis.RedisError:
            pass

        time.sleep(3)


def scan_allowed():
    """
    Allows one scan and then blocks all scans
    for the cooldown set on the dashboard.
    """

    global last_accepted_scan_time

    # Not set on dashboard yet = no cooldown
    cooldown_seconds = get_cooldown_seconds() or 0

    current_time = time.monotonic()

    elapsed = current_time - last_accepted_scan_time

    # Still inside cooldown
    if elapsed < cooldown_seconds:

        remaining = cooldown_seconds - elapsed

        print(
            f"⏳ IGNORED: Camera cooldown "
            f"({remaining:.1f} sec remaining)"
        )

        return False

    # Accept this scan and start new cooldown
    last_accepted_scan_time = current_time

    return True


# ==================================================
# REDIS PRODUCT LOGIC
# ==================================================

def process_product(part_no, date_time, serial_no):

    # --------------------------------------------------
    # Duplicate condition:
    #
    # SAME Part No + SAME Serial No
    #
    # Different Part + Same Serial = VALID
    # Same Part + Different Serial = VALID
    # Same Part + Same Serial = DUPLICATE
    # --------------------------------------------------

    product_key = f"product:{part_no}:{serial_no}"

    # Atomic check
    #
    # 1 = new Part No + Serial No combination
    # 0 = already exists
    is_new = r.hsetnx(
        product_key,
        "registered",
        1
    )

    # ==================================================
    # VALID PRODUCT
    # ==================================================

    if is_new:

        r.hset(
            product_key,
            mapping={
                "part_no": part_no,
                "serial_no": serial_no,
                "first_date_time": date_time,
                "last_seen": date_time,
                "duplicate_count": 0
            }
        )

        # Product count based on Part No
        r.hincrby(
            "part_counts",
            part_no,
            1
        )

        r.incr("stats:valid_count")
        r.incr("stats:total_scans")

        status = "VALID"

        print("\n" + "=" * 55)
        print("✅ VALID PRODUCT")
        print("=" * 55)

        print("Part No     :", part_no)
        print("Date & Time :", date_time)
        print("Serial No   :", serial_no)

        print("=" * 55)

    # ==================================================
    # DUPLICATE PRODUCT
    # ==================================================

    else:

        r.hincrby(
            product_key,
            "duplicate_count",
            1
        )

        r.hset(
            product_key,
            "last_seen",
            date_time
        )

        r.incr("stats:duplicate_count")
        r.incr("stats:total_scans")

        status = "INVALID"

        print("\n" + "=" * 55)
        print("🚨🚨🚨 DUPLICATE DETECTED 🚨🚨🚨")
        print("=" * 55)

        print("Part No     :", part_no)
        print("Date & Time :", date_time)
        print("Serial No   :", serial_no)

        print("=" * 55)

        # Start virtual buzzer in background
        threading.Thread(
            target=virtual_buzzer,
            daemon=True
        ).start()

    # ==================================================
    # SAVE EVERY ACCEPTED SCAN
    # ==================================================

    r.xadd(
        "scan_events",
        {
            "part_no": part_no,
            "date_time": date_time,
            "serial_no": serial_no,
            "status": status
        }
    )

    # Same record that was printed in the terminal
    save_to_csv(
        part_no=part_no,
        date_time=date_time,
        serial_no=serial_no,
        status="VALID" if status == "VALID" else "DUPLICATE"
    )


# ==================================================
# DATA MATRIX FORMAT
# ==================================================

# Actual camera output:
#
# 690129  21-10-202613:14:53  229583
#
#
# Meaning:
#
# Part No     = 690129
# Date        = 21-10-2026
# Time        = 13:14:53
# Serial No   = 229583
#
#
# The camera currently gives no space between:
#
# 21-10-2026
# and
# 13:14:53
#
# But \s* means this parser will accept BOTH:
#
# 21-10-202613:14:53
#
# and
#
# 21-10-2026 13:14:53


record_pattern = re.compile(
    r"(?P<part_no>\d+)\s+"
    r"(?P<date>\d{2}-\d{2}-\d{4})"
    r"\s*"
    r"(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<serial_no>\d+)"
)


# ==================================================
# PROCESS TCP BUFFER
# ==================================================

def process_buffer(buffer):

    while True:

        # --------------------------------------------------
        # REMOVE NoRead
        # --------------------------------------------------

        while "NoRead" in buffer:

            buffer = buffer.replace(
                "NoRead",
                "",
                1
            )

            print("IGNORED: NoRead")

        # --------------------------------------------------
        # FIND COMPLETE DATA MATRIX
        # --------------------------------------------------

        match = record_pattern.search(buffer)

        # Not enough data received yet
        if not match:

            # Safety against unlimited buffer growth
            if len(buffer) > 5000:
                buffer = buffer[-2000:]

            return buffer

        # --------------------------------------------------
        # EXTRACT DATA MATRIX VALUES
        # --------------------------------------------------

        part_no = match.group(
            "part_no"
        ).strip()

        date = match.group(
            "date"
        ).strip()

        scan_time = match.group(
            "time"
        ).strip()

        serial_no = match.group(
            "serial_no"
        ).strip()

        # Normalize:
        #
        # 21-10-202613:14:53
        #
        # becomes:
        #
        # 21-10-2026 13:14:53

        date_time = f"{date} {scan_time}"

        # --------------------------------------------------
        # VALIDATE DATE AND TIME
        # --------------------------------------------------

        try:

            datetime.strptime(
                date_time,
                "%d-%m-%Y %H:%M:%S"
            )

        except ValueError:

            print(
                "ERROR: Invalid Date & Time:",
                date_time
            )

            # Remove bad record
            buffer = buffer[
                match.end():
            ]

            continue

        # --------------------------------------------------
        # SHOW CAMERA READING
        # --------------------------------------------------

        print("\nDATA MATRIX DETECTED")

        print(
            "Part No     :",
            part_no
        )

        print(
            "Date & Time :",
            date_time
        )

        print(
            "Serial No   :",
            serial_no
        )

        # --------------------------------------------------
        # COOLDOWN CHECK (value set on dashboard)
        # --------------------------------------------------

        if scan_allowed():

            if current_cooldown_seconds is None:

                print(
                    "✅ Scan accepted - "
                    "no cooldown (not set on dashboard)."
                )

            else:

                print(
                    f"✅ Scan accepted - "
                    f"{current_cooldown_seconds:g} second cooldown started."
                )

            process_product(
                part_no=part_no,
                date_time=date_time,
                serial_no=serial_no
            )

        else:

            # IMPORTANT:
            #
            # This scan is completely ignored.
            #
            # It is NOT:
            #
            # - stored in Redis
            # - counted as total scan
            # - counted as duplicate
            # - counted as valid
            # - added to scan_events

            print(
                "Scan not stored in Redis."
            )

        # --------------------------------------------------
        # REMOVE THIS RECORD FROM BUFFER
        # --------------------------------------------------

        buffer = buffer[
            match.end():
        ]


# ==================================================
# TCP SERVER
# ==================================================

server = socket.socket(
    socket.AF_INET,
    socket.SOCK_STREAM
)

server.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_REUSEADDR,
    1
)

server.bind(
    (HOST, PORT)
)

server.listen(5)


# ==================================================
# START MESSAGE
# ==================================================

print("\n" + "=" * 60)

print(
    "SMART CAMERA DATA MATRIX "
    "DUPLICATE DETECTION SYSTEM"
)

print("=" * 60)

print(
    f"Listening on TCP port {PORT}"
)

current_cooldown_seconds = read_cooldown_seconds()

if current_cooldown_seconds is None:

    print(
        "Scan cooldown: NOT SET - set it on the dashboard "
        "(no cooldown until then)"
    )

else:

    print(
        f"Scan cooldown: {current_cooldown_seconds:g} seconds "
        f"(change it on the dashboard)"
    )

# Apply dashboard changes in real time
threading.Thread(
    target=watch_cooldown_changes,
    daemon=True
).start()

# Live status for the dashboard
set_camera_status("disconnected")

threading.Thread(
    target=send_listener_heartbeat,
    daemon=True
).start()

print(
    f"CSV log: {CSV_FILE}"
)

print(
    "Waiting for camera connection..."
)


# ==================================================
# CAMERA CONNECTION LOOP
# ==================================================

while True:

    conn, addr = server.accept()

    print(
        "\nCamera connected:",
        addr
    )

    set_camera_status("connected", addr[0])

    # TCP is a stream.
    #
    # One Data Matrix can arrive across
    # multiple recv() calls.
    #
    # Therefore we maintain a buffer.

    buffer = ""

    try:

        while True:

            data = conn.recv(
                4096
            )

            if not data:

                print(
                    "Camera disconnected"
                )

                break

            decoded = data.decode(
                "utf-8",
                errors="replace"
            )

            # Add received TCP data
            # to the existing buffer.
            buffer += decoded

            # Find complete records
            buffer = process_buffer(
                buffer
            )

    except ConnectionResetError:

        print(
            "Camera connection reset."
        )

    except Exception as e:

        print(
            "Camera connection error:",
            e
        )

    finally:

        conn.close()

        set_camera_status("disconnected", addr[0])

        print(
            "\nConnection closed."
        )

        print(
            "Waiting for camera connection..."
        )