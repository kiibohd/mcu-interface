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
import struct
import subprocess
import tempfile
import textwrap

import pyudev

from mcu_interface.common import timeit

# Logging
logger = logging.getLogger(__name__)



## Commands

dfu_bin = 'dfu-util'
dfu_suffix_bin = 'dfu-suffix'



## Classes

class DfuInfo:
    '''
    Wrapper class around dfu-util --list fields
    '''
    def __init__(self, dfu_output):
        # Possible fields, set to None if not set
        self.vid = None
        self.pid = None
        self.ver = None
        self.devnum = None
        self.cfg = None
        self.intf = None
        self.path = None
        self.alt = None
        self.name = None
        self.serial = None
        self.set(dfu_output)

    def __repr__(self):
        return "vid:pid({vid:04x}:{pid:04x})\nver({ver:x})\ndevnum({devnum})\ncfg({cfg})\nintf({intf})\npath({path})\nalt({alt})\nname({name})\nserial({serial})".format(
            vid=self.vid,
            pid=self.pid,
            ver=self.ver,
            devnum=self.devnum,
            cfg=self.cfg,
            intf=self.intf,
            path=self.path,
            alt=self.alt,
            name=self.name,
            serial=self.serial,
        )

    def set(self, dfu_output):
        '''
        Writes/Parses dfu-util --list entry fields.
        Parses one entry at a time.

        @param dfu_output: Single 'Found DFU:' line used to extract the fields
        '''
        basic_fields = dfu_output.split(', ') # Handles most fields, except vid:pid

        # Extract [vid:pid]
        self.vid, self.pid = [int(x, 16) for x in basic_fields[0].split('[')[1].split(']')[0].split(':')]

        # Handle rest of the fields
        for field in basic_fields:
            if 'ver' in field:
                self.ver = int(field.split('=')[1], 16)
            elif 'devnum' in field:
                self.devnum = int(field.split('=')[1])
            elif 'cfg' in field:
                self.cfg = int(field.split('=')[1])
            elif 'intf' in field:
                self.intf = int(field.split('=')[1])
            elif 'path' in field:
                self.path = field.split('=')[1].strip('"')
            elif 'alt' in field:
                self.alt = int(field.split('=')[1])
            elif 'name' in field:
                self.name = field.split('=')[1].strip('"')
            elif 'serial' in field:
                self.serial = DfuInfo.parse_serial(field.split('=')[1].strip('"'))

    @staticmethod
    def parse_serial(raw_serial):
        '''
        Takes a raw serial string and parses out each of the fields as found

        @param raw_serial: iSerial string taken from dfu-util or udev
        '''
        # Spaces are used to separate most of the fields
        # Example serials:
        # 5335310050464D4B3530343232333033 - sam4s4b:04E3 | 697E97E3A186C5DF - nRF52810:0015
        # 5335310050464D4B3530343232333033 - sam4s4b:04E3
        # 5335310050464D4B3530343232333033 - sam4s4b
        raw_serial_split = raw_serial.split(' ')

        # Check if bootloader has firmware revision (sam4s4b:FFFF vs sam4s4b)
        chip_rev = raw_serial_split[2].split(':')
        chip = raw_serial_split[2]
        mcu_revision = None
        if len(chip_rev) > 1:
            chip = chip_rev[0]
            mcu_revision = int(chip_rev[1], 16) # Hex value

        # Check if ble fields are present
        ble_serial = None
        ble_chip = None
        ble_revision = None
        if len(raw_serial_split) > 5:
            ble_serial = raw_serial_split[4]
            ble_chip_rev = raw_serial_split[6].split(':')
            ble_chip = ble_chip_rev[0]
            ble_revision = int(ble_chip_rev[1], 16)

        rdict = {
            'serial': raw_serial_split[0], # Isolate main serial number
            'chip': chip,
            'mcu_revision': mcu_revision,
            'ble_serial': ble_serial,
            'ble_chip': ble_chip,
            'ble_revision': ble_revision
        }

        return rdict


