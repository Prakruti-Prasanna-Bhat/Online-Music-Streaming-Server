"""
stress_test.py — Multi-Client Concurrent Streaming Test
========================================================
Spawns N simultaneous clients to demonstrate and measure:
  - Concurrent client support
  - Per-client throughput and latency
  - Aggregate performance summary (rubric: Performance Evaluation)

Usage:
    python stress_test.py --song song.mp3 --clients 5
"""

import socket
import ssl
import threading
import time
import hashlib
import argparse
import os

# ── Configuration ──────────────────────────────────────────────────────────────
HOST     = '127.0.0.1'
PORT     = 8443
BUF_SIZE = 8192

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode    = ssl.CERT_NONE

# ── Per-client result storage ──────────────────────────────────────────────────
results      = []
results_lock = threading.Lock()

def compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

# ── Single client worker ───────────────────────────────────────────────────────
def client_worker(client_id: int, song_name: str):
    result = {
        "id"         : client_id,
        "status"     : "FAIL",
        "latency_ms" : 0.0,
        "throughput" : 0.0,
        "bytes"      : 0,
        "integrity"  : False,
    }

    try:
        raw  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(15.0)
        conn = ssl_ctx.wrap_socket(raw, server_hostname=HOST)

        t0 = time.time()
        conn.connect((HOST, PORT))
        result["latency_ms"] = (time.time() - t0) * 1000

        conn.sendall(f"PLAY {song_name}\n".encode())
        header = conn.recv(1024).decode().strip().split()

        if not header or header[0] != "OK":
            result["status"] = "SERVER_ERROR"
            conn.close()
            return

        file_size    = int(header[1])
        expected_md5 = header[2]

        data      = b""
        received  = 0
        stream_t0 = time.time()

        while received < file_size:
            chunk = conn.recv(BUF_SIZE)
            if not chunk:
                break
            data     += chunk
            received += len(chunk)

        elapsed           = time.time() - stream_t0
        result["bytes"]   = received
        result["throughput"] = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        conn.close()

        # Integrity check
        if received == file_size and compute_md5(data) == expected_md5:
            result["integrity"] = True
            result["status"]    = "OK"
        else:
            result["status"] = "INTEGRITY_FAIL"

    except socket.timeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["status"] = f"ERROR: {e}"
    finally:
        with results_lock:
            results.append(result)
        tag = "✓" if result["status"] == "OK" else "✗"
        print(
            f"  [{tag}] Client {client_id:02d} | "
            f"status: {result['status']:<16} | "
            f"latency: {result['latency_ms']:6.1f} ms | "
            f"speed: {result['throughput']:.2f} MB/s"
        )

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Multi-client stress tester")
    parser.add_argument("--song",    default="song.mp3", help="Song to request")
    parser.add_argument("--clients", type=int, default=5, help="Number of concurrent clients")
    args = parser.parse_args()

    print(f"\n══════════════════════════════════════════════════")
    print(f"  Stress Test: {args.clients} concurrent clients → '{args.song}'")
    print(f"══════════════════════════════════════════════════")

    threads    = []
    wall_start = time.time()

    for i in range(1, args.clients + 1):
        t = threading.Thread(target=client_worker, args=(i, args.song))
        threads.append(t)

    # Launch all at once
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    wall_time = time.time() - wall_start

    # ── Aggregate stats ────────────────────────────────────────────────────────
    ok         = [r for r in results if r["status"] == "OK"]
    fail       = [r for r in results if r["status"] != "OK"]
    latencies  = [r["latency_ms"]  for r in ok]
    speeds     = [r["throughput"]  for r in ok]

    print(f"\n── Summary ────────────────────────────────────────")
    print(f"  Total clients     : {args.clients}")
    print(f"  Successful        : {len(ok)}")
    print(f"  Failed            : {len(fail)}")
    print(f"  Wall-clock time   : {wall_time:.2f} s")

    if ok:
        print(f"  Avg latency       : {sum(latencies)/len(latencies):.1f} ms")
        print(f"  Min / Max latency : {min(latencies):.1f} / {max(latencies):.1f} ms")
        print(f"  Avg throughput    : {sum(speeds)/len(speeds):.2f} MB/s")
        print(f"  Min / Max speed   : {min(speeds):.2f} / {max(speeds):.2f} MB/s")

    # Write to log
    with open("stress_test_log.txt", "a") as f:
        f.write(
            f"\n[{time.ctime()}] Clients: {args.clients} | Song: {args.song} | "
            f"OK: {len(ok)} | Fail: {len(fail)} | "
            f"Wall: {wall_time:.2f}s"
        )
        if ok:
            f.write(
                f" | Avg latency: {sum(latencies)/len(latencies):.1f}ms"
                f" | Avg speed: {sum(speeds)/len(speeds):.2f}MB/s\n"
            )

    print(f"  Results saved to  : stress_test_log.txt")
    print(f"══════════════════════════════════════════════════\n")

if __name__ == "__main__":
    main()
