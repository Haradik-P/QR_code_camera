import json
from datetime import datetime


def parse_qr(text):
    text = text.strip()

    # Ignore failed camera reads
    if not text or text == "NoRead":
        print("Ignored: NoRead")
        return None

    try:
        data = json.loads(text)

        part_no = data["PartNo"].strip()
        date_time = data["DATEandTIME"].strip()
        serial_no = data["SerialNo"].strip()

        # Validate date format
        datetime.strptime(
            date_time,
            "%Y-%m-%d %H:%M:%S"
        )

        result = {
            "part_no": part_no,
            "date_time": date_time,
            "serial_no": serial_no
        }

        print("QR VALID")
        print("Part No     :", part_no)
        print("Date & Time :", date_time)
        print("Serial No   :", serial_no)

        return result

    except json.JSONDecodeError:
        print("ERROR: Invalid JSON")
        print("Received:", text)

    except KeyError as e:
        print(f"ERROR: Missing field {e}")

    except ValueError:
        print("ERROR: Invalid DATEandTIME format")

    return None


# -------------------
# TEST DATA
# -------------------

samples = [
    '{"PartNo":"PN-1026","DATEandTIME":"2026-09-25 12:25:00","SerialNo":"SN-5026"}',
    'NoRead',
    '{"PartNo":"PN-1030","DATEandTIME":"2026-09-25 12:29:00","SerialNo":"SN-5030"}'
]

for sample in samples:
    print("\n--------------------")
    parse_qr(sample)