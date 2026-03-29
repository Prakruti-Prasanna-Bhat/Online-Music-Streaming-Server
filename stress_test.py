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

import socket       # For creating TCP client sockets
import ssl          # For wrapping sockets with TLS encryption
import threading    # For spawning and managing concurrent client threads
import time         # For measuring latency, throughput, and wall-clock time
import hashlib      # For computing MD5 checksums to verify data integrity
import argparse     # For parsing command-line arguments (--song, --clients)
import os           # Imported for potential OS-level utilities (e.g., path ops)

# ── Configuration ──────────────────────────────────────────────────────────────
HOST     = '127.0.0.1'  # Server address — loopback (localhost) for local testing
PORT     = 8443          # Port the TLS server listens on (matches server.py)
BUF_SIZE = 8192          # Receive buffer size in bytes per recv() call (8 KB chunks)

# Create a TLS context for all client connections
# check_hostname=False and CERT_NONE allow self-signed certs (dev/testing environment)
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode    = ssl.CERT_NONE

# ── Per-client result storage ──────────────────────────────────────────────────
# Shared list where each thread appends its result dict after completing
results      = []
# Lock to prevent race conditions when multiple threads write to `results` simultaneously
results_lock = threading.Lock()

def compute_md5(data: bytes) -> str:
    """Compute and return the MD5 hex digest of the given byte string.
    Used to verify that received file data matches the server's checksum."""
    return hashlib.md5(data).hexdigest()

# ── Single client worker ───────────────────────────────────────────────────────
def client_worker(client_id: int, song_name: str):
    """
    Simulates a single client session:
      1. Opens a TLS connection to the server
      2. Requests the specified song via the PLAY protocol
      3. Streams all bytes and measures throughput
      4. Verifies integrity via MD5 checksum
      5. Records results (status, latency, speed, bytes) to the shared list
    """

    # Initialize result dict with defaults; updated as the session progresses
    result = {
        "id"         : client_id,
        "status"     : "FAIL",      # Will be overwritten on success or specific error
        "latency_ms" : 0.0,         # Time (ms) from connect() call to connection established
        "throughput" : 0.0,         # Transfer speed in MB/s for the file payload
        "bytes"      : 0,           # Total bytes received from the server
        "integrity"  : False,       # True only if received bytes == file_size AND MD5 matches
    }

    try:
        # Create a raw TCP socket (IPv4, stream-based)
        raw  = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(15.0)  # 15-second timeout for the initial connection handshake
        # Wrap the raw socket with TLS using the configured ssl_ctx
        conn = ssl_ctx.wrap_socket(raw, server_hostname=HOST)

        # Record time immediately before connect to measure connection latency
        t0 = time.time()
        conn.connect((HOST, PORT))
        # latency_ms = time from initiating connect() to TLS handshake completion
        result["latency_ms"] = (time.time() - t0) * 1000

        # Send the PLAY request with the song filename, newline-terminated
        conn.sendall(f"PLAY {song_name}\n".encode())
        # Read the server's response header (expected: "OK <file_size> <md5>")
        header = conn.recv(1024).decode().strip().split()

        # If the server didn't respond with "OK", treat it as a server-side error
        if not header or header[0] != "OK":
            result["status"] = "SERVER_ERROR"
            conn.close()
            return

        # Parse metadata from the OK header
        file_size    = int(header[1])   # Total bytes the server will send
        expected_md5 = header[2]        # MD5 checksum of the full file for integrity check

        # ── Fix: dynamic timeout based on file size ────────────────────────────
        # assumes worst case ~0.05 MB/s under heavy load, minimum 60s
        # Under stress (many concurrent clients), throughput may drop significantly.
        # A fixed timeout risks premature disconnection for large files, so we
        # compute a safe minimum based on worst-case bandwidth (0.05 MB/s).
        safe_timeout = max(60.0, (file_size / (1024 * 1024)) / 0.05)
        conn.settimeout(safe_timeout)   # Override the earlier 15s timeout for streaming phase

        data      = b""     # Accumulates all received bytes for MD5 verification
        received  = 0       # Running count of bytes received so far
        stream_t0 = time.time()  # Timestamp to measure streaming duration (for throughput)

        # Keep receiving until we've collected exactly file_size bytes
        while received < file_size:
            chunk = conn.recv(BUF_SIZE)  # Read up to BUF_SIZE bytes at a time
            if not chunk:
                break           # Server closed connection prematurely
            data     += chunk
            received += len(chunk)

        elapsed           = time.time() - stream_t0   # Total time spent receiving the file
        result["bytes"]   = received
        # Throughput = total MB received divided by elapsed seconds
        result["throughput"] = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0

        conn.close()  # Cleanly shut down the TLS connection after streaming

        # Integrity check: both byte count and MD5 must match the server's values
        if received == file_size and compute_md5(data) == expected_md5:
            result["integrity"] = True
            result["status"]    = "OK"      # Full success
        else:
            result["status"] = "INTEGRITY_FAIL"  # Data was corrupted or truncated

    except socket.timeout:
        # Socket timed out — server was too slow or unresponsive under load
        result["status"] = "TIMEOUT"
    except Exception as e:
        # Catch-all for unexpected errors (connection refused, SSL errors, etc.)
        result["status"] = f"ERROR: {e}"
    finally:
        # Always append to shared results, regardless of success or failure
        with results_lock:
            results.append(result)
        # Print a one-line summary for this client; ✓ = success, ✗ = any failure
        tag = "✓" if result["status"] == "OK" else "✗"
        print(
            f"  [{tag}] Client {client_id:02d} | "
            f"status: {result['status']:<16} | "
            f"latency: {result['latency_ms']:6.1f} ms | "
            f"speed: {result['throughput']:.2f} MB/s"
        )

