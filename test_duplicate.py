import redis
import json

r = redis.Redis(
    host="127.0.0.1",
    port=6379,
    decode_responses=True
)

# Fake QR data for testing
parameter_1 = "PART001"
parameter_2 = "BATCH001"
serial_number = "SN000001"

key = f"serial:{serial_number}"

data = {
    "parameter_1": parameter_1,
    "parameter_2": parameter_2,
    "serial_number": serial_number
}

# 1 year TTL
ONE_YEAR = 31536000

result = r.set(
    key,
    json.dumps(data),
    nx=True,
    ex=ONE_YEAR
)

if result:
    print("NEW SERIAL")
    print("Stored:", data)
else:
    print("DUPLICATE SERIAL")
    print("Existing:", json.loads(r.get(key)))

print("TTL remaining:", r.ttl(key), "seconds")