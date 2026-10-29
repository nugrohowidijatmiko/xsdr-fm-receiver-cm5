#!/bin/bash
# Cek apakah xSDR terdeteksi (M.2 / USB / SoapySDR). Hasil juga disimpan ke ~/xsdr/cek_xsdr.log
LOG=~/xsdr/cek_xsdr.log
mkdir -p ~/xsdr
{
echo "=== Cek xSDR  $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "[M.2] lspci | grep 10ee"
lspci | grep 10ee && M2=1 || echo "  -> tidak ada 10ee:7049 (slot M.2 link down / kartu belum terpasang rata)"
echo "[M.2] ls /dev/usdr0"
ls /dev/usdr0 2>/dev/null || echo "  -> /dev/usdr0 tidak ada (coba: sudo modprobe usdr_pcie_uram)"
echo "[USB] lsusb | grep 3727"
lsusb | grep 3727 && USB=1 || echo "  -> xSDR USB tidak terdeteksi"
echo "[SoapySDR] SoapySDRUtil --find=\"driver=usdr\""
SoapySDRUtil --find="driver=usdr" 2>&1 | grep -v "^$"
echo
if [ -n "$M2" ] && [ -e /dev/usdr0 ]; then echo "HASIL: xSDR siap lewat M.2  ->  python3 ~/xsdr/fm_gui.py --device m2"
elif [ -n "$USB" ]; then echo "HASIL: xSDR siap lewat USB  ->  python3 ~/xsdr/fm_gui.py --device usb"
else echo "HASIL: xSDR TIDAK terdeteksi. Matikan, cabut daya, pasang ulang xSDR/adapter, lalu nyalakan lagi."
fi
} 2>&1 | tee "$LOG"
