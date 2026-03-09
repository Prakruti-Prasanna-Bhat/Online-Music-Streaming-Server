# 🎵 Secure Adaptive Music Streamer

A secure, multi-client TCP music streaming application built with raw Python sockets and SSL/TLS.  
Designed for the **Networked Applications** project — demonstrates socket programming, concurrency, adaptive streaming, QoS evaluation, and secure communication.

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
│  Thread Pool (one thread per client)                    │
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
│  Adaptive Buffer  (4KB / 8KB / 64KB based on speed)   │
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

All exchanges happen over a TLS-encrypted TCP connection. The MD5 checksum in the header allows the client to verify data integrity after transfer.

---

## Features

| Feature | Details |
|---|---|
| **Secure Transport** | SSL/TLS 1.2+ on all connections |
| **Multi-Client** | Each client gets its own thread; server tracks aggregate stats |
| **Buffer Management** | Adaptive: 4 KB / 8 KB / 64 KB based on live throughput |
| **Packet Loss Handling** | Byte-count check + MD5 integrity verify; auto-retry up to 3× |
| **Adaptive Streaming** | Buffer re-evaluated every 0.5 s mid-stream |
| **QoS Evaluation** | Latency, throughput, quality rating (Good/Fair/Poor), logged to file |
| **Performance Logging** | Per-session log (`performance_log.txt`) + server aggregate log |
| **Stress Testing** | `stress_test.py` simulates N concurrent clients with summary stats |
| **Security** | Path-traversal prevention, TLS minimum version enforcement, per-client timeout |

---

## Requirements

- Python 3.10+
- OpenSSL (for generating self-signed certificates)
- No third-party packages required

## Platform Notes

- Linux / macOS: Python command may be `python3`
- Windows: Python command is usually `python`
- Commands can be run in Terminal (Linux/macOS) or PowerShell / Windows Terminal (Windows)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Prakruti-Prasanna-Bhat/Online-Music-Streaming-Server.git
cd Online-Music-Streaming-Server
```

### 2. Generate a self-signed TLS certificate
- For Linux /macOS systems
```bash 
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key \
  -out certs/server.crt -days 365 -nodes \
  -subj "/CN=localhost"
```
- For Windows systems
```bash 
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/server.key -out certs/server.crt -days 365 -nodes -subj "/CN=localhost"
```
- Note Windows users: 
If OpenSSL is not installed, need to restart terminal after running: 
``` winget install ShiningLight.OpenSSL.Light ```
If OpenSSL is not recognized, try version:
```bash
"C:\Program Files\Git\mingw64\bin\openssl.exe" req -x509 -newkey rsa:4096 -keyout certs/server.key -out certs/server.crt -days 365 -nodes -subj "/CN=localhost"
``` 
### 3. Add songs
Copy your audio file into the `songs` folder.
- For Linux /macOS systems
```bash
mkdir -p songs
cp /path/to/your/audio.mp3 songs/
```
- For Windows systems
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

Output:
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
Enter song name (e.g., song.mp3): song.mp3

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

---

## Log Files

| File | Contents |
|---|---|
| `performance_log.txt` | Per-client: timestamp, song, latency, speed, quality, status |
| `server_performance.log` | Server-side: connections, bytes sent, active clients, errors |
| `stress_test_log.txt` | Aggregate stress test results |

---

## File Structure

```
secure-music-streamer/
├── server.py           # Multi-client TLS streaming server
├── client.py           # Adaptive streaming client
├── stress_test.py      # Concurrent client performance tester
├── certs/
│   ├── server.crt      # TLS certificate (generated)
│   └── server.key      # TLS private key (generated)
├── songs/              # Place your .mp3 / .wav files here
│   ├── sample.mp3      # Please Do not remove this file it is used for stress_test.py
└── README.md
```

---

## Performance Evaluation

Run `stress_test.py` and observe:

- **Latency** increases slightly with more concurrent clients (thread scheduling overhead)
- **Throughput per client** decreases as concurrent clients increase (shared bandwidth)
- All sessions still complete with integrity verified — no data loss under concurrent load

These results demonstrate the server scales correctly with multiple simultaneous clients.

---

## Security Notes

- All data is encrypted in transit using TLS 1.2 or higher
- Server enforces `TLSVersion.TLSv1_2` minimum — older clients are rejected
- Song filenames are sanitised with `os.path.basename()` to prevent directory traversal
- Each client connection has a 30-second receive timeout to prevent resource exhaustion
- In production: replace `CERT_NONE` in the client with a proper CA bundle and `check_hostname = True`
