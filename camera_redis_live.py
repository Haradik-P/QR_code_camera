import socket
import redis
import winsound
import threading
import time
import re
from datetime import datetime


# ==================================================
# REDIS CONNECTION
# ==================================================

r = redis.Redis(
    host="127.0.0.1",
    port=6379,
    decode_responses=True
)

try:
    print("Redis connected:", r.ping())
except redis.ConnectionError:
    print("ERROR: Redis is not running.")
    raise SystemExit


# ==================================================
# VIRTUAL BUZZER
# ==================================================

def virtual_buzzer():
    try:
        for _ in range(10):
            winsound.Beep(2500, 200)
            winsound.Beep(1800, 200)
            winsound.Beep(2500, 200)
            time.sleep(0.1)

    except Exception as e:
        print("Buzzer error:", e)

# ==================================================
# REDIS PRODUCT LOGIC
# ==================================================

def process_product(part_no, date_time, serial_no):

    # Duplicate identity:
    # Part No + Serial No
    product_key = f"product:{part_no}:{serial_no}"

    # Atomic first-registration check
    is_new = r.hsetnx(
        product_key,
        "registered",
        1
    )

    # --------------------------------------------------
    # NEW / VALID PRODUCT
    # --------------------------------------------------

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
        print("Serial No   :", serial_no)
        print("Date & Time :", date_time)
        print("=" * 55)

    # --------------------------------------------------
    # DUPLICATE
    # --------------------------------------------------

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
        print("Serial No   :", serial_no)
        print("Date & Time :", date_time)
        print("=" * 55)

        # Virtual buzzer
        threading.Thread(
            target=virtual_buzzer,
            daemon=True
        ).start()

    # --------------------------------------------------
    # STORE EVERY SCAN
    # --------------------------------------------------

    r.xadd(
        "scan_events",
        {
            "part_no": part_no,
            "date_time": date_time,
            "serial_no": serial_no,
            "status": status
        }
    )


# ==================================================
# CAMERA RECORD PATTERN
# ==================================================

# Example camera output:
#
# Part No     : PN-1030
# Serial No   : SN-5026
# Date & Time : 2026-09-25 12:32:00
#
# It also works if TCP joins everything:
#
# Part No : PN-1030Serial No : SN-5026Date & Time : ...

record_pattern = re.compile(
    r"Part\s*No\s*:\s*"
    r"(?P<part_no>.*?)"
    r"\s*Serial\s*No\s*:\s*"
    r"(?P<serial_no>.*?)"
    r"\s*Date\s*&\s*Time\s*:\s*"
    r"(?P<date_time>"
    r"\d{4}-\d{2}-\d{2}\s+"
    r"\d{2}:\d{2}:\d{2}"
    r")",
    re.IGNORECASE | re.DOTALL
)


# ==================================================
# PROCESS TCP BUFFER
# ==================================================

def process_buffer(buffer):

    # --------------------------------------------------
    # Remove CR/LF dependency
    # --------------------------------------------------

    buffer = buffer.replace("\r", " ")
    buffer = buffer.replace("\n", " ")

    while True:

        buffer = buffer.lstrip()

        # --------------------------------------------------
        # REMOVE NoRead
        # --------------------------------------------------

        if buffer.startswith("NoRead"):

            print("IGNORED: NoRead")

            buffer = buffer[len("NoRead"):]

            continue

        # --------------------------------------------------
        # SEARCH FOR COMPLETE PRODUCT RECORD
        # --------------------------------------------------

        match = record_pattern.search(buffer)

        if not match:

            # Nothing complete yet.
            # Keep buffer and wait for more TCP data.

            # Avoid unlimited buffer growth
            if len(buffer) > 10000:
                buffer = buffer[-5000:]

            break

        # --------------------------------------------------
        # Handle anything before Part No
        # --------------------------------------------------

        prefix = buffer[:match.start()]

        # Remove any NoRead messages that appeared
        # before the record
        if "NoRead" in prefix:

            count = prefix.count("NoRead")

            for _ in range(count):
                print("IGNORED: NoRead")

        # --------------------------------------------------
        # EXTRACT VALUES
        # --------------------------------------------------

        part_no = match.group(
            "part_no"
        ).strip()

        serial_no = match.group(
            "serial_no"
        ).strip()

        date_time = match.group(
            "date_time"
        ).strip()

        # --------------------------------------------------
        # VALIDATION
        # --------------------------------------------------

        if not part_no:
            print("ERROR: Empty Part No")

        elif not serial_no:
            print("ERROR: Empty Serial No")

        else:

            try:

                datetime.strptime(
                    date_time,
                    "%Y-%m-%d %H:%M:%S"
                )

                print("\nQR RECORD RECEIVED")
                print("Part No     :", part_no)
                print("Serial No   :", serial_no)
                print("Date & Time :", date_time)

                process_product(
                    part_no,
                    date_time,
                    serial_no
                )

            except ValueError:

                print(
                    "ERROR: Invalid Date & Time:",
                    date_time
                )

        # --------------------------------------------------
        # REMOVE PROCESSED RECORD FROM BUFFER
        # --------------------------------------------------

        buffer = buffer[match.end():]

    return buffer


# ==================================================
# TCP SERVER
# ==================================================

HOST = "0.0.0.0"
PORT = 5000

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


print("\n---------------------------------------------")
print(" SMART CAMERA DUPLICATE DETECTION SYSTEM")
print("---------------------------------------------")
print(f"Listening on TCP port {PORT}")
print("Waiting for camera connection...")


# ==================================================
# CAMERA CONNECTION LOOP
# ==================================================

while True:

    conn, addr = server.accept()

    print("\nCamera connected:", addr)

    # One continuous TCP buffer
    buffer = ""

    try:

        while True:

            data = conn.recv(4096)

            if not data:

                print("Camera disconnected")
                break

            decoded = data.decode(
                "utf-8",
                errors="replace"
            )

            # Add new TCP bytes to existing buffer
            buffer += decoded

            # Try extracting complete QR records
            buffer = process_buffer(
                buffer
            )

    except ConnectionResetError:

        print("Camera connection reset.")

    except Exception as e:

        print(
            "Camera connection error:",
            e
        )

    finally:

        conn.close()

        print(
            "\nConnection closed."
        )

        print(
            "Waiting for camera connection..."
        )