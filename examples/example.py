#!/usr/bin/env python3

## Imports

import __init__
import argparse
import logging

import pyudev

from mcu_interface import Bossa

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    # VID:PID lists
    pids = [(0x03eb,0x6124, 'bossa'), (0x308F, 0x002E, 'dfu'), (0x308F, 0x002F, 'hidio')]
    bossa_cmd = '/home/hyatt/git/manufacturing-next/flashenv/BOSSA/bin/bossac'
    bossa_file = '/home/hyatt/Source/kiibohd/controller-next/Bootloader/Builds/linux-gnu.sam4s4b.Hexgears_DK1008.gcc.ninja/kiibohd_bootloader.bin'
    bossa_regions = [0, 1, 2] # 24 kB

    # Setup udev connection
    context = pyudev.Context()

    # Query all connected devices
    for pid in pids:
        idvendor = '{:04x}'.format(pid[0])
        idproduct = '{:04x}'.format(pid[1])
        for device in context.list_devices(subsystem='usb').match_attribute('idVendor', idvendor).match_attribute('idProduct', idproduct):

            # Find ttyACM* name for SAM-BA
            if pid[2] == 'bossa':
                for child in device.children:
                    if child.driver == 'cdc_acm':
                        for subchild in child.children:
                            # This is /dev/ttyACM*
                            logger.info(subchild.device_node)

                            # Get BOSSA info
                            bossa = Bossa.from_udev(bossa_cmd)
                            #bossa = Bossa(subchild.device_node)
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
            # Find dfu information
            elif pid[2] == 'dfu':
                # First check for dfu enabled USB endpoint
                # TODO
                for child in device.children:
                    logger.info(child)
                    for subchild in child.children:
                        logger.info(subchild)
                pass
            # Find HID-IO information
            elif pid[2] == 'hidio':
                # First check for HID-IO enable USB endpoint
                # TODO
                pass

    # Monitor incoming devices
    #monitor = pyudev.Monitor.from_net
