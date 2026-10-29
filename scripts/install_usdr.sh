#!/bin/bash
# Pasang usdr-lib (library, alat CLI, modul SoapySDR) dan driver kernel PCIe lewat DKMS
# untuk xSDR di Raspberry Pi CM5. Jalankan setelah config.txt diubah dan CM5 di-reboot.
# Pakai: bash scripts/install_usdr.sh
set -e

# 2a. Paket yang dibutuhkan
sudo apt install -y git build-essential cmake pkg-config python3 python3-yaml \
  dwarves libusb-1.0-0-dev check dkms \
  libsoapysdr-dev soapysdr-tools python3-soapysdr python3-numpy python3-scipy \
  alsa-utils pciutils
sudo apt install -y linux-headers-$(uname -r) || sudo apt install -y linux-headers-rpi-v8

# 2b. Build dan install libusdr, alat CLI, dan modul SoapySDR
[ -d ~/usdr-lib ] || git clone https://github.com/wavelet-lab/usdr-lib.git ~/usdr-lib
cd ~/usdr-lib
cmake -S src -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
      -DENABLE_GUI=OFF -DENABLE_DMONITOR=OFF
cmake --build build -j$(nproc)
sudo cmake --install build
sudo ldconfig

# 2c. Driver kernel PCIe lewat DKMS
VER=$(git describe --tags --abbrev=0 | sed 's/^v//')
sudo mkdir -p /usr/src/usdr-dkms-$VER
sudo cp src/lib/lowlevel/pcie_uram/driver/usdr_pcie_uram.c \
        src/lib/lowlevel/pcie_uram/driver/Makefile \
        src/lib/lowlevel/pcie_uram/pcie_uram_driver_if.h \
        src/lib/device/device_cores.h \
        src/lib/ipblks/si2c.c src/lib/ipblks/si2c.h \
        src/lib/ipblks/spiext.c src/lib/ipblks/spiext.h \
        /usr/src/usdr-dkms-$VER/
sed "s/#MODULE_VERSION#/$VER/g" packaging/debian-bookworm/usdr-dkms.dkms.template \
  | sudo tee /usr/src/usdr-dkms-$VER/dkms.conf
sudo dkms add -m usdr-dkms -v $VER
sudo dkms build -m usdr-dkms -v $VER
sudo dkms install -m usdr-dkms -v $VER

# 2d. Izin akses dan muat otomatis saat boot
sudo cp src/lib/lowlevel/pcie_uram/driver/helpers/50-usdr-pcie-driver.rules /etc/udev/rules.d/
echo usdr_pcie_uram | sudo tee /etc/modules-load.d/usdr.conf
sudo udevadm control --reload-rules
sudo modprobe usdr_pcie_uram
sudo udevadm trigger

echo
echo "Selesai. Cek dengan: lsmod | grep usdr ; ls -l /dev/usdr0 ; SoapySDRUtil --find=\"driver=usdr\""
