#!/usr/bin/env python3
"""
Penerima radio FM dengan GUI untuk xSDR (dan SDR lain yang didukung SoapySDR).

Fitur:
  - pilihan input SDR (hasil SoapySDR enumerate) + mode simulasi tanpa hardware
  - pilihan output audio (aplay)
  - slider frekuensi, gain, volume
  - tampilan frekuensi gaya wsdr.io: waterfall +-500 kHz di sekitar stasiun,
    spektrum RF (PSD) dan spektrum MPX hasil demodulasi (pilot 19 kHz, stereo, RDS)
  - klik waterfall untuk pindah ke stasiun di posisi itu

Pakai:  python3 fm_gui.py            (butuh: sudo apt install python3-tk)
        python3 fm_gui.py --freq 98.7
"""

import argparse
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

import numpy as np
from scipy import signal

try:
    import SoapySDR
    from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
except ImportError:  # GUI tetap bisa jalan dalam mode simulasi
    SoapySDR = None

FS = 1.92e6            # sample rate SDR
OFFSET = 250e3         # tune 250 kHz di bawah stasiun, hindari spike DC
D1, D2 = 8, 5          # 1.92 MS/s -> 240 kS/s -> 48 kHz
AUDIO_RATE = 48000
BLOCK = 96000          # 50 ms
FMIN, FMAX = 87.5, 108.0
SIM_LABEL = "Simulasi (tanpa SDR)"


# ---------------------------------------------------------------- sumber sinyal
class SoapySource:
    """SDR asli lewat SoapySDR. Untuk xSDR gain dibagi manual ke LNA/TIA/PGA."""

    def __init__(self, kwargs, ch=0):
        # beberapa binding SoapySDR gagal/crash bila diberi dict, jadi pakai string "k=v,..."
        args = {k: str(kwargs[k]) for k in ("driver", "bus", "device", "serial") if k in kwargs}
        args = {k: v for k, v in args.items() if "," not in v and "=" not in v}
        self.dev = SoapySDR.Device(",".join(f"{k}={v}" for k, v in args.items()))
        self.driver = self.dev.getDriverKey()
        # xSDR punya dua kanal penerima: kanal 0 = port RX A, kanal 1 = port RX B
        n = self.dev.getNumChannels(SOAPY_SDR_RX)
        if ch >= n:
            raise RuntimeError(f"{self.driver} hanya punya {n} kanal RX, RX B tidak tersedia")
        self.ch = ch
        self.dev.setSampleRate(SOAPY_SDR_RX, ch, FS)
        ants = list(self.dev.listAntennas(SOAPY_SDR_RX, ch))
        if "LNAW" in ants:
            self.dev.setAntenna(SOAPY_SDR_RX, ch, "LNAW")
        self.gains = list(self.dev.listGains(SOAPY_SDR_RX, ch))
        r = self.dev.getGainRange(SOAPY_SDR_RX, ch)
        self.gain_range = (r.minimum(), r.maximum())
        self.stream = self.dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [ch])
        self.dev.activateStream(self.stream)

    def set_freq(self, hz):
        self.dev.setFrequency(SOAPY_SDR_RX, self.ch, hz)

    def set_gain(self, db):
        # setGain() total di driver usdr hanya mengatur LNA, jadi dibagi per tahap
        if {"LNA", "TIA", "PGA"} <= set(self.gains):
            lna = min(max(db, 0.0), 30.0)
            tia = min(max(db - lna, 0.0), 12.0)
            pga = min(max(db - lna - tia, -12.0), 19.0)
            for name, val in (("LNA", lna), ("TIA", tia), ("PGA", pga)):
                self.dev.setGain(SOAPY_SDR_RX, self.ch, name, val)
        else:
            self.dev.setGain(SOAPY_SDR_RX, self.ch, db)

    def read(self, buf):
        got = 0
        while got < len(buf):
            sr = self.dev.readStream(self.stream, [buf[got:]], len(buf) - got, timeoutUs=1_000_000)
            if sr.ret > 0:
                got += sr.ret
            elif sr.ret not in (-1, -4):  # -1 timeout, -4 overflow: cukup lanjut
                raise RuntimeError(f"readStream error {sr.ret}")
        return buf

    def close(self):
        self.dev.deactivateStream(self.stream)
        self.dev.closeStream(self.stream)


