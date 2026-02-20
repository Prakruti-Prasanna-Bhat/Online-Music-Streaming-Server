"""
client.py — Secure Adaptive Music Streaming Client
===================================================
Connects to the streaming server over TLS, downloads a song with:
  - Adaptive buffer sizing        (adjusts mid-stream based on throughput)
  - Packet-loss / integrity check (MD5 + byte-count, with auto-retry)
  - QoS metrics                   (latency, throughput, link quality rating)
  - Retry logic                   (up to 3 attempts on failure)
"""

import socket
import ssl
import time
import sys
import os
import platform
import hashlib

# ── Configuration ──────────────────────────────────────────────────────────────
HOST       = '127.0.0.1'
PORT       = 8443
MAX_RETRIES = 3

# Buffer sizes for adaptive streaming
BUF_SLOW   = 4_096    # < 0.5 MB/s  — very slow link
BUF_MED    = 8_192    # 0.5–1.5 MB/s
BUF_FAST   = 65_536   # > 1.5 MB/s

# ── SSL Context ────────────────────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode    = ssl.CERT_NONE   # use CERT_REQUIRED + CA bundle in production

# ── Helpers ────────────────────────────────────────────────────────────────────
def play_file(filepath):
    """Open the file in the system default player."""
    print(f"\n[*] Opening '{filepath}' for playback...")
    if platform.system() == 'Darwin':
        os.system(f"open '{filepath}'")
    elif platform.system() == 'Windows':
        os.system(f"start {filepath}")
    else:
        os.system(f"xdg-open '{filepath}'")

def compute_md5(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(BUF_MED), b""):
            h.update(chunk)
    return h.hexdigest()

def pick_buffer(throughput_mbps: float) -> int:
    """Return the best buffer size for the current measured throughput."""
    if throughput_mbps < 0.5:
        return BUF_SLOW
    if throughput_mbps < 1.5:
        return BUF_MED
    return BUF_FAST

def classify_quality(throughput_mbps: float, latency_ms: float) -> str:
    """Simple QoS rating based on throughput + latency."""
    if throughput_mbps >= 1.5 and latency_ms < 200:
        return "Good"
    if throughput_mbps >= 0.5 or latency_ms < 500:
        return "Fair"
    return "Poor"

def log_performance(song, latency_ms, throughput, quality, status):
    with open("performance_log.txt", "a") as f:
        f.write(
            f"{time.ctime()} | Song: {song} | "
            f"Latency: {latency_ms:.1f} ms | Speed: {throughput:.2f} MB/s | "
            f"Quality: {quality} | Status: {status}\n"
        )

