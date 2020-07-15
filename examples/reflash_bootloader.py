#!/usr/bin/env python3

## Imports

import __init__
import argparse
import functools
import logging

import pyudev

from mcu_interface import Bossa
from mcu_interface import Dfu

bossa_cmd = '/home/hyatt/git/manufacturing-next/flashenv/BOSSA/bin/bossac'
bossa_file = '/home/hyatt/Source/kiibohd/controller-next/Bootloader/Builds/linux-gnu.sam4s4b.Hexgears_DK1008.gcc.ninja/kiibohd_bootloader.bin'
bossa_regions = [0, 1, 2] # 24 kB

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    # Setup udev connection
    context = pyudev.Context()

    # Look for dfu devices
    for device in context.list_devices(subsystem='usb').match_attribute('bInterfaceClass', 'fe').match_attribute('bInterfaceSubClass', '01'):
        vid = device.parent.attributes.asstring('idVendor')
        pid = device.parent.attributes.asstring('idProduct')
        path = device.parent.sys_name

        # Get Dfu interface
        intf = Dfu(path, vid, pid)

        # Swap to SAM-BA bootloader
        intf.samba_bootloader()

        # Wait for udev plug bind event
        found = False
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='usb')
        for device in iter(functools.partial(monitor.poll, 3), None):
            if device.sys_name == path and device.action == 'bind':
                found = True
                break
        assert found, "Could not find USB device: {}".format(path)

        # Get BOSSA interface
        bossa = Bossa.from_udev(bossa_cmd, path)
        logger.info(bossa.query())

        # Flash file using bossa
        bossa.cmd(
            boot_flash=True,
            file=bossa_file,
            lock_regions=bossa_regions,
            reset=True,
            unlock_regions=bossa_regions,
            verify=True,
            write=True,
        )
