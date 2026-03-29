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
HOST     = '127.0.0.1'   # loopback — connects to server running on the same machine
PORT     = 8443           # must match the port server.py is listening on
BUF_SIZE = 8192           # 8 KB recv buffer — mirrors server's BUFFER_SIZE for symmetry

# client-side TLS context — hostname check and cert verification disabled for self-signed certs
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode    = ssl.CERT_NONE

# ── Per-client result storage ──────────────────────────────────────────────────
results      = []
results_lock = threading.Lock()   # guards `results` against concurrent writes from multiple threads

def compute_md5(data: bytes) -> str:
    """Return MD5 hex digest of raw bytes — used to verify received file matches server's checksum."""
    return hashlib.md5(data).hexdigest()

# ── Single client worker ───────────────────────────────────────────────────────
def client_worker(client_id: int, song_name: str):
    """Simulates one full client session: connect → PLAY → stream → integrity check → log result."""
    result = {
        "id"         : client_id,
        "status"     : "FAIL",    # overwritten on success or specific failure type
        "latency_ms" : 0.0,       # time from connect() call to handshake completion, in ms
        "throughput" : 0.0,       # MB/s during the file streaming phase only
        "bytes"      : 0,         # total bytes received
        "integrity"  : False,     # True only when byte count and MD5 both match
    }

    try:
        raw  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(15.0)   # 15s cap for initial connection — avoids hanging on unreachable server
        conn = ssl_ctx.wrap_socket(raw, server_hostname=HOST)

        t0 = time.time()
        conn.connect((HOST, PORT))
        result["latency_ms"] = (time.time() - t0) * 1000   # connection + TLS handshake latency

        conn.sendall(f"PLAY {song_name}\n".encode())
        header = conn.recv(1024).decode().strip().split()   # expected: "OK <file_size> <md5>"

        if not header or header[0] != "OK":
            result["status"] = "SERVER_ERROR"
            conn.close()
            return

        file_size    = int(header[1])
        expected_md5 = header[2]

        # ── Fix: dynamic timeout based on file size ────────────────────────────
        # assumes worst case ~0.05 MB/s under heavy load, minimum 60s
        # fixed timeout would cause false TIMEOUT failures for large files under high concurrency
        safe_timeout = max(60.0, (file_size / (1024 * 1024)) / 0.05)
        conn.settimeout(safe_timeout)   # replaces the 15s handshake timeout for streaming phase

        data      = b""
        received  = 0
        stream_t0 = time.time()

        while received < file_size:
            chunk = conn.recv(BUF_SIZE)
            if not chunk:
                break   # server closed connection before sending all bytes
            data     += chunk
            received += len(chunk)

        elapsed              = time.time() - stream_t0
        result["bytes"]      = received
        result["throughput"] = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        conn.close()

        if received == file_size and compute_md5(data) == expected_md5:
            result["integrity"] = True
            result["status"]    = "OK"
        else:
            result["status"] = "INTEGRITY_FAIL"   # data was truncated or corrupted in transit

    except socket.timeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["status"] = f"ERROR: {e}"
    finally:
        with results_lock:
            results.append(result)   # thread-safe append — lock prevents concurrent list corruption
        tag = "✓" if result["status"] == "OK" else "✗"
        print(
            f"  [{tag}] Client {client_id:02d} | "
            f"status: {result['status']:<16} | "
            f"latency: {result['latency_ms']:6.1f} ms | "
            f"speed: {result['throughput']:.2f} MB/s"
        )

# ── Summary printer (reused for normal exit and Ctrl+C) ────────────────────────
def print_summary(total_clients, song, wall_time):
    ok        = [r for r in results if r["status"] == "OK"]
    fail      = [r for r in results if r["status"] != "OK"]
    latencies = [r["latency_ms"] for r in ok]
    speeds    = [r["throughput"] for r in ok]

    print(f"\n── Summary ────────────────────────────────────────")
    print(f"  Total clients     : {total_clients}")
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
            f"\n[{time.ctime()}] Clients: {total_clients} | Song: {song} | "
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
        t = threading.Thread(target=client_worker, args=(i, args.song), daemon=True)
        threads.append(t)

    # Launch all at once
    for t in threads:
        t.start()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print(f"\n[*] Interrupted — printing partial summary...")

    wall_time = time.time() - wall_start
    print_summary(args.clients, args.song, wall_time)

if __name__ == "__main__":
    main()
