**English** | [Bahasa Indonesia](README.md)

# xSDR FM Receiver for Raspberry Pi CM5

Companion code for the tutorial **"Implementing the xSDR Software Defined Radio on Raspberry Pi Compute Module 5 over the PCIe M.2 Interface"** (written in Indonesian). With this code, an xSDR (Wavelet Lab) in the M.2 slot of a piBrick PocketCM5 carrier board can receive FM radio broadcasts.

The full tutorial, with hardware photos, is on the blog.

![fm_gui.py: waterfall, RF spectrum and MPX spectrum](docs/fm-gui.png)

*`fm_gui.py` in a wsdr.io-like layout: ±500 kHz waterfall, RF spectrum, and MPX spectrum showing the 19 kHz pilot.*

> The GUI labels, code comments and script messages are in Indonesian. This README gives the English meaning of each label.

## Repository contents

| File | Purpose |
|---|---|
| `config/config.txt.snippet` | Three lines for `/boot/firmware/config.txt`: enable PCIe, 4 KB page kernel, 32-bit DMA |
| `scripts/install_usdr.sh` | Builds and installs usdr-lib, the SoapySDR module, and the `usdr_pcie_uram` kernel driver (DKMS) |
| `scripts/cek_xsdr.sh` | Checks whether the xSDR is detected over M.2, USB and SoapySDR ("cek" = "check") |
| `scripts/cek-xsdr.desktop` | Runs `cek_xsdr.sh` automatically at every desktop login |
| `test_soapy.py` | Quick test that reads IQ samples through SoapySDR |
| `fm_mini.py` | Minimal mono FM receiver for the terminal (~60 lines) |
| `fm_gui.py` | FM receiver with a wsdr.io-style GUI: waterfall, RF spectrum, MPX spectrum, source and RX A/B antenna selection, plus a simulation mode that needs no hardware |

## Hardware

- Raspberry Pi Compute Module 5 on a carrier board with an M.2 slot (tested on piBrick PocketCM5)
- xSDR M.2 2230 module (Wavelet Lab)
- M.2 B+M-key to A+E-key adapter (or a USB3_to_M2_E adapter for testing over USB)
- FM antenna with an MHF4 connector

## Quick start

```bash
# 1. Enable PCIe: copy config/config.txt.snippet into the [all] section of /boot/firmware/config.txt
sudo nano /boot/firmware/config.txt
sudo reboot

# After the reboot, check:
getconf PAGESIZE          # 4096
lspci -nn | grep 10ee     # Xilinx [10ee:7049]

# 2. Install the library and driver
bash scripts/install_usdr.sh

# 3. Check the xSDR
bash scripts/cek_xsdr.sh

# 4. Listen to the radio
python3 fm_mini.py 98.7
sudo apt install -y python3-tk
python3 fm_gui.py --device m2 --rx A --freq 98.7 --autostart
```

### Automatic check at login (optional)

```bash
mkdir -p ~/xsdr ~/.config/autostart
cp scripts/cek_xsdr.sh ~/xsdr/ && chmod +x ~/xsdr/cek_xsdr.sh
cp scripts/cek-xsdr.desktop ~/.config/autostart/
```

## fm_gui.py options

| Option | Description |
|---|---|
| `--freq 98.7` | Initial frequency (MHz) |
| `--gain 40` | Initial gain (dB), split automatically across LNA/TIA/PGA |
| `--device m2` / `usb` | SDR source; without an SDR, choose "Simulasi" (simulation) in the GUI |
| `--rx A` / `B` | xSDR receive antenna port |
| `--audio plughw:2,0` | `aplay` output device |
| `--autostart` | Start receiving immediately |

## The GUI

### Input source

The **Sumber (input SDR)** (input source) dropdown always lists xSDR M.2 (PCIe), xSDR USB, any other SDR found by SoapySDR, and **Simulasi (tanpa SDR)** (simulation, no SDR) for trying the GUI without hardware. Paths that are not detected are marked "(tidak terdeteksi)" (not detected); the ⟳ button rescans devices.

![Input source dropdown](docs/fm-gui-sumber-input.jpg)

### What each part does

![Parts of the FM receiver GUI](docs/fm-gui-fungsi-bagian.jpg)

- **▶ / ■**: start or stop receiving
- **Sumber** (source), **Antena** (antenna, RX A / RX B), **Output audio**: choose the SDR, antenna port and `aplay` device
- **Frek. tengah** (center frequency), **Tune Frequency**, **Bandwidth**: type a value and press Enter
- **Gain** and **Volume**: sliders; gain is split automatically across LNA/TIA/PGA
- **Waterfall** (left) and **RF spectrum** (top right): ±500 kHz around the center frequency, with the demodulated channel highlighted
- **MPX spectrum** (bottom right): demodulator output; pilot 19 kHz, stereo 38 kHz, RDS 57 kHz
- **FPS, Alpha, dB min/maks**: display refresh rate, smoothing and dB range
- **Status bar**: frequency, channel width, source and in-channel signal level (dBFS)

### Adjusting the channel with the mouse

Drag the middle of the channel in the waterfall to move it:

![Moving the channel](docs/fm-gui-geser-kanal.jpg)

Drag a channel edge to widen or narrow it (30–220 kHz):

![Changing the channel width](docs/fm-gui-lebar-kanal.jpg)

You can also click outside the channel to move it there, scroll to shift it by 5 kHz, and double-click to make the channel the new center frequency. The left/right arrow keys move the center frequency by 0.1 MHz.

## Notes

- In the usdr driver, the overall `setGain()` only sets the LNA, so this code sets LNA, TIA and PGA individually.
- The SDR is tuned 250 kHz below the station so the DC spike does not interfere.
- `fm_gui.py` still opens without SoapySDR (simulation mode), which is handy for trying the interface.

## References

- usdr-lib (official driver and library): https://github.com/wavelet-lab/usdr-lib
- piBrick PocketCM5: https://github.com/amarullz/piBrick

## License

The code in this repository is released under the [MIT](LICENSE) license.
