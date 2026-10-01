import redis
from datetime import datetime

r = redis.Redis(
    host="127.0.0.1",
    port=6379,
    decode_responses=True
)


def process_product(part_no, date_time, serial_number):

    # Exact Part No + Serial Number pair
    product_key = f"product:{part_no}:{serial_number}"

    # Check whether this exact combination already exists
    if r.exists(product_key):

        # Same Part No + Same Serial No = INVALID
        r.hincrby(product_key, "duplicate_count", 1)

        r.hset(
            product_key,
            mapping={
                "last_seen": date_time
            }
        )

        status = "INVALID"

        r.incr("stats:duplicate_count")

        print(
            f"ALERT: DUPLICATE DETECTED | "
            f"Part No: {part_no} | "
            f"Serial: {serial_number}"
        )

    else:

        # New Part No + Serial combination = VALID
        r.hset(
            product_key,
            mapping={
                "part_no": part_no,
                "serial_number": serial_number,
                "first_date_time": date_time,
                "last_seen": date_time,
                "duplicate_count": 0
            }
        )

        # Increase product count for this Part No
        r.hincrby("part_counts", part_no, 1)

        r.incr("stats:valid_count")

        status = "VALID"

        print(
            f"VALID PRODUCT | "
            f"Part No: {part_no} | "
            f"Serial: {serial_number}"
        )

    # Every scan is counted
    r.incr("stats:total_scans")

    # Save every scan, including invalid scans
    event_date = date_time[:10]

    r.xadd(
        f"scan_events:{event_date}",
        {
            "part_no": part_no,
            "date_time": date_time,
            "serial_number": serial_number,
            "status": status
        }
    )

    return status


# -----------------------
# TEST OUR FOUR CONDITIONS
# -----------------------

process_product(
    "P001",
    "2026-09-30 10:00:00",
    "S001"
)

process_product(
    "P002",
    "2026-09-30 10:01:00",
    "S001"
)

process_product(
    "P001",
    "2026-09-30 10:02:00",
    "S002"
)

process_product(
    "P001",
    "2026-09-30 10:03:00",
    "S001"
)