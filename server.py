import socket
import ssl
import threading
import os
import time

# --- Configuration ---
HOST = '0.0.0.0' 
PORT = 8443
BUFFER_SIZE = 8192
SONGS_DIR = "songs"

# --- SSL Setup ---
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(certfile="certs/server.crt", keyfile="certs/server.key")

def handle_client(conn, addr):
    print(f"[+] Secure connection established with {addr}")
    try:
        request = conn.recv(1024).decode().strip()
        if not request.startswith("PLAY"):
            conn.sendall(b"ERROR: Invalid Protocol\n")
            return
        
        song_name = request.split(" ")[1]
        file_path = os.path.join(SONGS_DIR, song_name)

        if not os.path.exists(file_path):
            conn.sendall(b"ERROR: File Not Found\n")
            return

        file_size = os.path.getsize(file_path)
        # Protocol: OK <file_size>
        conn.sendall(f"OK {file_size}\n".encode())
        
        time.sleep(0.1) 

        print(f"[*] Streaming {song_name} to {addr}...")
        with open(file_path, "rb") as f:
            while (chunk := f.read(BUFFER_SIZE)):
                conn.sendall(chunk)
        
        print(f"[#] Finished streaming to {addr}")

    except Exception as e:
        print(f"[!] Server Error with {addr}: {e}")
    finally:
        conn.close()

def main():
    if not os.path.exists(SONGS_DIR): os.makedirs(SONGS_DIR)
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(5)
    secure_server = context.wrap_socket(server_sock, server_side=True)
    
    print(f"[*] Secure Server listening on {PORT}")
    
    while True:
        client_conn, client_addr = secure_server.accept()
        threading.Thread(target=handle_client, args=(client_conn, client_addr), daemon=True).start()

if __name__ == "__main__":
    main()