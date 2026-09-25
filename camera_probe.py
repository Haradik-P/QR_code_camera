# camera_probe.py

import socket

HOST = "0.0.0.0"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

server.bind((HOST, PORT))
server.listen(5)

print(f"Waiting for camera on TCP port {PORT}...")

while True:
    conn, addr = server.accept()

    print(f"\nCamera connected: {addr}")

    try:
        while True:
            data = conn.recv(4096)

            if not data:
                break

            # print("RAW BYTES :", repr(data))

            try:
                print("TEXT      :", data.decode("utf-8"))
            except UnicodeDecodeError:
                print("Could not decode as UTF-8")

    finally:
        conn.close()
        print("Camera disconnected")