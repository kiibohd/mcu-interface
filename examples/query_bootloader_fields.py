#!/usr/bin/env python3

## Imports

import __init__
import argparse
import functools
import logging

import pyudev

from mcu_interface import Dfu


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

        # Show results
        logger.info(intf.dfu_interfaces())
