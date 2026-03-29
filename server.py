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
HOST        = '0.0.0.0'        # listen on all network interfaces
PORT        = 8443              # using 8443 to avoid requiring root privileges (443 needs root)
BUFFER_SIZE = 8192              # 8 KB chunk size — used for file reads, MD5, and socket sends
SONGS_DIR   = "songs"           # all streamable files must live inside this folder
LOG_FILE    = "server_performance.log"

# ── Logging ────────────────────────────────────────────────────────────────────
# Writes timestamped logs to both the terminal and a persistent log file simultaneously
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
# Fix Windows terminal encoding for special characters
import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
log = logging.getLogger(__name__)

# ── Global Stats (thread-safe) ─────────────────────────────────────────────────
# These counters are shared across all client threads — QoS aggregate metrics
stats_lock       = threading.Lock()   # prevents race conditions on shared counters
active_clients   = 0
total_sessions   = 0
total_bytes_sent = 0

def update_stats(delta_clients, bytes_sent=0):
    """Thread-safe update of global server stats. Pass +1 on connect, -1 on disconnect."""
    global active_clients, total_sessions, total_bytes_sent
    with stats_lock:   # only one thread can update at a time
        active_clients   += delta_clients
        total_bytes_sent += bytes_sent
        if delta_clients > 0:
            total_sessions += 1   # only increment on new connection, not on disconnect
        cur = active_clients
    return cur   # return snapshot taken while lock was held (consistent value)

# ── SSL Context ────────────────────────────────────────────────────────────────
def build_ssl_context():
    """Create a server-side TLS context — loads certificate/key and enforces TLS 1.2+."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile="certs/server.crt", keyfile="certs/server.key")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # enforce modern TLS only
    return ctx

# ── Helpers ────────────────────────────────────────────────────────────────────
def compute_md5(filepath):
    """Compute MD5 hash of a file in chunks — avoids loading entire file into RAM."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(BUFFER_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()   # returns 32-char hex string sent to client for integrity check

def safe_song_path(song_name):
    """Resolve path and block directory-traversal attempts."""
    safe_name  = os.path.basename(song_name)    # strip any directory components (e.g. ../../etc)
    full_path  = os.path.realpath(os.path.join(SONGS_DIR, safe_name))
    songs_root = os.path.realpath(SONGS_DIR)
    # ensure resolved path is still inside the songs directory
    if not full_path.startswith(songs_root + os.sep):
        return None   # path escaped songs dir — reject
    return full_path

# ── Client Handler ─────────────────────────────────────────────────────────────
def handle_client(conn: ssl.SSLSocket, addr):
    """Runs in its own thread for each connected client. Handles one full streaming session."""
    cur_clients   = update_stats(+1)   # register new client in global stats
    session_start = time.time()        # track total session duration for QoS
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
        # Custom application-layer protocol: client must send exactly "PLAY <songname>"
        parts = request.split(" ", 1)   # split on first space only — handles filenames with spaces
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
        checksum  = compute_md5(file_path)   # pre-compute MD5 for integrity check on client side

        # Adaptive timeout: larger files get more time — assumes worst-case 0.05 MB/s minimum speed
        safe_timeout = max(60.0, (file_size / (1024 * 1024)) / 0.05)
        conn.settimeout(safe_timeout)   # overrides the initial 30s timeout set in main()

        # Header: OK <file_size> <md5>
        conn.sendall(f"OK {file_size} {checksum}\n".encode())
        log.info(f"[*] Streaming '{song_name}' ({file_size/1024:.1f} KB) → {addr}")

        # ── Stream ────────────────────────────────────────────────────────────
        # Buffer management: file is read and sent in BUFFER_SIZE chunks — never fully loaded into RAM
        stream_start = time.time()
        with open(file_path, "rb") as f:
            while chunk := f.read(BUFFER_SIZE):   # walrus operator: read + assign in one step
                try:
                    conn.sendall(chunk)   # sendall() guarantees entire chunk is sent (unlike send())
                    bytes_sent += len(chunk)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    # Packet loss / client drop handling — log and exit this thread cleanly
                    log.warning(f"[!] {addr} dropped mid-stream after {bytes_sent} bytes.")
                    return

        # ── QoS metric: per-session throughput ────────────────────────────────
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
        # Always runs — guarantees socket is closed and stats are updated even if an exception occurred
        try:
            conn.close()
        except Exception:
            pass
        elapsed_session = time.time() - session_start
        cur = update_stats(-1, bytes_sent)   # deregister client, add bytes to global total
        log.info(
            f"[-] {addr} gone | session {elapsed_session:.1f}s | "
            f"active: {cur} | total sessions: {total_sessions} | "
            f"total sent: {total_bytes_sent / 1024:.1f} KB"
        )

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(SONGS_DIR, exist_ok=True)   # create songs/ folder if it doesn't exist
    ssl_ctx = build_ssl_context()

    # AF_INET = IPv4, SOCK_STREAM = TCP (reliable, ordered, connection-oriented)
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # allow reuse of port immediately after restart
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)   # detect dead peers
    server_sock.bind((HOST, PORT))
    server_sock.listen(10)   # queue up to 10 pending connections before refusing

    # Wrap plain TCP socket with TLS — all connections hereafter are encrypted
    secure_server = ssl_ctx.wrap_socket(server_sock, server_side=True)
    secure_server.settimeout(1.0)   # allows Ctrl+C to be caught every 1s
    log.info(f"[*] Secure server on {HOST}:{PORT}  (TLS 1.2+, multi-client ready)")
    log.info("[*] Press Ctrl+C to stop the server.")

    while True:
        try:
            client_conn, client_addr = secure_server.accept()
            client_conn.settimeout(30.0)   # per-client recv timeout — client has 30s to send PLAY command
        except ssl.SSLError as e:
            log.warning(f"[SSL] Handshake failed: {e}")
            continue   # bad handshake — skip this client, keep accepting others
        except socket.timeout:
            continue   # no connection in last 1s, loop again (allows Ctrl+C)
        except KeyboardInterrupt:
            log.info("[*] Server shutting down.")
            break
        except Exception as e:
            log.error(f"[!] Accept error: {e}")
            continue

        # Spawn a new daemon thread per client — daemon=True means threads die when main exits
        threading.Thread(
            target=handle_client,
            args=(client_conn, client_addr),
            daemon=True
        ).start()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Server stopped.")