# ── Summary printer (reused for normal exit and Ctrl+C) ────────────────────────
def print_summary(total_clients, song, wall_time):
    """
    Aggregates results from all client threads and prints a formatted summary.
    Also appends a one-line log entry to stress_test_log.txt for record-keeping.
    Called both on normal completion and on KeyboardInterrupt (partial results).
    """
    # Separate successful and failed clients for independent statistics
    ok        = [r for r in results if r["status"] == "OK"]
    fail      = [r for r in results if r["status"] != "OK"]
    latencies = [r["latency_ms"] for r in ok]   # Only count latency for successful clients
    speeds    = [r["throughput"] for r in ok]    # Only count throughput for successful clients

    print(f"\n── Summary ────────────────────────────────────────")
    print(f"  Total clients     : {total_clients}")
    print(f"  Successful        : {len(ok)}")
    print(f"  Failed            : {len(fail)}")
    print(f"  Wall-clock time   : {wall_time:.2f} s")   # Total elapsed time from first thread start

    # Only print latency/throughput stats if at least one client succeeded
    if ok:
        print(f"  Avg latency       : {sum(latencies)/len(latencies):.1f} ms")
        print(f"  Min / Max latency : {min(latencies):.1f} / {max(latencies):.1f} ms")
        print(f"  Avg throughput    : {sum(speeds)/len(speeds):.2f} MB/s")
        print(f"  Min / Max speed   : {min(speeds):.2f} / {max(speeds):.2f} MB/s")

    # Append a compact summary line to the persistent log file
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
    # Set up CLI argument parsing for flexible test configuration
    parser = argparse.ArgumentParser(description="Multi-client stress tester")
    parser.add_argument("--song",    default="song.mp3", help="Song to request")
    parser.add_argument("--clients", type=int, default=5, help="Number of concurrent clients")
    args = parser.parse_args()

    print(f"\n══════════════════════════════════════════════════")
    print(f"  Stress Test: {args.clients} concurrent clients → '{args.song}'")
    print(f"══════════════════════════════════════════════════")

    threads    = []
    wall_start = time.time()  # Start the wall-clock before any threads are created

    # Create one thread per client; daemon=True ensures threads don't block process exit
    for i in range(1, args.clients + 1):
        t = threading.Thread(target=client_worker, args=(i, args.song), daemon=True)
        threads.append(t)

    # Launch all threads as close together as possible to maximize concurrency
    for t in threads:
        t.start()

    try:
        # Wait for every thread to finish before printing the summary
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        # If the user hits Ctrl+C, print whatever partial results are available
        print(f"\n[*] Interrupted — printing partial summary...")

    wall_time = time.time() - wall_start   # Total test duration including all threads
    print_summary(args.clients, args.song, wall_time)

if __name__ == "__main__":
    main()