# ── Core streaming function ────────────────────────────────────────────────────
def request_song(song_name: str):
    output_file = f"streamed_{song_name}"

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} ──────────────────────────")
        latency_ms = 0.0
        throughput = 0.0

        try:
            # ── Socket + TLS setup ─────────────────────────────────────────────
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10.0)                        # QoS: connection timeout

            conn = ssl_ctx.wrap_socket(raw_sock, server_hostname=HOST)

            # ── Connect + measure latency ──────────────────────────────────────
            t0 = time.time()
            conn.connect((HOST, PORT))
            latency_ms = (time.time() - t0) * 1000
            print(f"[QoS] Connected  |  latency: {latency_ms:.1f} ms")

            # ── Send request ───────────────────────────────────────────────────
            conn.sendall(f"PLAY {song_name}\n".encode())

            # ── Read response header ───────────────────────────────────────────
            header = conn.recv(1024).decode(errors="replace").strip().split()
            if not header or header[0] != "OK":
                msg = ' '.join(header) if header else "empty response"
                print(f"[!] Server error: {msg}")
                conn.close()
                break   # server-side problem — no point retrying

            file_size    = int(header[1])
            expected_md5 = header[2]
            print(f"[*] File size: {file_size / (1024*1024):.2f} MB")

            # ── Receive stream ─────────────────────────────────────────────────
            received   = 0
            buf_size   = BUF_MED          # start at default
            last_check = time.time()
            stream_t0  = time.time()

            with open(output_file, "wb") as f:
                while received < file_size:
                    try:
                        chunk = conn.recv(buf_size)
                    except socket.timeout:
                        print("\n[!] Stalled — no data received within timeout.")
                        break

                    if not chunk:
                        break   # server closed connection

                    f.write(chunk)
                    received += len(chunk)

                    # Progress bar
                    pct = (received / file_size) * 100
                    sys.stdout.write(f"\r[*] Progress: {pct:.1f}%  ({received}/{file_size} bytes)")
                    sys.stdout.flush()

                    # ── Adaptive buffer: re-sample every 0.5 s ─────────────────
                    now = time.time()
                    if now - last_check >= 0.5:
                        elapsed    = now - stream_t0
                        mid_speed  = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                        new_buf    = pick_buffer(mid_speed)
                        if new_buf != buf_size:
                            buf_size = new_buf
                            print(f"\n[Adaptive] Speed ~{mid_speed:.2f} MB/s → buffer: {buf_size // 1024} KB")
                        last_check = now

            conn.close()

            # ── QoS calculations ───────────────────────────────────────────────
            elapsed    = time.time() - stream_t0
            throughput = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0
            quality    = classify_quality(throughput, latency_ms)

            print(f"\n[QoS] Throughput : {throughput:.2f} MB/s")
            print(f"[QoS] Received   : {received}/{file_size} bytes")
            print(f"[QoS] Link quality: {quality}")

            # ── Packet-loss / integrity check ──────────────────────────────────
            if received < file_size:
                short = file_size - received
                print(f"[!] Incomplete transfer — missing {short} bytes.")
                log_performance(song_name, latency_ms, throughput, quality, "INCOMPLETE")
                if attempt < MAX_RETRIES:
                    print(f"[*] Retrying in 2 s...")
                    time.sleep(2)
                    continue
                else:
                    print("[!] Max retries reached.")
                    return

            actual_md5 = compute_md5(output_file)
            if actual_md5 != expected_md5:
                print(f"[!] Checksum mismatch — data corrupted.")
                print(f"    expected : {expected_md5}")
                print(f"    got      : {actual_md5}")
                os.remove(output_file)
                log_performance(song_name, latency_ms, throughput, quality, "CHECKSUM_FAIL")
                if attempt < MAX_RETRIES:
                    print(f"[*] Retrying in 2 s...")
                    time.sleep(2)
                    continue
                else:
                    print("[!] Max retries reached.")
                    return

            # ── Success ────────────────────────────────────────────────────────
            print("[✓] Integrity check passed.")
            log_performance(song_name, latency_ms, throughput, quality, "OK")
            play_file(output_file)
            return

        except socket.timeout:
            print(f"[!] Connection timed out (attempt {attempt}/{MAX_RETRIES}).")
            log_performance(song_name, latency_ms, throughput, "N/A", "TIMEOUT")
        except ssl.SSLError as e:
            print(f"[!] SSL error: {e}")
            log_performance(song_name, latency_ms, throughput, "N/A", f"SSL_ERROR")
            break   # SSL errors are usually not retryable
        except ConnectionRefusedError:
            print(f"[!] Server refused connection — is it running on {HOST}:{PORT}?")
            break
        except Exception as e:
            print(f"[!] Unexpected error: {e}")
            break

        if attempt < MAX_RETRIES:
            print(f"[*] Retrying in 2 s...")
            time.sleep(2)

    print("[!] Transfer failed after all attempts.")

# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("══════════════════════════════════════")
    print("   Secure Adaptive Music Streamer")
    print("══════════════════════════════════════")
    song = input("Enter song name (e.g., song.mp3): ").strip()
    if song:
        request_song(song)
    else:
        print("[!] No song name entered.")