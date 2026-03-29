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
import math
import threading
import tkinter as tk

# ── Configuration ──────────────────────────────────────────────────────────────
HOST        = '127.0.0.1'   # server address — localhost for same-machine testing
PORT        = 8443
MAX_RETRIES = 3              # number of times to retry a failed stream before giving up

# Buffer sizes for adaptive streaming — chosen based on measured throughput tier
BUF_SLOW   = 4_096    # < 0.5 MB/s  — very slow link
BUF_MED    = 8_192    # 0.5–1.5 MB/s
BUF_FAST   = 65_536   # > 1.5 MB/s  — large chunks reduce recv() call overhead on fast links

# ── SSL Context ────────────────────────────────────────────────────────────────
# Client-side TLS: verifies the server's certificate instead of presenting one
ssl_ctx = ssl.create_default_context()
ssl_ctx.load_verify_locations("certs/server.crt")  # verify against our known certificate
ssl_ctx.verify_mode    = ssl.CERT_REQUIRED          # reject any other certificate
ssl_ctx.check_hostname = False                      # skip hostname check — connecting by IP not domain

# ── Helpers ────────────────────────────────────────────────────────────────────
def play_file(filepath):
    """Open the file in the system default player."""
    print(f"\n[*] Opening '{filepath}' for playback...")
    if platform.system() == 'Darwin':
        os.system(f"open '{filepath}'")
    elif platform.system() == 'Windows':
        os.system(f'start "" "{filepath}"')
    else:
        os.system(f"xdg-open '{filepath}'")   # Linux default media handler

