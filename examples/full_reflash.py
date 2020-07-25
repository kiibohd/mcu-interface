#!/usr/bin/env python3

## Imports

import __init__
import argparse
import functools
import logging
import os

import pyudev

from mcu_interface import Bossa
from mcu_interface import Dfu

bossa_cmd = '/home/hyatt/git/manufacturing-next/flashenv/BOSSA/bin/bossac'
bossa_regions = [0, 1, 2] # 24 kB

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def retrieve_firmware_revision(buildvars_h):
    '''
    Using buildvars.h read out the firmware revision.
    This is the same as the git commit number
    '''
    with open(buildvars_h) as search:
        for line in search:
            line = line.rstrip() # Remove \n at the end of line
            if 'CLI_RevisionNumber' in line:
                return int(line.split(' ')[-1])
            if 'BCD_VERSION' in line:
                return int(line.split(' ')[-1])
            if 'GIT_COMMIT_NUMBER' in line:
                return int(line.split(' ')[-1])

def find_buildvars_h(hint_file):
    '''
    Locate a buildvars.h using a hint file

    @param hint_file: File that, depending on the name, will look in the surrounding paths
    '''
    path = os.path.dirname(hint_file)
    fullpath = os.path.join(path, 'buildvars.h')
    if os.path.exists(fullpath):
        return fullpath

if __name__ == '__main__':
    # Arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("bootloader_firmware", help="Path to bootloader firmware file, must have companion buildvars.h")
    parser.add_argument("mcu_firmware", help="Path to mcu firmware file, must have companion buildvars.h")
    parser.add_argument("--ble-firmware", help="Path to ble firmware file, must have companion buildvars.h")
    args = parser.parse_args()

    # Find buildvars.h's
    bootloader_buildvars_h = find_buildvars_h(args.bootloader_firmware)
    mcu_buildvars_h = find_buildvars_h(args.mcu_firmware)
    ble_buildvars_h = None
    if args.ble_firmware:
        ble_buildvars_h = find_buildvars_h(args.ble_firmware)

    # Retrieve firmware revisions
    bootloader_rev = retrieve_firmware_revision(bootloader_buildvars_h)
    mcu_rev = retrieve_firmware_revision(mcu_buildvars_h)
    ble_rev = None
    if ble_buildvars_h:
        ble_rev = retrieve_firmware_revision(ble_buildvars_h)

    # Setup udev connection
    context = pyudev.Context()

    # Look for dfu devices
    for device in context.list_devices(subsystem='usb').match_attribute('bInterfaceClass', 'fe').match_attribute('bInterfaceSubClass', '01'):
        vid = device.parent.attributes.asstring('idVendor')
        pid = device.parent.attributes.asstring('idProduct')
        path = device.parent.sys_name

        # Get Dfu interface
        intf = Dfu(path, vid, pid)

        # Check if bootloader needs to be updated
        for dintf in intf.dfu_interfaces():
            # Only check AltSetting 0
            if dintf.alt == 0:
                # Check bootloader revision
                # XXX Do absolute comparsion, this is correct in flashing station context only
                if dintf.ver != bootloader_rev:
                    logger.warning(
                        "Running revision %d doesn't match %d, switching to SAM-BA for bootloader update",
                        dintf.ver,
                        bootloader_rev,
                    )
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
                    assert found, "Could not find USB device (DFU->SAM-BA): {}".format(path)

                    # Get BOSSA interface (uses physical port address to find correct /dev/ttyACM*)
                    bossa = Bossa.from_udev(bossa_cmd, path)
                    logger.info(bossa.query())

                    # Flash file using bossa
                    bossa.cmd(
                        boot_flash=True,
                        file=args.bootloader_firmware,
                        lock_regions=bossa_regions,
                        reset=True,
                        unlock_regions=bossa_regions,
                        verify=True,
                        write=True,
                    )

                    # Wait for udev plug bind event
                    found = False
                    monitor = pyudev.Monitor.from_netlink(context)
                    monitor.filter_by(subsystem='usb')
                    cur_device = None
                    for device in iter(functools.partial(monitor.poll, 3), None):
                        if device.sys_name == path and device.action == 'bind':
                            found = True
                            # Refresh vid and pid in case they were updated
                            vid = device.attributes.asstring('idVendor')
                            pid = device.attributes.asstring('idProduct')
                            cur_device = device
                            break
                    assert found, "Could not find USB device (SAM-BA->DFU): {}".format(path)
                    device = cur_device
                else:
                    logger.info("Bootloader revision %d: OK!", bootloader_rev)

                # Break loop, as there should only be 1 interface
                break

        # Get Dfu interface again (may have been updated)
        intf = Dfu(path, vid, pid)

        # Check if BLE module needs to be updated
        if ble_rev:
            for dintf in intf.dfu_interfaces():
                # Only check AltSetting 1
                if dintf.alt == 1:
                    # Check BLE firmware revision
                    if dintf.serial['ble_revision'] != ble_rev:
                        logger.warning(
                            "BLE revision %d doesn't match %d, flashing BLE firmware",
                            dintf.serial['ble_revision'],
                            ble_rev,
                        )

                        # Download firmware from host to device
                        # AltSetting==1 is used for BLE
                        intf.download(args.ble_firmware, alt=1)

                        # Wait for udev plug bind event
                        found = False
                        monitor = pyudev.Monitor.from_netlink(context)
                        monitor.filter_by(subsystem='usb')
                        for device in iter(functools.partial(monitor.poll, 3), None):
                            if device.sys_name == path and device.action == 'bind':
                                found = True
                                break
                        assert found, "Could not find USB device (DFU): {}".format(path)
                    else:
                        logger.info("BLE revision %d: OK!", ble_rev)

                    # Break loop, as there should only be 1 interface
                    break

        # Check if MCU module needs to be updated
        for dintf in intf.dfu_interfaces():
            # Only check AltSetting 0
            if dintf.alt == 0:
                # Check MCU firmware revision
                if dintf.serial['mcu_revision'] != mcu_rev:
                    logger.warning(
                        "MCU revision %d doesn't match %d, flashing BLE firmware",
                        dintf.serial['mcu_revision'],
                        mcu_rev,
                    )

                    # Download firmware from host to device
                    # AltSetting==0 is used for the USB MCU
                    intf.download(args.mcu_firmware, alt=0)
                else:
                    logger.info("MCU revision %d: OK!", mcu_rev)

                    # Since nothing needs to be flashed, just returning to firmware
                    logger.info("Booting to firmware as no actions are necessary")
                    intf.reset(alt=0)

                # Break loop, as there should only be 1 interface
                break

    logger.info("DONE!")
