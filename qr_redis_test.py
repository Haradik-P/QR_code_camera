import json
import redis
from datetime import datetime


# --------------------------------------------------
# REDIS CONNECTION
# --------------------------------------------------

r = redis.Redis(
    host="127.0.0.1",
    port=6379,
    decode_responses=True
)

print("Redis connected:", r.ping())


# --------------------------------------------------
# QR PARSER
# --------------------------------------------------

def parse_qr(text):
    text = text.strip()

    # Ignore camera failure output
    if not text or text == "NoRead":
        print("IGNORED: NoRead")
        return None

    try:
        data = json.loads(text)

        part_no = data["PartNo"].strip()
        date_time = data["DATEandTIME"].strip()
        serial_no = data["SerialNo"].strip()

        # Validate date/time
        datetime.strptime(
            date_time,
            "%Y-%m-%d %H:%M:%S"
        )

        return {
            "part_no": part_no,
            "date_time": date_time,
            "serial_no": serial_no
        }

    except json.JSONDecodeError:
        print("ERROR: Invalid JSON")
        print("Received:", text)

    except KeyError as e:
        print(f"ERROR: Missing field {e}")

    except ValueError:
        print("ERROR: Invalid DATEandTIME")

    return None


# --------------------------------------------------
# REDIS PRODUCT LOGIC
# --------------------------------------------------

def process_product(part_no, date_time, serial_no):

    # Duplicate identity is:
    # Part No + Serial Number
    product_key = f"product:{part_no}:{serial_no}"

    # -------------------------
    # DUPLICATE
    # -------------------------

    if r.exists(product_key):

        r.hincrby(
            product_key,
            "duplicate_count",
            1
        )

        r.hset(
            product_key,
            mapping={
                "last_seen": date_time
            }
        )

        r.incr("stats:duplicate_count")
        r.incr("stats:total_scans")

        status = "INVALID"

        print("\n🚨 DUPLICATE DETECTED")
        print("Part No     :", part_no)
        print("Serial No   :", serial_no)
        print("Date & Time :", date_time)

    # -------------------------
    # NEW PRODUCT
    # -------------------------

    else:

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

        # Count this product under its Part No
        r.hincrby(
            "part_counts",
            part_no,
            1
        )

        r.incr("stats:valid_count")
        r.incr("stats:total_scans")

        status = "VALID"

        print("\n✅ VALID PRODUCT")
        print("Part No     :", part_no)
        print("Serial No   :", serial_no)
        print("Date & Time :", date_time)

    # --------------------------------------------------
    # SAVE EVERY SCAN
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

    return status


# --------------------------------------------------
# COMPLETE PROCESS
# --------------------------------------------------

def process_qr(text):

    qr = parse_qr(text)

    if qr is None:
        return

    process_product(
        qr["part_no"],
        qr["date_time"],
        qr["serial_no"]
    )


# --------------------------------------------------
# TEST DATA
# --------------------------------------------------

samples = [

    # VALID
    '{"PartNo":"PN-1026","DATEandTIME":"2026-09-25 12:25:00","SerialNo":"SN-5026"}',

    # VALID - same Part No, different Serial
    '{"PartNo":"PN-1026","DATEandTIME":"2026-09-25 12:26:00","SerialNo":"SN-5027"}',

    # VALID - different Part No, same Serial
    '{"PartNo":"PN-1030","DATEandTIME":"2026-09-25 12:27:00","SerialNo":"SN-5026"}',

    # INVALID - same Part No + same Serial
    '{"PartNo":"PN-1026","DATEandTIME":"2026-09-25 12:28:00","SerialNo":"SN-5026"}',

    # Ignore
    'NoRead'
]


for sample in samples:
    process_qr(sample)