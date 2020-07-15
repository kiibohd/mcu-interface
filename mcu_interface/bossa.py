# Copyright (C) 2020 Jacob Alexander
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

## Imports

import logging
import subprocess

import pyudev

from mcu_interface.common import timeit

# Logging
logger = logging.getLogger(__name__)



## Classes

class BossaInfo:
    '''
    Wrapper class around bossac --info fields

    @param bossac_output: Newline separated string containing the bossac --info fields
    '''
    def __init__(self, bossac_output):
        # Possible fields, set to None if not set
        self.device = None
        self.version = None
        self.version_date = None
        self.address = None
        self.pages = None
        self.page_size = None
        self.total_size = None
        self.planes = None
        self.lock_region = None
        self.locked = None
        self.security = None
        self.boot_flash = None
        self.unique_id = None
        self.set(bossac_output)

    def __repr__(self):
        return "device({device})\nversion({version})\nversion_date({version_date})\naddress({address:#x})\npages({pages})\npage_size({page_size} bytes)\ntotal_size({total_size} bytes)\nplanes({planes})\nlock_region({lock_region})\nlocked({locked})\nsecurity({security})\nboot_flash({boot_flash})\nunique_id({unique_id})".format(
            device=self.device,
            version=self.version,
            version_date=self.version_date,
            address=self.address,
            pages=self.pages,
            page_size=self.page_size,
            total_size=self.total_size,
            planes=self.planes,
            lock_region=self.lock_region,
            locked=self.locked,
            security=self.security,
            boot_flash=self.boot_flash,
            unique_id=self.unique_id,
        )

    def set(self, bossac_output):
        '''
        Writes/Parses bossac --info fields

        @param bossac_output: Newline separated string containing the bossac --info fields
        '''
        for line in bossac_output.split('\n'):
            if 'Device' in line:
                self.device = line.split(': ')[1]
            elif 'Version' in line:
                self.version, self.version_date = line.split(': ')[1].split(' ', 1)
            elif 'Address' in line:
                self.address = int(line.split(': ')[1], 16)
            elif 'Pages' in line:
                self.pages = int(line.split(': ')[1])
            elif 'Page Size' in line:
                self.page_size = int(line.split(': ')[1].split(' ', 1)[0])
            elif 'Total Size' in line:
                value = line.split(': ')[1]
                if 'KB' in value:
                    self.total_size = int(value.split('KB')[0]) * 1024
                elif 'MB' in value:
                    self.total_size = int(value.split('MB')[0]) * 1024 * 1024
            elif 'Planes' in line:
                self.planes = int(line.split(': ')[1])
            elif 'Lock Regions' in line:
                self.lock_region = int(line.split(': ')[1])
            elif 'Locked' in line:
                self.locked = line.split(': ')[1]
            elif 'Security' in line:
                self.security = bool(line.split(': ')[1] == 'true')
            elif 'Boot Flash' in line:
                self.boot_flash = bool(line.split(': ')[1] == 'true')
            elif 'Unique Id' in line:
                self.unique_id = line.split(': ')[1]
            elif line == '':
                pass
            else:
                logger.warning("Unknown bossac line '%s'", line)


class Bossa:
    '''
    Wrapper class around bossac

    @param bossa_cmd: Path to bossac executable
    '''
    def __init__(self, port, bossa_cmd):
        self.port = port
        self.bossa_info = None
        self.bossa_cmd = bossa_cmd

    @classmethod
    def from_udev(cls, bossa_cmd, path=None):
        '''
        Attempt to detect SAM-BA device using udev

        @param bossa_cmd: Path to bossac executable
        '''
        # Use list of pids to determine SAM-BA compatible devices
        pids = [
            (0x03eb,0x6124)
        ]

        # Setup udev connection
        context = pyudev.Context()

        # Find ttyACM* name for SAM-BA
        device_list = []
        for pid in pids:
            idvendor = '{:04x}'.format(pid[0])
            idproduct = '{:04x}'.format(pid[1])
            matches = context.list_devices(subsystem='usb').match_attribute('idVendor', idvendor).match_attribute('idProduct', idproduct)
            # Optionally match sys_name (used to look for a specific USB port)
            if path:
                matches = matches.match_sys_name(path)

            # Look for SAM-BA/cdc_acm interfaces
            for device in matches:
                for child in device.children:
                    if child.driver == 'cdc_acm':
                        for subchild in child.children:
                            # This is /dev/ttyACM*
                            device_list.append((subchild.device_node, device.sys_name))

        # Check if any devices were found
        if len(device_list) == 0:
            raise FileNotFoundError('No SAM-BA devices were found.')

        # Sort list, then use the first one if a selection must be made
        def getkey(item):
            return item[0]
        device_list = sorted(device_list, key=getkey)

        # Show devices
        logger.info("--- SAM-BA devices found ---")
        for device in device_list:
            logger.info(device)

        # Select first item
        return cls(port=device_list[0][0], bossa_cmd=bossa_cmd)

    def query(self):
        '''
        Queries SAM-BA port and runs bossac --info
        '''
        # Get BOSSA info
        cmd = [self.bossa_cmd, '--port', self.port, '--info']
        logger.info("Retrieving bossac info: %s", cmd)
        bossa_info_cmd = subprocess.run(cmd, capture_output=True, check=True)
        self.bossa_info = BossaInfo(bossa_info_cmd.stdout.decode())
        return self.bossa_info

    @timeit
    def cmd(
            self, file=None, erase=None, write=False, boot_flash=False, unlock_regions=[], lock_regions=[], verify=False, reset=False):
        '''
        Sends a command via bossac

        @param file: If a file is specified it is flashed. Erase is automatically set when a file + write is specified
        @param erase: None (auto), False (do not erase), True (erase)
        @param write: True to write to flash (Erase automatically enabled)
        @param boot_flash: Set to True to boot to flash (False boots to ROM)
        @param unlock_regions: List of regions to unlock (before flashing)
        @param lock_regions: List of regions to lock (after flashing)
        @param verify: Verify after flashing specified file (file must be specified)
        @param reset: Reset after flashing file
        '''
        bossa_run_cmd = [self.bossa_cmd, '--port', self.port]

        # If unlock is specified, run first as a separate command
        if len(unlock_regions) > 0:
            bossa_run_cmd_unlock = bossa_run_cmd
            bossa_run_cmd_unlock.append('--unlock={}'.format(','.join(str(x) for x in unlock_regions)))
            logger.info("Unlocking chip: %s", bossa_run_cmd_unlock)
            bossa_run_output = subprocess.run(bossa_run_cmd_unlock, check=True)

        # Run command
        if file:
            bossa_run_cmd.append(file)
        if erase or (file and write):
            bossa_run_cmd.append('--erase')
        if write:
            bossa_run_cmd.append('--write')
        if boot_flash:
            bossa_run_cmd.append('--boot=1') # Flash
        else:
            bossa_run_cmd.append('--boot=0') # ROM
        if len(lock_regions) > 0:
            bossa_run_cmd.append('--lock={}'.format(','.join(str(x) for x in lock_regions)))
        if verify and file:
            bossa_run_cmd.append('--verify')
        if reset:
            bossa_run_cmd.append('--reset')
        logger.info("Running bossac command: %s", bossa_run_cmd)

        bossa_run_output = subprocess.run(bossa_run_cmd, check=True)
