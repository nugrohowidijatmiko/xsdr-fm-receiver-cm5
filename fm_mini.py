#!/usr/bin/env python3
# Penerima FM mono sederhana untuk xSDR (SoapySDR, driver=usdr)
# Pakai: python3 fm_mini.py 98.7   (Ctrl+C untuk berhenti)
import subprocess, sys
import numpy as np
from scipy import signal
import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

FREQ = float(sys.argv[1]) * 1e6 if len(sys.argv) > 1 else 98.7e6
FS, OFFSET = 1.92e6, 250e3          # tune 250 kHz di bawah stasiun, hindari spike DC
D1, D2 = 8, 5                       # 1.92 MS/s -> 240 kS/s -> 48 kHz
BLOCK = 96000                       # 50 ms

dev = SoapySDR.Device("driver=usdr")
dev.setSampleRate(SOAPY_SDR_RX, 0, FS)
dev.setFrequency(SOAPY_SDR_RX, 0, FREQ - OFFSET)
dev.setAntenna(SOAPY_SDR_RX, 0, "LNAW")
# setGain() total hanya mengatur LNA, jadi atur tiap tahap
for name, val in (("LNA", 30), ("TIA", 12), ("PGA", 19)):
    dev.setGain(SOAPY_SDR_RX, 0, name, val)

rx = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [0])
dev.activateStream(rx)
aplay = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "48000"],
                         stdin=subprocess.PIPE)

lp1 = signal.firwin(65, 100e3, fs=FS)              # pilih satu stasiun (+-100 kHz)
lp2 = signal.firwin(65, 15e3, fs=FS / D1)          # audio 15 kHz
z1 = np.zeros(len(lp1) - 1, complex)
z2 = np.zeros(len(lp2) - 1)
alpha = 1 - np.exp(-1 / (FS / D1 * 50e-6))         # de-emphasis 50 us
zd = np.zeros(1)
mix = np.exp(-2j * np.pi * OFFSET / FS * np.arange(BLOCK))
phase, last = 1 + 0j, 1 + 0j
buf = np.zeros(BLOCK, np.complex64)

try:
    while True:
        got = 0
        while got < BLOCK:
            sr = dev.readStream(rx, [buf[got:]], BLOCK - got, timeoutUs=1_000_000)
            if sr.ret > 0:
                got += sr.ret
        x = buf * mix * phase                          # geser stasiun ke 0 Hz
        phase *= np.exp(-2j * np.pi * OFFSET / FS * BLOCK)
        x, z1 = signal.lfilter(lp1, 1, x, zi=z1)
        x = x[::D1]                                    # 240 kS/s
        d = np.angle(x * np.conj(np.r_[last, x[:-1]])) # demodulasi FM
        last = x[-1]
        d, zd = signal.lfilter([alpha], [1, alpha - 1], d, zi=zd)
        a, z2 = signal.lfilter(lp2, 1, d, zi=z2)
        a = a[::D2] / (2 * np.pi * 75e3 / (FS / D1))   # 48 kHz, skala ke +-1
        aplay.stdin.write((np.clip(a * 0.8, -1, 1) * 32767).astype("<i2").tobytes())
except KeyboardInterrupt:
    pass
finally:
    dev.deactivateStream(rx)
    dev.closeStream(rx)
    aplay.stdin.close()
