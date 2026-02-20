# Secure Online Music Streaming Server

A TLS-secured client-server audio streaming system built using Python sockets.

This project implements a secure TCP-based music streaming service where clients can request audio files from a server and receive them over an encrypted SSL/TLS connection.

---

## Features

- TCP socket-based communication  
- SSL/TLS encrypted connection  
- Multi-client support using threading  
- Buffered file streaming  
- Basic Quality-of-Service metrics (latency and throughput)  
- Simple request protocol: `PLAY <filename>`

---

## Project Structure

```
client.py        → Streaming client  
server.py        → TLS-enabled streaming server  
certs/           → Server SSL certificate and key  
songs/           → Audio files available for streaming  
```

---

## How It Works

1. Client connects to server over TLS.  
2. Client sends: `PLAY <song_name>`  
3. Server validates request.  
4. Server responds with file size.  
5. Server streams file in buffered chunks.  
6. Client measures latency and throughput.  
7. Streamed file is saved locally.  

---

## Running the Project

### Start the Server
```
python server.py
```

### Start the Client
```
python client.py
```

When prompted, enter a valid file name (example: `sample.mp3`).

---

## Quick Testing

- Run multiple clients simultaneously to test concurrent streaming.  
- Request a non-existing file to test error handling.  
- Interrupt a client during download to observe server stability.  

---

## Requirements

- Python 3.x  
- No external libraries required (uses Python standard library only)  

---

## Notes

- This implementation is designed for local testing (localhost).  
- SSL certificates are required for secure communication.  