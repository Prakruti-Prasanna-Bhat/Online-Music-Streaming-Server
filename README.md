# 🎵 Secure Adaptive Music Streamer

![Python](https://img.shields.io/badge/language-Python-blue)
![Sockets](https://img.shields.io/badge/networking-TCP%20Sockets-green)
![Security](https://img.shields.io/badge/security-TLS%201.2-orange)
![Concurrency](https://img.shields.io/badge/concurrency-Multi--Client-purple)

A secure, multi-client TCP music streaming application built with raw Python sockets and SSL/TLS.  
Developed for the **Networked Applications** course project — demonstrates socket programming, concurrency, adaptive streaming logic, QoS evaluation, and secure communication.

---

## Problem Statement

This project implements an Online Music Streaming Server that streams audio files efficiently to multiple concurrent clients over a secure TCP connection.

The system demonstrates:
- Low-level socket communication (creation, binding, listening, connection handling, data transmission)
- Encrypted transport using TLS 1.2+
- Concurrent multi-client handling via a thread-per-client model
- Custom application-level protocol design
- Performance evaluation under concurrent load

---

## Objectives

- Implement secure communication using TLS over raw TCP sockets
- Support multiple concurrent clients using a thread-per-client architecture
- Design a simple application-level protocol for requesting and streaming audio files
- Measure system performance under concurrent client load
- Ensure reliable file transfer using integrity verification mechanisms

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        SERVER                           │
│                                                         │
│  TCP Socket (bind → listen → accept)                    │
│       │                                                 │
│  SSL/TLS Wrap (TLS 1.2+)                                │
│       │                                                 │
│  Thread-per-client concurrency model                    │
│    ├── Client 1 Handler                                 │
│    ├── Client 2 Handler  ← concurrent                   │
│    └── Client N Handler                                 │
│                                                         │
│  Shared Stats (thread-safe lock)                        │
│    active_clients | total_sessions | total_bytes_sent   │
└─────────────────────────────────────────────────────────┘
             │  TLS-encrypted TCP  │
┌────────────▼─────────────────────▼────────────────────┐
│                       CLIENT                          │
│                                                       │
│  TCP Socket → SSL Wrap → connect()                    │
│  Send:  PLAY <song_name>                              │
│  Recv:  OK <file_size> <md5_checksum>  (header)       │
│  Recv:  <binary audio data>            (stream)       │
│                                                       │
│  Adaptive Streaming Buffer  (4KB / 8KB / 64KB based on speed)   │
│  Integrity Check  (byte count + MD5 verify)           │
│  Auto-Retry       (up to 3 attempts on failure)       │
│  QoS Metrics      (latency, throughput, quality)      │
└───────────────────────────────────────────────────────┘
```

### Protocol Design

```
Client → Server:   PLAY <song_name>\n
Server → Client:   OK <file_size> <md5_hex>\n       (success)
                   ERROR: <reason>\n                (failure)
Server → Client:   <raw binary chunks until EOF>
```

All exchanges happen over a TLS-encrypted TCP connection. The MD5 checksum in the header allows the client to verify data integrity after the full transfer completes.

### Socket Implementation Overview

The application uses low-level Python socket APIs directly — no high-level networking frameworks are involved.

**Server:**
- `socket()` — create a TCP socket (`AF_INET`, `SOCK_STREAM`)
- `bind()` — bind to `0.0.0.0:8443` (accept connections on all interfaces)
- `listen()` — listen with a backlog of 10 pending connections
- `accept()` — block and accept each incoming client connection
- Each accepted connection is wrapped with TLS and dispatched to a new thread

**Client:**
- `socket()` — create a TCP socket
- `connect()` — connect to the server's IP and port
- TLS handshake performed via Python's `ssl` module
- `sendall()` — transmit the PLAY request
- `recv()` — receive the response header and audio stream

### Design Decisions

- **TCP vs UDP:** TCP was chosen because it guarantees reliable, ordered delivery of audio data without requiring custom packet reassembly or loss-recovery logic at the application layer.

- **Thread-per-client model:** Each client is handled in a dedicated thread, giving each connection an independent execution context. A `threading.Lock` protects shared server statistics across threads.

- **TLS encryption:** TLS 1.2+ secures both the control header and the audio data stream, preventing interception or tampering in transit.

- **Adaptive buffer sizing:** The client re-evaluates throughput every 0.5 seconds and adjusts its receive buffer (4 KB / 8 KB / 64 KB) to match network conditions, reducing stalls on slow links and maximising throughput on fast ones.

- **MD5 integrity check:** After transfer, the client recomputes the MD5 of the saved file and compares it to the server's checksum. A mismatch triggers an automatic retry.

---

## Features

| Feature | Details |
|---|---|
| **Secure Transport** | SSL/TLS 1.2+ on all connections |
| **Multi-Client** | Each client gets its own thread; server tracks aggregate stats |
| **Buffer Management** | Adaptive Receive Buffer: 4 KB / 8 KB / 64 KB based on measured throughput |
| **Packet Loss Handling** | Byte-count check + MD5 integrity verify; auto-retry up to 3× |
| **Adaptive Streaming Logic** | Client dynamically adjusts receive buffer size based on measured throughput |
| **QoS Evaluation** | Latency, throughput, quality rating (Good / Fair / Poor), logged to file |
| **Performance Logging** | Per-session log (`performance_log.txt`) + server aggregate log |
| **Stress Testing** | `stress_test.py` simulates N concurrent clients with summary stats |
| **Security** | Path-traversal prevention, TLS minimum version enforcement, per-client timeout |

---

## Optimizations and Edge Case Handling

The following issues were identified during testing and addressed:

| Issue | Fix |
|---|---|
| Abrupt client disconnection mid-stream | `BrokenPipeError` / `ConnectionResetError` caught per-chunk; session cleaned up gracefully |
| SSL handshake failure from bad client | Caught at `accept()` loop — server logs and continues without crashing |
| Invalid or path-traversal filenames | `os.path.basename()` + `realpath()` check blocks any `../` traversal attempts |
| Slow/unresponsive clients | 30-second per-client receive timeout prevents thread exhaustion |
| Incomplete transfer | Byte count compared to expected file size; triggers retry if short |
| Corrupted data in transit | MD5 checksum verified after transfer; corrupted file deleted and retried |
| Stale TCP connections | `SO_KEEPALIVE` enabled on server socket to detect dead peers |

---

## Requirements

- **Implementation Language:** Python (using only the standard library: `socket`, `ssl`, `threading`, `hashlib`)
- Python 3.10+
- OpenSSL (for generating self-signed certificates)
- No third-party packages required

## Platform Notes

- Linux / macOS: use `python3` command
- Windows: use `python` command
- Commands can be run in Terminal (Linux/macOS) or PowerShell / Windows Terminal (Windows)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Prakruti-Prasanna-Bhat/Online-Music-Streaming-Server.git
cd Online-Music-Streaming-Server
```

### 2. Generate a self-signed TLS certificate

**Linux / macOS:**
```bash
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key \
  -out certs/server.crt -days 365 -nodes \
  -subj "/CN=localhost"
```

**Windows:**
```bash
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key -out certs/server.crt -days 365 -nodes -subj "/CN=localhost"
```

> **Windows note:** If OpenSSL is not installed, run `winget install ShiningLight.OpenSSL.Light` and restart the terminal. If still not recognised, try:
> ```bash
> "C:\Program Files\Git\mingw64\bin\openssl.exe" req -x509 -newkey rsa:4096 -keyout certs/server.key -out certs/server.crt -days 365 -nodes -subj "/CN=localhost"
> ```

### 3. Add songs

**Linux / macOS:**
```bash
mkdir -p songs
cp /path/to/your/audio.mp3 songs/
```

**Windows:**
```bash
mkdir songs
copy "C:\path\to\your\audio.mp3" songs\
```

---

## Usage

### Start the server
```bash
python server.py
```
```
2025-01-01 12:00:00 [INFO] [*] Secure server on 0.0.0.0:8443  (TLS 1.2+, multi-client ready)
```

### Stream a song (single client)
```bash
python client.py
```
```
══════════════════════════════════════
   Secure Adaptive Music Streamer
══════════════════════════════════════
Enter song name (e.g., song.mp3): sample.mp3

── Attempt 1/3 ──────────────────────
[QoS] Connected  |  latency: 2.3 ms
[*] File size: 4.21 MB
[*] Progress: 100.0%  (4415832/4415832 bytes)
[QoS] Throughput : 3.84 MB/s
[QoS] Received   : 4415832/4415832 bytes
[QoS] Link quality: Good
[✓] Integrity check passed.
```

### Run a multi-client stress test
```bash
python stress_test.py --song sample.mp3 --clients 10
```
```
══════════════════════════════════════════════════
  Stress Test: 10 concurrent clients → 'sample.mp3'
══════════════════════════════════════════════════
  [✓] Client 01 | status: OK               | latency:    2.1 ms | speed: 3.82 MB/s
  [✓] Client 02 | status: OK               | latency:    2.4 ms | speed: 3.71 MB/s
  ...
── Summary ────────────────────────────────────────
  Total clients     : 10
  Successful        : 10
  Failed            : 0
  Wall-clock time   : 1.43 s
  Avg latency       : 2.3 ms
  Min / Max latency : 1.9 / 3.1 ms
  Avg throughput    : 3.76 MB/s
  Min / Max speed   : 3.61 / 3.91 MB/s
```

### Running server and client on separate machines

1. Find the server machine's IP address:
   - **Windows:** `ipconfig` → look for **Wireless LAN adapter Wi-Fi → IPv4 Address**
   - **macOS/Linux:** `ipconfig getifaddr en0`

2. On the **client machine**, open `client.py` and `stress_test.py` and update:
   ```python
   HOST = '192.168.x.x'   # replace with server machine's IP
   ```

3. Ensure both machines are on the same network and port 8443 is not blocked by a firewall.

---

## Performance Evaluation

Run `stress_test.py` with increasing client counts to observe scalability:

```bash
python stress_test.py --song sample.mp3 --clients 5
python stress_test.py --song sample.mp3 --clients 10
python stress_test.py --song sample.mp3 --clients 20
```

Expected observations:
- **Latency** increases slightly with more concurrent clients due to thread scheduling overhead
- **Throughput per client** decreases as more clients share available bandwidth
- All sessions complete with integrity verified — no data loss under concurrent load
- Results are saved cumulatively in `stress_test_log.txt` for comparison across runs

These results demonstrate that the system maintains reliable and secure streaming performance even under increased concurrent load.

---

## Log Files

| File | Contents |
|---|---|
| `performance_log.txt` | Per-client: timestamp, song, latency, speed, quality, status |
| `server_performance.log` | Server-side: connections, bytes sent, active clients, errors |
| `stress_test_log.txt` | Aggregate stress test results across all runs |

---

## File Structure

```
secure-music-streamer/
├── server.py             # Multi-client TLS streaming server
├── client.py             # Adaptive streaming client
├── stress_test.py        # Concurrent client performance tester
├── certs/
│   ├── server.crt        # TLS certificate (generated locally, git-ignored)
│   └── server.key        # TLS private key (generated locally, git-ignored)
├── songs/
│   └── sample.mp3        # Place audio files here
├── .gitignore            # Excludes certs, logs, streamed files
└── README.md
```

---

## Security Notes

- All data is encrypted in transit using TLS 1.2 or higher
- Server enforces `TLSVersion.TLSv1_2` minimum — older clients are rejected
- Song filenames are sanitised with `os.path.basename()` to block directory traversal attempts such as `../`
- Each client connection has a 30-second receive timeout to prevent resource exhaustion
- In production: replace `CERT_NONE` in the client with a proper CA bundle and set `check_hostname = True`