class Dfu:
    '''
    Wrapper class around dfu-util.
    With some udev assistance.

    @param path: Platform specific USB port identifier, autodetected if set to None
    @param vid: VID of the target device, autodetected if set to None
    @param pid: PID of the target device, autodetected if set to None
    @param serial: Serial of the main MCU, autodetected if set to None
    @param ble_serial: Serial of the BLE MCU, autodetected if set to None (and is available)
    '''
    def __init__(self, path=None, vid=None, pid=None, serial=None, ble_serial=None):
        # Build a command to filter out the dfu interface as much as possible
        dfu_cmd = [dfu_bin, '--list']

        # Apply filters
        if path:
            dfu_cmd.extend(['--path', path])
        if vid and pid:
            dfu_cmd.extend(['--device', '{}:{}'.format(vid, pid)])
        # Serial filter must be applied after building the list

        # Gather found interfaces
        found_interfaces = []
        logger.info("Gathering DFU interfaces: %s", dfu_cmd)
        dfu_result = subprocess.run(dfu_cmd, capture_output=True, check=True)
        for line in dfu_result.stdout.decode().split('\n'):
            if 'Found DFU:' in line:
                found_interfaces.append(DfuInfo(line))

        # No devices found
        if len(found_interfaces) == 0:
            logger.error("No matching DFU devices were found.")
            raise FileNotFoundError("No matching DFU devices were found.")

        # Make sure there is only one device found
        # AltSettings are ok
        path_found = None
        filtered_interfaces = []
        for intf in found_interfaces:
            # Match serial numbers
            if serial and serial != intf.serial['serial']:
                continue
            if ble_serial and ble_serial != intf.serial['ble_serial']:
                continue

            # First interface
            if not path_found:
                path_found = intf.path
                filtered_interfaces.append(intf)
                continue

            # Ignore duplicate USB device paths
            if intf.path == path_found:
                filtered_interfaces.append(intf)
                continue

            logger.error("More than one DFU device matched the criteria: %s", found_interfaces)
            raise LookupError("More than one DFU device matched the criteria")

        # Store identified DFU interfaces
        self.interfaces = filtered_interfaces

    def dfu_interfaces(self):
        '''
        Returns a list of located interfaces based on the filter parameters of the object
        If duplicate interfaces are returned, these correspond to AltSettings available in DFU
        '''
        return self.interfaces

    @classmethod
    def from_udev(cls, vid=None, pid=None, serial=None, ble_serial=None):
        '''
        Locate DFU interface with udev
        Optionally use the main serial number or BLE serial number to filter for a specific device.
        The advantage of using udev is that udev rules don't needed to be set in order to build a list of devices.
        (helps with debugging)
        '''
        # Setup udev connection
        context = pyudev.Context()

        # Look for dfu devices
        devices = context.list_devices(subsystem='usb').match_attribute('bInterfaceClass', 'fe').match_attribute('bInterfaceSubClass', '01')

        # Apply vid filter if set
        if vid:
            devices = devices.match_attribute('idVendor')

        # Apply pid filter if set
        if pid:
            devices = devices.match_attribute('idProduct')

        # Iterate over found devices to apply more filters
        found_devices = []
        for device in devices:
            # Read serial number and parse out specific pieces
            parsed_serial = DfuInfo.parse_serial(device.parent.attributes.asstring('serial'))

            # Read vid:pid in case it was not a filter
            dev_vid = device.parent.attributes.asstring('idVendor')
            dev_pid = device.parent.attributes.asstring('idProduct')

            # Check serial filters
            if serial and serial != parsed_serial['serial']:
                continue
            if ble_serial and ble_serial != parsed_serial['ble_serial']:
                continue

            found_devices.append((device, (dev_vid, dev_pid), parsed_serial))

        # Fail when no devices, or more than 1 device is found
        if len(found_devices) == 0:
            logger.error('No matching DFU devices were found.')
            raise FileNotFoundError('No matching DFU devices were found.')

        # Show devices
        logger.info("--- DFU devices found ---")
        for device in found_devices:
            logger.info(device)

        if len(found_devices) > 1:
            logger.error("More than one DFU device matched the criteria: %s", found_devices)
            raise LookupError('More than one DFU device matched the criteria')

        dev = found_devices[0]

        return cls(path, vid=dev[1][0], pid=dev[1][1], serial=dev[2]['serial'], ble_serial=dev[2]['ble_serial'])

    @timeit
    def download(self, file, alt=0):
        '''
        Uses dfu-util to download file from host to device

        @param file: File to download
        @param alt: Altsetting to use (defaults to 0)
        '''
        # Acquire specifics on DFU interface
        vid = self.interfaces[0].vid
        pid = self.interfaces[0].pid
        path = self.interfaces[0].path

        # Build command and run
        dfu_cmd = [
            dfu_bin,
            '--alt', '{}'.format(alt),
            '--device', '{}:{}'.format(vid, pid),
            '--path', path,
            '--download', file
        ]
        logger.info('Using dfu-util to download file from host: %s', dfu_cmd)
        subprocess.run(dfu_cmd, check=True)

    @timeit
    def upload(self, file, file_size=None, alt=0):
        '''
        Uses dfu-util to upload file from device to host

        @param file: File to upload
        @param file_size: Size of file to upload (by default uploads entire flash
        @param alt: Altsetting to use (defaults to 0)
        '''
        # Acquire specifics on DFU interface
        vid = self.interfaces[0].vid
        pid = self.interfaces[0].pid
        path = self.interfaces[0].path

        # Build command and run
        dfu_cmd = [
            dfu_bin,
            '--alt', '{}'.format(alt),
            '--device', '{}:{}'.format(vid, pid),
            '--path', path,
            '--upload', file
        ]
        if file_size:
            dfu_cmd.extend(['--upload-size', '{}'.format(file_size)])
        logger.info('Using dfu-util to upload file to host: %s', dfu_cmd)
        subprocess.run(dfu_cmd, check=True)

    @timeit
    def reset(self, alt=0):
        '''
        Detach the given DFU interface.
        This will chip reset the given interface.
        Resetting alt=0 generally jumps back to firmware.
        NOTE: This is not dfu-util --reset, that only calls for a reset after downloading or uploading commands.

        @param alt: Altsetting for the interface
        '''
        # Acquire specifics on DFU interface
        vid = self.interfaces[0].vid
        pid = self.interfaces[0].pid
        path = self.interfaces[0].path

        # Build command and run
        dfu_cmd = [
            dfu_bin,
            '--alt', '{}'.format(alt),
            '--device', '{}:{}'.format(vid, pid),
            '--path', path,
            '--detach'
        ]
        logger.info('Using dfu-util to detach and interface/reset: %s', dfu_cmd)
        subprocess.run(dfu_cmd, check=True)

    @timeit
    def samba_bootloader(self):
        '''
        On supported Input Club DFU bootloader jump back to the built-in bootloader.
        This is used to update the chip bootloader without having to open up the device.
        '''
        # Acquire specifics on DFU interface
        vid = self.interfaces[0].vid
        pid = self.interfaces[0].pid
        path = self.interfaces[0].path
        chip = self.interfaces[0].serial['chip']
        revision = self.interfaces[0].ver
        serial = self.interfaces[0].serial['serial']
        name = self.interfaces[0].name

        # Make sure this is an Input Club device
        # NOTE: Only works with Input Club bootloaders
        if vid != 0x308f:
            raise NotImplementedError('Not a valid vid:pid {}:{}'.format(vid, pid))

        # Make sure this is a SAM4S device
        if 'sam4s' not in chip:
            raise NotImplementedError('Not implemented for {}'.format(chip))

        # Check for minimum version number (must be over revision 1233)
        #
        # commit 3584c35e041a4efb122878b6bfd6986285f4d08f
        # Author: Jacob Alexander <haata@kiibohd.com>
        # Date:   Sun May 3 21:46:10 2020 -0700
        #
        # Adding bootloader reset from dfu
        #
        # - When dfu-util -D <serial number> is sent
        # The bootloader will unset the GPNVM bits and jump to the SAM-BA
        # bootloader
        # - This is permanent until the bits are set back
        #
        if revision < 1233:
            raise NotImplementedError("{vid}:{pid} - {name} - {serial} -> Bootloader version is too old, must be updated manually) {revision} < {match}".format(
                vid=vid,
                pid=pid,
                name=name,
                serial=serial,
                revision=revision,
                match=1233,
            ))

        # Convert serial number into a binary file
        # We must also swap the byte order
        serial_num = serial.split(' - ')[0] # Isolate serial number
        serial_num_words = textwrap.wrap(serial_num, 8) # Split into 32-bit words
        serial_num_words_int = [int(x, 16) for x in serial_num_words] # Convert hex strings into integers

        # Write serial number as binary to a temporary file
        with tempfile.NamedTemporaryFile() as tf:
            for word in serial_num_words_int:
                tf.write(bytes(struct.unpack('4B', struct.pack('I', word))))
            tf.seek(0)

            # Write dfu-suffix
            dfu_cmd = [dfu_suffix_bin, '--vid', '{:04x}'.format(vid), '--pid', '{:04x}'.format(pid), '--add', tf.name]
            logger.info("Apply dfu-suffix to temporary file: %s", dfu_cmd)
            subprocess.run(dfu_cmd, check=True)

            # Write temporary file to dfu-util
            dfu_cmd = [
                dfu_bin,
                '--alt', '0', # Always alt 0
                '--device',
                '{}:{}'.format(vid, pid),
                '--path', path,
                '--download', tf.name]
            logger.info("Using dfu-util to send boot switch command: %s", dfu_cmd)
            subprocess.run(dfu_cmd, check=True)
