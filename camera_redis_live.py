import socket
import redis
import winsound
import threading
import time
import re
from datetime import datetime


# ==================================================
# SETTINGS
# ==================================================

HOST = "0.0.0.0"
PORT = 5000

# After accepting one Data Matrix,
# ignore every other reading for 5 seconds.
COOLDOWN_SECONDS = 2

last_accepted_scan_time = 0


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
    print("Start Redis Docker container first.")
    raise SystemExit


# ==================================================
# VIRTUAL BUZZER
# ==================================================

def virtual_buzzer():
    """
    Virtual buzzer for duplicate detection.
    Runs for approximately 5 seconds.
    """

    try:
        start_time = time.time()

        while time.time() - start_time < 5:
            winsound.Beep(2200, 250)
            winsound.Beep(1400, 250)

    except Exception as e:
        print("Buzzer error:", e)


# ==================================================
# 5 SECOND CAMERA COOLDOWN
# ==================================================

def scan_allowed():
    """
    Allows one scan and then blocks all scans
    for COOLDOWN_SECONDS.
    """

    global last_accepted_scan_time

    current_time = time.monotonic()

    elapsed = current_time - last_accepted_scan_time

    # Still inside cooldown
    if elapsed < COOLDOWN_SECONDS:

        remaining = COOLDOWN_SECONDS - elapsed

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
        # 5 SECOND COOLDOWN CHECK
        # --------------------------------------------------

        if scan_allowed():

            print(
                "✅ Scan accepted - "
                "5 second cooldown started."
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

print(
    f"Scan cooldown: {COOLDOWN_SECONDS} seconds"
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

        print(
            "\nConnection closed."
        )

        print(
            "Waiting for camera connection..."
        )