class SimSource:
    """Beberapa stasiun FM tiruan (nada audio berbeda) + noise, untuk mencoba GUI tanpa SDR."""

    STATIONS = {95.0e6: 440.0, 98.7e6: 1000.0, 99.0e6: 550.0, 101.1e6: 660.0, 101.4e6: 770.0,
                105.3e6: 880.0}
    gain_range = (0.0, 61.0)
    driver = "sim"

    def __init__(self):
        self.lo = 98.7e6 - OFFSET
        self.gain = 40.0
        self.n = 0
        self.t_next = time.monotonic()
        self.rng = np.random.default_rng()

    def set_freq(self, hz):
        self.lo = hz

    def set_gain(self, db):
        self.gain = db

    def read(self, buf):
        n = len(buf)
        t = (self.n + np.arange(n)) / FS
        x = (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n)) * 0.003
        for f_st, tone in self.STATIONS.items():
            off = f_st - self.lo
            if abs(off) < FS / 2 - 100e3:
                # FM: deviasi 75 kHz, nada audio
                ph = (2 * np.pi * off * t + (67.5e3 / tone) * np.sin(2 * np.pi * tone * t)
                      + (7.5e3 / 19e3) * np.sin(2 * np.pi * 19e3 * t))   # + pilot 19 kHz
                x += 0.05 * np.exp(1j * ph)
        x *= 10 ** ((self.gain - 40.0) / 20)
        buf[:] = np.clip(x.real, -1, 1) + 1j * np.clip(x.imag, -1, 1)
        self.n += n
        # jaga laju data seperti hardware sungguhan
        self.t_next += n / FS
        delay = self.t_next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self.t_next = time.monotonic()
        return buf

    def close(self):
        pass


# ---------------------------------------------------------------- DSP & audio
NFFT_RF, NFFT_MPX = 2048, 1024
SPAN = 500e3           # lebar tampilan waterfall: +-500 kHz di sekitar frekuensi tengah
BW = 200e3             # lebar kanal awal (filter 100 kHz kiri-kanan)
BW_MIN, BW_MAX = 30e3, 220e3   # batas lebar kanal (dibatasi laju 240 kS/s setelah decimate)


def psd_db(x, nfft, nseg, scale=1.0):
    """PSD rata-rata beberapa segmen, dalam dBFS (nada skala penuh = 0 dB)."""
    w = np.hanning(nfft)
    seg = x[:nfft * nseg].reshape(nseg, nfft) * w
    p = np.mean(np.abs(np.fft.fft(seg, axis=1)) ** 2, axis=0) / (w.sum() * scale) ** 2
    return 10 * np.log10(p + 1e-15)