def compute_md5(filepath):
    """Compute MD5 of the saved file in chunks — used to verify integrity after download."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(BUF_MED), b""):
            h.update(chunk)
    return h.hexdigest()

def pick_buffer(throughput_mbps: float) -> int:
    """Return the best buffer size for the current measured throughput.
    Called every 0.5s during streaming to adapt recv() size to current link speed."""
    if throughput_mbps < 0.5:
        return BUF_SLOW
    if throughput_mbps < 1.5:
        return BUF_MED
    return BUF_FAST

def classify_quality(throughput_mbps: float, latency_ms: float) -> str:
    """Simple QoS rating based on throughput + latency.
    Good requires both conditions; Fair needs at least one; Poor means neither."""
    if throughput_mbps >= 1.5 and latency_ms < 200:
        return "Good"
    if throughput_mbps >= 0.5 or latency_ms < 500:
        return "Fair"
    return "Poor"

def log_performance(song, latency_ms, throughput, quality, status):
    """Append one line per session to the performance log — never overwrites previous entries."""
    with open("performance_log.txt", "a") as f:   # "a" = append mode
        f.write(
            f"{time.ctime()} | Song: {song} | "
            f"Latency: {latency_ms:.1f} ms | Speed: {throughput:.2f} MB/s | "
            f"Quality: {quality} | Status: {status}\n"
        )

# ── Core streaming function ────────────────────────────────────────────────────
def request_song(song_name: str):
    output_file = f"streamed_{song_name}"   # downloaded file saved with this name

    for attempt in range(1, MAX_RETRIES + 1):   # attempt numbers: 1, 2, 3
        print(f"\n── Attempt {attempt}/{MAX_RETRIES} ──────────────────────────")
        latency_ms = 0.0   # initialise to 0 so log_performance always has a valid value
        throughput = 0.0

        try:
            # ── Socket + TLS setup ─────────────────────────────────────────────
            # Create a fresh socket each attempt — never reuse a failed connection
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(10.0)                        # QoS: connection timeout

            conn = ssl_ctx.wrap_socket(raw_sock, server_hostname=HOST)

            # ── Connect + measure latency ──────────────────────────────────────
            # latency includes both TCP 3-way handshake and TLS handshake
            t0 = time.time()
            conn.connect((HOST, PORT))
            latency_ms = (time.time() - t0) * 1000   # convert seconds to milliseconds
            print(f"[QoS] Connected  |  latency: {latency_ms:.1f} ms")

            # ── Send request ───────────────────────────────────────────────────
            conn.sendall(f"PLAY {song_name}\n".encode())   # custom protocol: PLAY <songname>

            # ── Read response header ───────────────────────────────────────────
            # Server responds: "OK <file_size> <md5>" or "ERROR: <reason>"
            header = conn.recv(1024).decode(errors="replace").strip().split()
            if not header or header[0] != "OK":
                msg = ' '.join(header) if header else "empty response"
                print(f"[!] Server error: {msg}")
                conn.close()
                break   # server-side problem — no point retrying

            file_size    = int(header[1])
            expected_md5 = header[2]   # MD5 sent by server — compared after download
            print(f"[*] File size: {file_size / (1024*1024):.2f} MB")

            # ── Receive stream ─────────────────────────────────────────────────
            received   = 0
            buf_size   = BUF_MED          # start at medium — speed unknown at this point
            last_check = time.time()      # timestamp of last adaptive buffer re-evaluation
            stream_t0  = time.time()

            try:
                with open(output_file, "wb") as f:
                    while received < file_size:   # loop until all expected bytes are received
                        try:
                            chunk = conn.recv(buf_size)   # recv up to buf_size bytes
                        except socket.timeout:
                            # no data received within timeout — stream has stalled
                            print("\n[!] Stalled — no data received within timeout.")
                            break

                        if not chunk:
                            break   # server closed connection

                        f.write(chunk)      # write directly to disk — avoids loading full file in RAM
                        received += len(chunk)

                        # Progress bar — \r rewrites the same line in-place
                        pct = (received / file_size) * 100
                        sys.stdout.write(f"\r[*] Progress: {pct:.1f}%  ({received}/{file_size} bytes)")
                        sys.stdout.flush()

                        # ── Adaptive buffer: re-sample every 0.5 s ─────────────
                        # Measure current average speed and switch buffer tier if needed
                        now = time.time()
                        if now - last_check >= 0.5:
                            elapsed    = now - stream_t0
                            mid_speed  = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            new_buf    = pick_buffer(mid_speed)
                            if new_buf != buf_size:   # only update if tier has actually changed
                                buf_size = new_buf
                                print(f"\n[Adaptive] Speed ~{mid_speed:.2f} MB/s → buffer: {buf_size // 1024} KB")
                            last_check = now

            except KeyboardInterrupt:
                print(f"\n[*] Download cancelled by user.")
                conn.close()
                if os.path.exists(output_file):
                    os.remove(output_file)   # clean up partial file
                return

            conn.close()

            # ── QoS calculations ───────────────────────────────────────────────
            elapsed    = time.time() - stream_t0
            throughput = (received / (1024 * 1024)) / elapsed if elapsed > 0 else 0
            quality    = classify_quality(throughput, latency_ms)   # Good / Fair / Poor

            print(f"\n[QoS] Throughput : {throughput:.2f} MB/s")
            print(f"[QoS] Received   : {received}/{file_size} bytes")
            print(f"[QoS] Link quality: {quality}")

            # ── Packet-loss / integrity check ──────────────────────────────────
            # Step 1: byte count check — did we receive everything?
            if received < file_size:
                short = file_size - received
                print(f"[!] Incomplete transfer — missing {short} bytes.")
                log_performance(song_name, latency_ms, throughput, quality, "INCOMPLETE")
                if attempt < MAX_RETRIES:
                    print(f"[*] Retrying in 2 s...")
                    time.sleep(2)
                    continue   # go back to top of retry loop with a fresh connection
                else:
                    print("[!] Max retries reached.")
                    return

            # Step 2: MD5 checksum — did the bytes arrive uncorrupted?
            actual_md5 = compute_md5(output_file)
            if actual_md5 != expected_md5:
                print(f"[!] Checksum mismatch — data corrupted.")
                print(f"    expected : {expected_md5}")
                print(f"    got      : {actual_md5}")
                os.remove(output_file)   # delete corrupt file — don't leave bad data on disk
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
            # Connection-level timeout — server may be temporarily busy, worth retrying
            print(f"[!] Connection timed out (attempt {attempt}/{MAX_RETRIES}).")
            log_performance(song_name, latency_ms, throughput, "N/A", "TIMEOUT")
        except ssl.SSLError as e:
            # TLS handshake failure — certificate mismatch or protocol error, not retryable
            print(f"[!] SSL error: {e}")
            log_performance(song_name, latency_ms, throughput, "N/A", f"SSL_ERROR")
            break   # SSL errors are usually not retryable
        except ConnectionRefusedError:
            # Nothing listening on that port — server is not running, no point retrying
            print(f"[!] Server refused connection — is server.py running on {HOST}:{PORT}?")
            break
        except Exception as e:
            print(f"[!] Unexpected error: {e}")
            break

        if attempt < MAX_RETRIES:
            print(f"[*] Retrying in 2 s...")
            time.sleep(2)   # brief wait before retry — gives network time to recover

    print("[!] Transfer failed after all attempts.")

# ── Colour palette ─────────────────────────────────────────────────────────────
BG         = "#1c1c1c"
PANEL      = "#2a2a2a"
SILVER     = "#8a8a8a"
SILVER_LT  = "#b0b0b0"
SPEAKER_BG = "#111111"
AMBER      = "#e8a020"
RED_LED    = "#ff3030"
GREEN_LED  = "#22dd44"
LCD_BG     = "#1a2a18"
LCD_FG     = "#55ee55"
BTN_PLAY   = "#c8820a"
BTN_HOV    = "#e8a020"
BTN_TXT    = "#1a0a00"
BTN_DIS    = "#555555"
TEXT_MAIN  = "#d0d0d0"
TEXT_DIM   = "#666666"
GRILLE_DOT = "#222222"


class BoomboxUI:
    SONGS_DIR = "songs"   # folder to scan for tracks

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Music Streaming Server")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self._playing    = False
        self._selected   = None
        self._songs: list[str] = []
        self._vu_phase   = 0.0
        self._reel_angle = 0
        self._led_tick   = 0

        self._lcd_text   = tk.StringVar(value="-- SELECT A TRACK --")
        self._status_txt = tk.StringVar(value="READY")

        self._build()
        self._refresh_songs()
        self._animate()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self):
        chassis = tk.Frame(self.root, bg=BG, padx=16, pady=12)
        chassis.pack()

        # Brand strip
        top = tk.Frame(chassis, bg=BG)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        tk.Label(top, text="◈ Music Streaming Server", bg=BG,
                 fg=SILVER_LT, font=("Courier", 11, "bold")).pack(side="left")
        tk.Label(top, text="Made by Paras, Prakruti & Raghavendra", bg=BG,
                 fg=TEXT_DIM, font=("Courier", 8)).pack(side="right")

        # Speaker | Centre | Speaker
        self._build_speaker(chassis, col=0)
        self._build_centre(chassis, col=1)
        self._build_speaker(chassis, col=2)

        # Song list
        self._build_tracklist(chassis)

    def _build_speaker(self, parent, col):
        frame = tk.Frame(parent, bg=SPEAKER_BG, bd=3, relief="sunken",
                         width=130, height=210)
        frame.grid(row=1, column=col, padx=10, pady=4)
        frame.pack_propagate(False)

        c = tk.Canvas(frame, width=128, height=208,
                      bg=SPEAKER_BG, highlightthickness=0)
        c.pack()

        # Dot grille
        for r in range(6, 200, 10):
            for x in range(6, 122, 10):
                c.create_oval(x, r, x+4, r+4, fill=GRILLE_DOT, outline="")

        # Woofer — concentric circles simulating speaker cone
        cx, cy = 64, 104
        for rad, col_fill in [(52, "#1a1a1a"), (40, "#1e1e1e"),
                               (27, "#222"), (15, "#2a2a2a"), (6, SILVER)]:
            c.create_oval(cx-rad, cy-rad, cx+rad, cy+rad,
                          outline=SILVER, fill=col_fill, width=1)

    def _build_centre(self, parent, col):
        panel = tk.Frame(parent, bg=PANEL, bd=4, relief="raised",
                         width=250, height=210)
        panel.grid(row=1, column=col, padx=4, pady=4)
        panel.pack_propagate(False)

        # LCD
        lcd_wrap = tk.Frame(panel, bg="#0d1a0d", bd=3, relief="sunken")
        lcd_wrap.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(lcd_wrap, textvariable=self._lcd_text,
                 bg=LCD_BG, fg=LCD_FG, font=("Courier", 9, "bold"),
                 width=26, anchor="w", padx=6, pady=4).pack(fill="x")

        # VU meter
        vu_row = tk.Frame(panel, bg=PANEL)
        vu_row.pack(pady=4)
        self._vu_bars: list[tk.Canvas] = []
        for i in range(10):
            b = tk.Canvas(vu_row, width=14, height=36,
                          bg=PANEL, highlightthickness=0)
            b.grid(row=0, column=i, padx=1)
            self._vu_bars.append(b)

        # Status row
        status_row = tk.Frame(panel, bg=PANEL)
        status_row.pack(pady=2)
        self._led_cv = tk.Canvas(status_row, width=16, height=14,
                                  bg=PANEL, highlightthickness=0)
        self._led_cv.grid(row=0, column=0)
        self._led = self._led_cv.create_oval(2, 2, 12, 12,
                                              fill=GREEN_LED, outline="")
        tk.Label(status_row, textvariable=self._status_txt,
                 bg=PANEL, fg=AMBER, font=("Courier", 8, "bold")).grid(row=0, column=1, padx=4)

        # Cassette deck
        self._deck = tk.Canvas(panel, width=220, height=44,
                                bg="#1e1e1e", highlightthickness=1,
                                highlightbackground=SILVER)
        self._deck.pack(pady=6, padx=10)
        self._deck.create_rectangle(8, 6, 100, 38, outline=SILVER, fill="#141414")
        self._deck.create_rectangle(120, 6, 212, 38, outline=SILVER, fill="#141414")

        # Play button
        self._play_btn = tk.Button(
            panel, text="▶  PLAY",
            bg=BTN_PLAY, fg=BTN_TXT,
            font=("Courier", 11, "bold"),
            activebackground=BTN_HOV,
            relief="raised", bd=3,
            padx=14, pady=4,
            cursor="hand2",
            command=self._on_play
        )
        self._play_btn.pack(pady=(0, 8))
        self._play_btn.bind("<Enter>",
            lambda e: self._play_btn["state"] == "normal" and
                      self._play_btn.config(bg=BTN_HOV))
        self._play_btn.bind("<Leave>",
            lambda e: self._play_btn.config(bg=BTN_PLAY)
                      if self._play_btn["state"] == "normal" else None)

    def _build_tracklist(self, parent):
        outer = tk.Frame(parent, bg=PANEL, bd=3, relief="sunken")
        outer.grid(row=2, column=0, columnspan=3,
                   sticky="ew", padx=0, pady=(10, 0))

        hdr = tk.Frame(outer, bg=PANEL)
        hdr.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(hdr, text="◈  TRACK LIST", bg=PANEL,
                 fg=AMBER, font=("Courier", 9, "bold")).pack(side="left")
        tk.Button(hdr, text="↺ REFRESH", bg=PANEL, fg=SILVER_LT,
                  font=("Courier", 8), relief="flat", cursor="hand2",
                  command=self._refresh_songs).pack(side="right")

        list_frame = tk.Frame(outer, bg=PANEL)
        list_frame.pack(fill="both", padx=10, pady=(0, 8))

        sb = tk.Scrollbar(list_frame, orient="vertical", bg=PANEL)
        sb.pack(side="right", fill="y")

        self._listbox = tk.Listbox(
            list_frame,
            bg="#111111", fg=TEXT_MAIN,
            selectbackground=AMBER, selectforeground=BTN_TXT,
            font=("Courier", 10),
            height=5, width=56,
            activestyle="none",
            yscrollcommand=sb.set,
            highlightthickness=0, bd=0
        )
        self._listbox.pack(side="left", fill="both")
        sb.config(command=self._listbox.yview)

        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", lambda e: self._on_play())

        # ── Manual input row ───────────────────────────────────────────────────
        manual_row = tk.Frame(outer, bg=PANEL)
        manual_row.pack(fill="x", padx=10, pady=(4, 8))

        tk.Label(manual_row, text="OR TYPE SONG NAME:", bg=PANEL,
                 fg=SILVER_LT, font=("Courier", 8, "bold")).pack(side="left")

        self._manual_var = tk.StringVar()
        self._manual_entry = tk.Entry(
            manual_row,
            textvariable=self._manual_var,
            bg="#111111", fg=LCD_FG,
            insertbackground=LCD_FG,
            font=("Courier", 10),
            relief="sunken", bd=2,
            highlightthickness=1,
            highlightcolor=AMBER,
            highlightbackground=SILVER,
            width=34
        )
        self._manual_entry.pack(side="left", padx=(6, 0))
        # Enter key triggers play directly
        self._manual_entry.bind("<Return>", lambda e: self._on_play())
        # Typing clears the listbox selection and sets _selected
        self._manual_var.trace_add("write", self._on_manual_type)

    # ── Songs ──────────────────────────────────────────────────────────────────

    def _refresh_songs(self):
        self._listbox.delete(0, tk.END)
        self._songs = []
        if not os.path.isdir(self.SONGS_DIR):
            self._listbox.insert(tk.END, f"  ['{self.SONGS_DIR}/' folder not found]")
            return
        songs = sorted(f for f in os.listdir(self.SONGS_DIR) if not f.startswith("."))
        if not songs:
            self._listbox.insert(tk.END, "  [folder is empty]")
            return
        self._songs = songs
        for s in songs:
            self._listbox.insert(tk.END, f"  ♪  {s}")

    def _on_manual_type(self, *_args):
        """Called whenever the user types in the manual entry box."""
        typed = self._manual_var.get().strip()
        if typed:
            self._listbox.selection_clear(0, tk.END)   # deselect list
            self._selected = typed
            label = typed if len(typed) <= 22 else typed[:21] + "…"
            self._lcd_text.set(f">> {label}")
            self._lcd_text.set("-- SELECT A TRACK --")

    def _on_select(self, _event=None):
        sel = self._listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx < len(self._songs):
            self._selected = self._songs[idx]
            # clear any manual text so there's no confusion about what will play
            self._manual_var.set("")
            label = self._selected if len(self._selected) <= 22 else self._selected[:21] + "…"
            self._lcd_text.set(f">> {label}")

    # ── Play ───────────────────────────────────────────────────────────────────

    def _on_play(self):
        if self._playing:
            return   # ignore button press if already streaming
        if not self._selected:
            self._lcd_text.set("!! SELECT A TRACK !!")
            return

        self._playing = True
        self._play_btn.config(state="disabled", text="● STARTING TO STREAM", bg=BTN_DIS)
        self._status_txt.set("STREAMING")

        song  = self._selected
        label = song if len(song) <= 20 else song[:19] + "…"
        self._lcd_text.set(f">> {label}")

        def _run():
            try:
                request_song(song)
            except Exception as ex:
                self.root.after(0, lambda: self._lcd_text.set(f"ERR: {str(ex)[:22]}"))
            finally:
                self.root.after(0, self._on_stream_done)   # schedule UI reset on main thread

        # Run streaming in background thread — keeps the UI responsive during download
        threading.Thread(target=_run, daemon=True).start()

    def _on_stream_done(self):
        self._playing = False
        self._play_btn.config(state="normal", text="▶  PLAY", bg=BTN_PLAY)
        self._status_txt.set("READY TO STREAM")
        self._lcd_text.set("-- SELECT A TRACK --")

    # ── Animation ──────────────────────────────────────────────────────────────

    def _animate(self):
        self._vu_phase += 0.2

        if self._playing:
            # VU bars — sine wave combination simulates realistic audio level movement
            for i, bar in enumerate(self._vu_bars):
                level = max(0.05, min(1.0,
                    math.sin(self._vu_phase + i * 0.65) * 0.38 +
                    math.sin(self._vu_phase * 1.8 + i * 0.4) * 0.28 +
                    0.45))
                self._draw_vu(bar, level)

            # Spinning reels
            self._reel_angle = (self._reel_angle + 7) % 360
            self._draw_reels()

            # Blinking LED
            self._led_tick = (self._led_tick + 1) % 8
            self._led_cv.itemconfig(
                self._led,
                fill=GREEN_LED if self._led_tick < 4 else AMBER
            )
        else:
            for bar in self._vu_bars:
                self._draw_vu(bar, 0.0)
            self._led_cv.itemconfig(self._led, fill=GREEN_LED)

        self.root.after(55, self._animate)   # schedule next frame in ~55ms (~18 fps)

    def _draw_vu(self, canvas: tk.Canvas, level: float):
        canvas.delete("all")
        h = 36
        lit = int(level * h)
        canvas.create_rectangle(0, 0, 14, h, fill="#0a0a0a", outline="")
        # colour segments: green (low) → amber (mid) → red (high)
        for y in range(h, h - lit, -3):
            if y > h * 0.7:
                colour = GREEN_LED
            elif y > h * 0.4:
                colour = AMBER
            else:
                colour = RED_LED
            canvas.create_rectangle(2, y - 2, 12, y, fill=colour, outline="")

    def _draw_reels(self):
        d = self._deck
        d.delete("reels")
        for cx, cy in [(54, 22), (166, 22)]:   # two reel centres on the cassette deck
            for k in range(3):
                angle = math.radians(self._reel_angle + k * 120)   # spokes 120° apart
                x1 = cx + 12 * math.cos(angle)
                y1 = cy + 12 * math.sin(angle)
                x2 = cx - 12 * math.cos(angle)
                y2 = cy - 12 * math.sin(angle)
                d.create_line(x1, y1, x2, y2,
                               fill=SILVER, width=2, tags="reels")
            d.create_oval(cx-4, cy-4, cx+4, cy+4,
                           fill=SILVER_LT, outline="", tags="reels")


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point — launches the UI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = tk.Tk()
    BoomboxUI(root)
    root.mainloop()
