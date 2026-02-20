"""
server.py — Secure Multi-Client Music Streaming Server
=======================================================
Uses raw TCP sockets + SSL/TLS.  Each client is handled in its own thread.
Tracks per-session and aggregate performance metrics for QoS evaluation.
"""

import socket
import ssl
import threading
import os
import time
import hashlib
import logging

# ── Configuration ──────────────────────────────────────────────────────────────
HOST        = '0.0.0.0'
PORT        = 8443
BUFFER_SIZE = 8192
SONGS_DIR   = "songs"
LOG_FILE    = "server_performance.log"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Global Stats (thread-safe) ─────────────────────────────────────────────────
stats_lock       = threading.Lock()
active_clients   = 0
total_sessions   = 0
total_bytes_sent = 0

def update_stats(delta_clients, bytes_sent=0):
    global active_clients, total_sessions, total_bytes_sent
    with stats_lock:
        active_clients   += delta_clients
        total_bytes_sent += bytes_sent
        if delta_clients > 0:
            total_sessions += 1
        cur = active_clients
    return cur

# ── SSL Context ────────────────────────────────────────────────────────────────
def build_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile="certs/server.crt", keyfile="certs/server.key")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # enforce modern TLS only
    return ctx

# ── Helpers ────────────────────────────────────────────────────────────────────
def compute_md5(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(BUFFER_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()

def safe_song_path(song_name):
    """Resolve path and block directory-traversal attempts."""
    safe_name  = os.path.basename(song_name)
    full_path  = os.path.realpath(os.path.join(SONGS_DIR, safe_name))
    songs_root = os.path.realpath(SONGS_DIR)
    if not full_path.startswith(songs_root + os.sep):
        return None
    return full_path

# ── Client Handler ─────────────────────────────────────────────────────────────
def handle_client(conn: ssl.SSLSocket, addr):
    cur_clients   = update_stats(+1)
    session_start = time.time()
    bytes_sent    = 0
    log.info(f"[+] {addr} connected  |  active clients: {cur_clients}")

    try:
        # ── Read request ───────────────────────────────────────────────────────
        try:
            request = conn.recv(1024).decode(errors="replace").strip()
        except (socket.timeout, ssl.SSLError) as e:
            conn.sendall(b"ERROR: Read timeout\n")
            log.warning(f"[!] {addr} read error: {e}")
            return

        # ── Validate protocol ──────────────────────────────────────────────────
        parts = request.split()
        if len(parts) != 2 or parts[0] != "PLAY":
            conn.sendall(b"ERROR: Invalid Protocol. Usage: PLAY <song>\n")
            log.warning(f"[!] {addr} bad request: {request!r}")
            return

        song_name = parts[1]
        file_path = safe_song_path(song_name)

        if file_path is None:
            conn.sendall(b"ERROR: Invalid filename\n")
            log.warning(f"[!] {addr} path traversal attempt: {song_name!r}")
            return

        if not os.path.isfile(file_path):
            conn.sendall(b"ERROR: File Not Found\n")
            log.warning(f"[!] {addr} requested missing file: {song_name}")
            return

        file_size = os.path.getsize(file_path)
        checksum  = compute_md5(file_path)

        # Header: OK <file_size> <md5>
        conn.sendall(f"OK {file_size} {checksum}\n".encode())
        log.info(f"[*] Streaming '{song_name}' ({file_size/1024:.1f} KB) → {addr}")

        # ── Stream ────────────────────────────────────────────────────────────
        stream_start = time.time()
        with open(file_path, "rb") as f:
            while chunk := f.read(BUFFER_SIZE):
                try:
                    conn.sendall(chunk)
                    bytes_sent += len(chunk)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    log.warning(f"[!] {addr} dropped mid-stream after {bytes_sent} bytes.")
                    return

        elapsed    = time.time() - stream_start
        throughput = (bytes_sent / (1024 * 1024)) / elapsed if elapsed > 0 else 0
        log.info(
            f"[#] Finished → {addr} | "
            f"{bytes_sent} bytes | {throughput:.2f} MB/s | {elapsed:.2f}s"
        )

    except ssl.SSLError as e:
        log.error(f"[SSL] Error with {addr}: {e}")
    except Exception as e:
        log.exception(f"[!] Unexpected error with {addr}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        elapsed_session = time.time() - session_start
        cur = update_stats(-1, bytes_sent)
        log.info(
            f"[-] {addr} gone | session {elapsed_session:.1f}s | "
            f"active: {cur} | total sessions: {total_sessions} | "
            f"total sent: {total_bytes_sent / 1024:.1f} KB"
        )

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(SONGS_DIR, exist_ok=True)
    ssl_ctx = build_ssl_context()

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,  1)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE,  1)  # detect dead peers
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)

    secure_server = ssl_ctx.wrap_socket(server_sock, server_side=True)
    log.info(f"[*] Secure server on {HOST}:{PORT}  (TLS 1.2+, multi-client ready)")

    while True:
        try:
            client_conn, client_addr = secure_server.accept()
            client_conn.settimeout(30.0)   # per-client recv timeout
        except ssl.SSLError as e:
            # Bad handshake — log and keep running
            log.warning(f"[SSL] Handshake failed: {e}")
            continue
        except KeyboardInterrupt:
            log.info("[*] Server shutting down.")
            break
        except Exception as e:
            log.error(f"[!] Accept error: {e}")
            continue

        threading.Thread(
            target=handle_client,
            args=(client_conn, client_addr),
            daemon=True
        ).start()

if __name__ == "__main__":
    main()