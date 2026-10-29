#!/usr/bin/env python3
# Tes singkat: baca 65536 sampel IQ dari xSDR lewat SoapySDR (driver=usdr)
import numpy as np
import SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32

dev = SoapySDR.Device("driver=usdr")
dev.setSampleRate(SOAPY_SDR_RX, 0, 1.92e6)
dev.setFrequency(SOAPY_SDR_RX, 0, 100e6)
dev.setAntenna(SOAPY_SDR_RX, 0, "LNAW")
for name, val in (("LNA", 30), ("TIA", 12), ("PGA", 0)):
    dev.setGain(SOAPY_SDR_RX, 0, name, val)

rx = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32, [0])
dev.activateStream(rx)
buf = np.zeros(65536, np.complex64)
sr = dev.readStream(rx, [buf], len(buf), timeoutUs=1_000_000)
print("sampel diterima:", sr.ret)
dev.deactivateStream(rx)
dev.closeStream(rx)