class FMDemod:
    """Geser frekuensi tengah ke 0 Hz (untuk spektrum), lalu geser lagi sejauh offset kanal df,
    filter selebar kanal, demodulasi FM, de-emphasis 50 us, decimate ke 48 kHz.
    Sekalian menghitung spektrum RF (sebelum filter) dan spektrum MPX (sebelum de-emphasis)."""

    def __init__(self):
        self.set_channel(0.0, BW)
        self.lp2 = signal.firwin(65, 15e3, fs=FS / D1)
        self.z1 = np.zeros(len(self.lp1) - 1, complex)
        self.z2 = np.zeros(len(self.lp2) - 1)
        self.alpha = 1 - np.exp(-1 / (FS / D1 * 50e-6))
        self.zd = np.zeros(1)
        self.mix = np.exp(-2j * np.pi * OFFSET / FS * np.arange(BLOCK))
        self.step = np.exp(-2j * np.pi * OFFSET / FS * BLOCK)
        self.phase = 1 + 0j
        self.last = 1 + 0j

    def set_channel(self, df, bw):
        """Pindah kanal ke df Hz dari frekuensi tengah, dengan lebar bw Hz."""
        self.df, self.bw = df, bw
        self.lp1 = signal.firwin(65, bw / 2, fs=FS)
        self.mix2 = np.exp(-2j * np.pi * df / FS * np.arange(BLOCK))
        self.step2 = np.exp(-2j * np.pi * df / FS * BLOCK)
        self.phase2 = 1 + 0j

    def process(self, iq):
        x = iq * self.mix * self.phase
        self.phase *= self.step
        self.phase /= abs(self.phase)
        rf = np.fft.fftshift(psd_db(x, NFFT_RF, 8))
        if self.df:
            x = x * self.mix2 * self.phase2
            self.phase2 *= self.step2
            self.phase2 /= abs(self.phase2)
        x, self.z1 = signal.lfilter(self.lp1, 1, x, zi=self.z1)
        x = x[::D1]
        rssi = 10 * np.log10(np.mean(np.abs(x) ** 2) + 1e-12)   # dBFS di kanal
        d = np.angle(x * np.conj(np.r_[self.last, x[:-1]]))
        self.last = x[-1]
        mpx = psd_db(d, NFFT_MPX, 8, scale=2 * np.pi * 75e3 / (FS / D1))[:NFFT_MPX // 2]
        d, self.zd = signal.lfilter([self.alpha], [1, self.alpha - 1], d, zi=self.zd)
        a, self.z2 = signal.lfilter(self.lp2, 1, d, zi=self.z2)
        a = a[::D2] / (2 * np.pi * 75e3 / (FS / D1))
        return a, rssi, rf, mpx


class AudioSink:
    def __init__(self, device):
        cmd = ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", str(AUDIO_RATE)]
        if device and device != "default":
            cmd += ["-D", device]
        try:
            self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.p = None  # tanpa aplay: audio dibuang, GUI tetap jalan

    def write(self, a):
        if self.p and self.p.poll() is None:
            self.p.stdin.write((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())

    def close(self):
        if self.p:
            try:
                self.p.stdin.close()
            except OSError:
                pass
            self.p.wait(timeout=2)


def list_audio_devices():
    devs = ["default"]
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=3).stdout
        for card, name, dev in re.findall(r"card (\d+): \S+ \[(.+?)\], device (\d+):", out):
            devs.append(f"plughw:{card},{dev}  ({name})")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return devs


XSDR_M2, XSDR_USB = "xSDR M.2 (PCIe)", "xSDR USB"
RX_PORTS = ["RX A", "RX B"]   # port antena xSDR = kanal RX 0 / 1


def xsdr_kind(d):
    """'usb' atau 'pci' untuk perangkat usdr hasil enumerate."""
    info = " ".join(str(v) for v in d.values()).lower()
    return "usb" if "usb" in info else "pci"


def list_sdr_devices():
    """Kembalikan [(label, kwargs)]: xSDR M.2, xSDR USB, SDR lain, lalu mode simulasi.
    xSDR yang tidak terdeteksi tetap dicantumkan supaya pilihannya selalu ada."""
    found = []
    if SoapySDR is not None:
        try:
            found = [{key: k[key] for key in k.keys()} for k in SoapySDR.Device.enumerate()]
        except Exception:
            found = []
    items, others = [], []
    for kind, name in (("pci", XSDR_M2), ("usb", XSDR_USB)):
        hits = [d for d in found if d.get("driver") == "usdr" and xsdr_kind(d) == kind]
        for d in hits:
            tag = d.get("device") or d.get("serial") or ""
            items.append((f"{name}  {tag}".rstrip(), d))
        if not hits:
            items.append((f"{name}  (tidak terdeteksi)", {"driver": "usdr", "bus": kind}))
    for d in found:
        if d.get("driver") != "usdr":
            label = d.get("label") or d.get("driver", "?")
            extra = ", ".join(f"{x}={d[x]}" for x in ("bus", "device", "serial") if x in d)
            others.append((f"{label} [{d.get('driver', '?')}{', ' + extra if extra else ''}]", d))
    return items + others + [(SIM_LABEL, None)]


class Receiver(threading.Thread):
    """Thread pembaca SDR + DSP. Perubahan frekuensi/gain diterapkan di antara blok."""

    def __init__(self, source, audio_dev, freq_hz, gain_db, volume):
        super().__init__(daemon=True)
        self.src = source
        self.audio = AudioSink(audio_dev)
        self.demod = FMDemod()
        self.cmds = queue.Queue()
        self.volume = volume
        self.rssi = -120.0
        self.rf = self.mpx = None     # spektrum terbaru, diambil GUI
        self.error = None
        self.running = True
        self.src.set_freq(freq_hz - OFFSET)
        self.src.set_gain(gain_db)

    def tune(self, freq_hz):
        self.cmds.put(("freq", freq_hz))

    def gain(self, db):
        self.cmds.put(("gain", db))

    def channel(self, df, bw):
        self.cmds.put(("chan", (df, bw)))

    def run(self):
        buf = np.zeros(BLOCK, np.complex64)
        try:
            while self.running:
                while not self.cmds.empty():
                    what, val = self.cmds.get_nowait()
                    if what == "freq":
                        self.src.set_freq(val - OFFSET)
                    elif what == "chan":
                        self.demod.set_channel(*val)
                    else:
                        self.src.set_gain(val)
                a, self.rssi, self.rf, self.mpx = self.demod.process(self.src.read(buf))
                self.audio.write(a * self.volume)
        except Exception as e:  # tampilkan di status bar
            self.error = str(e)
        finally:
            self.src.close()
            self.audio.close()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- GUI
BLUE, BG, PANEL = "#3d5fa6", "#eef1f8", "#ffffff"
FONT, FONT_B, FONT_S = ("DejaVu Sans", 11), ("DejaVu Sans", 11, "bold"), ("DejaVu Sans", 8)


def make_cmap():
    """Peta warna biru tua -> cyan -> hijau -> kuning -> merah, seperti colorbar wsdr."""
    anchors = [(0.0, (0, 0, 150)), (0.25, (0, 120, 220)), (0.45, (60, 190, 160)),
               (0.6, (90, 180, 40)), (0.78, (220, 200, 40)), (1.0, (170, 20, 10))]
    t = np.linspace(0, 1, 256)
    xs = [a[0] for a in anchors]
    return np.stack([np.interp(t, xs, [a[1][i] for a in anchors]) for i in range(3)], 1).astype(np.uint8)


CMAP = make_cmap()


class Waterfall(tk.Canvas):
    """Waterfall +-SPAN di sekitar frekuensi tengah, colorbar dB di kiri, sumbu kHz di bawah.
    Kanal yang didemodulasi disorot; geser dengan mouse, tarik tepinya untuk melebarkan/menyempitkan,
    klik di luar kanal untuk memindahkannya ke sana, scroll untuk geser 5 kHz,
    klik ganda untuk menjadikan kanal sebagai frekuensi tengah."""

    EDGE = 6                 # jarak (piksel) untuk menangkap tepi kanal

    W, H = 640, 245          # ukuran gambar waterfall (muat di layar 1280x600)
    L, B = 70, 50            # margin kiri (colorbar + label) dan bawah (sumbu)

    def __init__(self, master, on_channel, on_recenter):
        super().__init__(master, width=self.L + self.W + 15, height=self.H + self.B + 15,
                         bg=PANEL, highlightthickness=0)
        self.lo, self.hi = -110.0, -10.0
        self.rows = np.zeros((self.H, self.W, 3), np.uint8)
        self.rows[:] = CMAP[0]
        self.img = tk.PhotoImage(width=self.W, height=self.H)
        self.create_image(self.L, 10, image=self.img, anchor="nw")
        self.fx = np.linspace(-SPAN, SPAN, self.W)
        self.df, self.bw = 0.0, BW
        self.band = np.abs(self.fx) <= BW / 2
        self.on_channel, self.on_recenter = on_channel, on_recenter
        self.drag = None
        self.cbar = tk.PhotoImage(width=12, height=self.H)
        self.create_image(self.L - 22, 10, image=self.cbar, anchor="nw")
        self._axes()
        self.edges = [self.create_line(0, 10, 0, self.H + 10, fill="#ffffff", width=1, dash=(3, 3))
                      for _ in range(2)]
        self.cline = self.create_line(0, 10, 0, self.H + 10, fill="#ff3030", width=2)
        self.bind("<Motion>", self._hover)
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.bind("<Double-Button-1>", lambda e: self.on_recenter())
        self.bind("<MouseWheel>", lambda e: self._wheel(1 if e.delta > 0 else -1))
        self.bind("<Button-4>", lambda e: self._wheel(1))
        self.bind("<Button-5>", lambda e: self._wheel(-1))
        self.set_channel(0.0, BW)
        self.render()

    def _axes(self):
        self.delete("axis")
        col = CMAP[np.linspace(255, 0, self.H).astype(int)]
        self.cbar.put(" ".join("{" + " ".join(["#%02x%02x%02x" % tuple(c)] * 12) + "}" for c in col))
        y0 = 10
        for db in range(int(np.ceil(self.lo / 10) * 10), int(self.hi) + 1, 10):
            y = y0 + (self.hi - db) / (self.hi - self.lo) * self.H
            self.create_text(self.L - 28, y, text=str(db), anchor="e", font=FONT_S, tags="axis")
        for k in range(-400, 401, 200):
            x = self.L + (k * 1e3 + SPAN) / (2 * SPAN) * self.W
            self.create_line(x, self.H + 10, x, self.H + 15, tags="axis")
            self.create_text(x, self.H + 25, text=str(k), font=FONT, tags="axis")
        self.create_text(self.L + self.W / 2, self.H + 45, text="Frekuensi, kHz", font=FONT, tags="axis")

    def set_range(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self._axes()

    def push(self, f_hz, p_db):
        """Tambah satu baris spektrum (f relatif stasiun, dB) di atas waterfall."""
        p = np.interp(self.fx, f_hz, p_db)
        idx = np.clip((p - self.lo) / (self.hi - self.lo) * 255, 0, 255).astype(int)
        self.rows = np.roll(self.rows, 1, axis=0)
        self.rows[0] = CMAP[idx]
        self.render()

    def render(self):
        rgb = self.rows.copy()
        rgb[:, self.band] = rgb[:, self.band] // 2 + 60      # sorot lebar kanal yang didemodulasi
        ppm = b"P6 %d %d 255\n" % (self.W, self.H) + rgb.tobytes()
        self.img.configure(data=ppm, format="PPM")

    # --- konversi piksel <-> frekuensi relatif tengah
    def _f(self, x):
        return (min(max(x, self.L), self.L + self.W) - self.L) / self.W * 2 * SPAN - SPAN

    def _x(self, f):
        return self.L + (f + SPAN) / (2 * SPAN) * self.W

    def set_channel(self, df, bw):
        """Gambar ulang sorotan kanal (dipanggil App setiap kanal berubah)."""
        self.df, self.bw = df, bw
        self.band = np.abs(self.fx - df) <= bw / 2
        for item, f in zip(self.edges, (df - bw / 2, df + bw / 2)):
            self.coords(item, self._x(f), 10, self._x(f), self.H + 10)
        self.coords(self.cline, self._x(df), 10, self._x(df), self.H + 10)
        self.render()

    def _where(self, x):
        xl, xr = self._x(self.df - self.bw / 2), self._x(self.df + self.bw / 2)
        if abs(x - xl) <= self.EDGE or abs(x - xr) <= self.EDGE:
            return "edge"
        return "inside" if xl < x < xr else "outside"

    def _hover(self, e):
        if self.drag is None:
            w = self._where(e.x)
            self.configure(cursor={"edge": "sb_h_double_arrow", "inside": "fleur"}.get(w, "crosshair"))

    def _press(self, e):
        if not self.L <= e.x < self.L + self.W or e.y > self.H + 10:
            return
        w = self._where(e.x)
        f = self._f(e.x)
        if w == "outside":                       # lompat: tengah kanal ke titik klik
            self._emit(f, self.bw)
            w = "inside"
        self.drag = ("edge", 0.0) if w == "edge" else ("move", f - self.df)

    def _move(self, e):
        if self.drag is None:
            return
        mode, grab = self.drag
        f = self._f(e.x)
        if mode == "edge":
            self._emit(self.df, 2 * abs(f - self.df))
        else:
            self._emit(f - grab, self.bw)

    def _wheel(self, sign):
        self._emit(self.df + sign * 5e3, self.bw)

    def _emit(self, df, bw):
        bw = min(max(bw, BW_MIN), BW_MAX)
        lim = SPAN - bw / 2
        df = round(min(max(df, -lim), lim) / 100) * 100      # resolusi 0,1 kHz
        self.on_channel(df, bw)


class Plot(tk.Canvas):
    """Grafik garis sederhana (PSD) dengan area terisi, sumbu dan grid."""

    def __init__(self, master, xlim, ylim, xticks, yticks, xlabel, ylabel, w=420, h=168):
        super().__init__(master, width=w, height=h, bg=PANEL, highlightthickness=0)
        self.xlim, self.ylim = xlim, ylim
        self.x0, self.y0, self.x1, self.y1 = 62, 10, w - 12, h - 40
        self.create_rectangle(self.x0, self.y0, self.x1, self.y1, fill="#e3edf9", outline="#999")
        for v in xticks:
            x = self._x(v)
            self.create_line(x, self.y0, x, self.y1, fill="#cfd8e6")
            self.create_text(x, self.y1 + 10, text=f"{v:g}", font=FONT_S)
        for v in yticks:
            y = self._y(v)
            self.create_line(self.x0, y, self.x1, y, fill="#cfd8e6")
            self.create_text(self.x0 - 5, y, text=f"{v:g}", anchor="e", font=FONT_S)
        self.create_text((self.x0 + self.x1) / 2, h - 12, text=xlabel, font=FONT_S)
        self.create_text(14, (self.y0 + self.y1) / 2, text=ylabel, angle=90, font=FONT_S)
        self.bandrect = self.create_rectangle(0, 0, 0, 0, fill="#8fb4e6", stipple="gray25", outline="#2f6fd0")
        self.area = self.create_polygon(0, 0, 0, 0, fill="#b9d3f2", outline="")
        self.line = self.create_line(0, 0, 0, 0, fill="#2f6fd0", width=1)

    def _x(self, v):
        return self.x0 + (v - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * (self.x1 - self.x0)

    def _y(self, v):
        v = min(max(v, self.ylim[0]), self.ylim[1])
        return self.y1 - (v - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * (self.y1 - self.y0)

    def set_band(self, lo, hi):
        self.coords(self.bandrect, self._x(lo), self.y0, self._x(hi), self.y1)

    def set_data(self, xs, ys):
        m = (xs >= self.xlim[0]) & (xs <= self.xlim[1])
        pts = [c for x, y in zip(xs[m], ys[m]) for c in (self._x(x), self._y(y))]
        if len(pts) >= 4:
            self.coords(self.line, *pts)
            self.coords(self.area, self._x(xs[m][0]), self.y1, *pts, self._x(xs[m][-1]), self.y1)


class App:
    def __init__(self, root, args):
        self.root = root
        self.rx = None
        self.avg_rf = self.avg_mpx = None
        root.title("Penerima FM xSDR")
        root.configure(bg=BG)
        style = ttk.Style()
        style.configure("TLabel", background=BG, font=FONT)
        style.configure("H.TLabel", background=BG, font=FONT_B)

        # --- header biru dengan tombol play dan judul
        head = tk.Frame(root, bg=BLUE)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.start_btn = tk.Button(head, text="▶", font=("DejaVu Sans", 16), width=3, bg="#5a9e9a",
                                   fg="white", activebackground="#4c8c88", relief="flat", command=self.toggle)
        self.start_btn.pack(side="left", padx=14, pady=8)
        tk.Label(head, text="FM receiver", bg=BLUE, fg="white",
                 font=("DejaVu Sans", 18, "bold")).pack(side="right", padx=20)
        tk.Label(head, text="xSDR  ·  FM broadcast 87,5 - 108 MHz", bg=BLUE, fg="white",
                 font=FONT).pack(side="right")

        # --- baris kolom isian seperti wsdr
        bar = tk.Frame(root, bg=BG)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        self.freq = tk.DoubleVar(value=args.freq)
        self.gain = tk.DoubleVar(value=args.gain)
        self.vol = tk.DoubleVar(value=60)
        self.sdr_var, self.audio_var = tk.StringVar(), tk.StringVar()
        self.rx_var = tk.StringVar(value=RX_PORTS[0])
        self.freq_txt, self.tune_txt, self.bw_txt = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.df, self.bw = 0.0, BW          # posisi dan lebar kanal relatif frekuensi tengah

        def field(col, title, widget, **kw):
            lbl = ttk.Label(bar, text=title, style="H.TLabel")
            lbl.grid(row=0, column=col, sticky="w", padx=6)
            widget.grid(row=1, column=col, sticky="w", padx=6, **kw)
            return lbl

        f0 = tk.Frame(bar, bg=BG)
        self.sdr_box = ttk.Combobox(f0, textvariable=self.sdr_var, state="readonly", width=21)
        self.sdr_box.pack(side="left")
        ttk.Button(f0, text="⟳", width=2, command=self.refresh_devices).pack(side="left", padx=2)
        field(0, "Sumber (input SDR)", f0)
        self.rx_box = ttk.Combobox(bar, textvariable=self.rx_var, values=RX_PORTS, state="readonly", width=5)
        self.rx_box.bind("<<ComboboxSelected>>", self.on_rx_port)
        field(1, "Antena", self.rx_box)
        ent = ttk.Entry(bar, textvariable=self.freq_txt, width=10, font=FONT)
        ent.bind("<Return>", self.on_entry)
        field(2, "Frek. tengah", ent)
        ent_t = ttk.Entry(bar, textvariable=self.tune_txt, width=11, font=FONT)
        ent_t.bind("<Return>", self.on_tune_entry)
        field(3, "Tune Frequency", ent_t)
        ent_b = ttk.Entry(bar, textvariable=self.bw_txt, width=8, font=FONT)
        ent_b.bind("<Return>", self.on_bw_entry)
        field(4, "Bandwidth", ent_b)
        self.gain_scale = ttk.Scale(bar, variable=self.gain, from_=0, to=61, length=110,
                                    command=self.on_gain)
        self.gain_lbl = field(5, "Gain", self.gain_scale)
        field(6, "Samplerate", ttk.Label(bar, text=f"{FS / 1e6:.3f} MHz", width=10, relief="groove"))
        self.audio_box = ttk.Combobox(bar, textvariable=self.audio_var, width=15)
        field(7, "Output audio", self.audio_box)
        self.vol_scale = ttk.Scale(bar, variable=self.vol, from_=0, to=100, length=90, command=self.on_vol)
        self.vol_lbl = field(8, "Volume", self.vol_scale)

        # --- kiri: waterfall + slider frekuensi
        left = tk.Frame(root, bg=BG)
        left.grid(row=2, column=0, sticky="n", padx=(10, 4))
        self.wf = Waterfall(left, self.on_channel, self.recenter)
        self.wf.pack()
        tuner = tk.Frame(left, bg=BG)
        tuner.pack(fill="x", pady=6)
        ttk.Button(tuner, text="◀", width=3, command=lambda: self.step(-0.1)).pack(side="left")
        tk.Scale(tuner, variable=self.freq, from_=FMIN, to=FMAX, resolution=0.1, orient="horizontal",
                 length=560, bg=BG, highlightthickness=0, font=FONT_S,
                 showvalue=True, command=self.on_freq).pack(side="left", padx=4)
        ttk.Button(tuner, text="▶", width=3, command=lambda: self.step(0.1)).pack(side="left")

        # --- kanan: spektrum RF, pengaturan, spektrum MPX
        right = tk.Frame(root, bg=BG)
        right.grid(row=2, column=1, sticky="n", padx=(4, 10))
        self.p_rf = Plot(right, (-SPAN / 1e3, SPAN / 1e3), (-110, -10), range(-400, 401, 200),
                         range(-100, -9, 10), "Frekuensi, kHz", "PSD, dBFS")
        self.p_rf.pack()
        opt = tk.Frame(right, bg=BG)
        opt.pack(fill="x", pady=6)
        self.fps, self.alpha = tk.StringVar(value="10"), tk.StringVar(value="0.3")
        self.db_lo, self.db_hi = tk.StringVar(value="-110"), tk.StringVar(value="-10")
        for i, (t, v, vals) in enumerate((("FPS", self.fps, ("5", "10", "20")),
                                          ("Alpha", self.alpha, ("0.1", "0.3", "0.5", "1")),
                                          ("dB min", self.db_lo, ("-130", "-120", "-110", "-100", "-90")),
                                          ("dB maks", self.db_hi, ("-40", "-30", "-20", "-10", "0")))):
            ttk.Label(opt, text=t, style="H.TLabel").grid(row=0, column=i, sticky="w", padx=6)
            cb = ttk.Combobox(opt, textvariable=v, values=vals, width=6, state="readonly")
            cb.grid(row=1, column=i, padx=6, sticky="w")
            cb.bind("<<ComboboxSelected>>", self.on_range)
        self.p_mpx = Plot(right, (0, 60), (-80, 0), range(0, 61, 10), range(-80, 1, 10),
                          "Frekuensi MPX, kHz (pilot 19, stereo 38, RDS 57)", "PSD, dBFS")
        self.p_mpx.pack()

        self.status = ttk.Label(root, text="Pilih sumber SDR lalu tekan ▶.", anchor="w")
        self.status.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 8))

        root.bind("<Left>", lambda e: self.step(-0.1))
        root.bind("<Right>", lambda e: self.step(0.1))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        self.refresh_devices(prefer=args.device)
        self.audio_box["values"] = list_audio_devices()
        self.audio_var.set(args.audio or self.audio_box["values"][0])
        self.rx_var.set(f"RX {getattr(args, 'rx', 'A').upper()}")
        self.on_freq(); self.on_gain(); self.on_vol()
        self.f_rf = (np.arange(NFFT_RF) - NFFT_RF / 2) * FS / NFFT_RF
        self.f_mpx = np.arange(NFFT_MPX // 2) * (FS / D1) / NFFT_MPX
        self.update_view()

    # --- perangkat
    def refresh_devices(self, prefer=None):
        self.devices = list_sdr_devices()
        labels = [l for l, _ in self.devices]
        self.sdr_box["values"] = labels
        alias = {"m2": XSDR_M2, "m.2": XSDR_M2, "pci": XSDR_M2, "pcie": XSDR_M2, "usb": XSDR_USB}
        want = alias.get((prefer or "").lower(), prefer)
        found = [l for l in labels if "tidak terdeteksi" not in l and l != SIM_LABEL]
        pick = next((l for l in labels if want and want.lower() in l.lower()
                     and "tidak terdeteksi" not in l), None)
        pick = pick or (found[0] if found else labels[0])
        self.sdr_var.set(pick)
        self.status["text"] = (f"Terdeteksi: {', '.join(found)}" if found
                               else "Tidak ada SDR terdeteksi. Pilih mode simulasi untuk mencoba GUI.")

    def toggle(self):
        if self.rx:
            self.stop()
        else:
            self.start()

    def start(self):
        kwargs = dict(self.devices)[self.sdr_var.get()]
        try:
            ch = RX_PORTS.index(self.rx_var.get())
            src = SimSource() if kwargs is None else SoapySource(kwargs, ch)
        except Exception as e:
            hint = (" Cek kartu di slot M.2 (lspci) atau pilih xSDR USB." if "M.2" in self.sdr_var.get()
                    else " Cek colokan adapter USB (lsusb)." if "USB" in self.sdr_var.get() else "")
            self.status["text"] = f"Gagal membuka {self.sdr_var.get()}: {e}.{hint}"
            return
        lo, hi = src.gain_range
        self.gain_scale.configure(from_=lo, to=hi)
        self.gain.set(min(max(self.gain.get(), lo), hi))
        audio = self.audio_var.get().split()[0]
        self.rx = Receiver(src, audio, self.freq.get() * 1e6, self.gain.get(), self.vol.get() / 100)
        self.rx.channel(self.df, self.bw)
        self.rx.start()
        self.start_btn["text"] = "■"
        self.sdr_box.configure(state="disabled")
        self.status["text"] = f"Menerima lewat {self.sdr_var.get()}, antena {self.rx_var.get()}"

    def stop(self):
        if self.rx:
            self.rx.stop()
            self.rx.join(timeout=3)
            self.rx = None
        self.start_btn["text"] = "▶"
        self.sdr_box.configure(state="readonly")
        self.avg_rf = self.avg_mpx = None

    # --- kontrol
    def step(self, d):
        self.freq.set(round(min(max(self.freq.get() + d, FMIN), FMAX), 1))
        self.on_freq()

    def on_channel(self, df, bw):
        """Kanal digeser/dilebarkan (dari mouse atau kolom isian)."""
        self.df, self.bw = df, bw
        self.wf.set_channel(df, bw)
        self.p_rf.set_band((df - bw / 2) / 1e3, (df + bw / 2) / 1e3)
        self.tune_txt.set(f"{(self.freq.get() * 1e6 + df) / 1e3:.1f}kHz")
        self.bw_txt.set(f"{bw / 1e3:.1f}kHz")
        if self.rx:
            self.rx.channel(df, bw)

    def on_rx_port(self, *_):
        """Ganti port antena RX A/RX B; kalau sedang menerima, stream dibuka ulang."""
        if self.rx:
            self.stop()
            self.start()

    def recenter(self):
        """Jadikan kanal yang dipilih sebagai frekuensi tengah."""
        f = min(max(self.freq.get() + self.df / 1e6, FMIN), FMAX)
        self.freq.set(round(f, 4))
        self.df = 0.0
        self.on_freq()

    @staticmethod
    def _num(txt, unit):
        return float(txt.replace(",", ".").lower().replace(unit, "").strip())

    def on_tune_entry(self, *_):
        try:
            f = self._num(self.tune_txt.get(), "khz") * 1e3
        except ValueError:
            return
        df = f - self.freq.get() * 1e6
        if abs(df) > SPAN - self.bw / 2:       # di luar tampilan: pindahkan frekuensi tengah
            self.freq.set(round(min(max(f / 1e6, FMIN), FMAX), 4))
            self.df = 0.0
            self.on_freq()
        else:
            self.on_channel(round(df / 100) * 100, self.bw)

    def on_bw_entry(self, *_):
        try:
            bw = self._num(self.bw_txt.get(), "khz") * 1e3
        except ValueError:
            return
        bw = min(max(bw, BW_MIN), BW_MAX)
        self.on_channel(min(max(self.df, -(SPAN - bw / 2)), SPAN - bw / 2), bw)

    def on_entry(self, *_):
        try:
            f = float(self.freq_txt.get().replace(",", ".").lower().replace("mhz", ""))
        except ValueError:
            return
        self.freq.set(round(min(max(f, FMIN), FMAX), 4))
        self.on_freq()

    def on_freq(self, *_):
        f = round(self.freq.get(), 4)
        self.freq_txt.set(f"{f:.3f}MHz")
        if self.rx:
            self.rx.tune(f * 1e6)
        self.on_channel(self.df, self.bw)

    def on_gain(self, *_):
        self.gain_lbl["text"] = f"Gain {self.gain.get():.0f} dB"
        if self.rx:
            self.rx.gain(round(self.gain.get()))

    def on_vol(self, *_):
        self.vol_lbl["text"] = f"Volume {self.vol.get():.0f} %"
        if self.rx:
            self.rx.volume = self.vol.get() / 100

    def on_range(self, *_):
        self.wf.set_range(self.db_lo.get(), self.db_hi.get())

    def update_view(self):
        if self.rx:
            if self.rx.error:
                self.status["text"] = f"Error: {self.rx.error}"
                self.stop()
            elif self.rx.rf is not None:
                rf, mpx, a = self.rx.rf, self.rx.mpx, float(self.alpha.get())
                self.avg_rf = rf if self.avg_rf is None else (1 - a) * self.avg_rf + a * rf
                self.avg_mpx = mpx if self.avg_mpx is None else (1 - a) * self.avg_mpx + a * mpx
                self.wf.push(self.f_rf, rf)
                self.p_rf.set_data(self.f_rf / 1e3, self.avg_rf)
                self.p_mpx.set_data(self.f_mpx / 1e3, self.avg_mpx)
                self.status["text"] = (f"Menerima {(self.freq.get() * 1e6 + self.df) / 1e6:.4f} MHz"
                                       f" (lebar {self.bw / 1e3:.0f} kHz) lewat {self.sdr_var.get()}"
                                       f" {self.rx_var.get()}"
                                       f"   |   sinyal kanal {self.rx.rssi:.0f} dBFS")
        self.root.after(int(1000 / int(self.fps.get())), self.update_view)

    def quit(self):
        self.stop()
        self.root.destroy()


def main():
    p = argparse.ArgumentParser(description="Penerima FM dengan GUI untuk xSDR")
    p.add_argument("--freq", type=float, default=98.7, help="frekuensi awal (MHz)")
    p.add_argument("--gain", type=float, default=40, help="gain awal (dB)")
    p.add_argument("--device", help="sumber SDR: m2, usb, atau teks yang ada di labelnya")
    p.add_argument("--rx", choices=["A", "B", "a", "b"], default="A", help="port antena penerima: A atau B")
    p.add_argument("--audio", help="perangkat aplay, mis. plughw:2,0")
    p.add_argument("--autostart", action="store_true", help="langsung mulai menerima")
    args = p.parse_args()
    root = tk.Tk()
    app = App(root, args)
    if args.autostart:
        root.after(300, app.start)
    root.mainloop()


if __name__ == "__main__":
    main()
