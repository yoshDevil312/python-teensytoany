import os
import subprocess
from contextlib import contextmanager
from time import sleep
from typing import Sequence
from warnings import warn

from packaging.version import Version
from serial import LF, Serial
from serial.tools.list_ports import comports

__all__ = ["TeensyToAny"]


class TeensyToAny:
    # I've noticed that this is extremely slow. Simply asking the device
    # for the version number seems to take 100 ms exactly.

    # %timeit teensytoany._ask('version')
    # 101 ms

    # This was due to it timeing out on the reads instead of stopping when it
    # received the newline character.

    # Currently, the roundtrip time is about 6ms. This is with version 0.0.1-2
    # of the firmware.
    VID_PID_s = [
        (0x16C0, 0x0483),
    ]

    _device_name = "TeensyToAny"

    @staticmethod
    def find(serial_numbers=None):
        """Find all the serial ports that are associated with debuggers.

        Parameters
        ----------
        serial_numbers: list of str, or None
            If provided, will only match the serial numbers that are contained
            in the provided list.

        Returns
        -------
        devices: list of serial devices
            List of serial devices

        Note
        ----
        If a list of serial numbers is not provided, then this function may
        match Teensy 3.1/3.2 microcontrollers that are connected to the
        computer but that may not be associated with the TeensyToAny boards.

        """
        pairs = TeensyToAny.device_serial_number_pairs(serial_numbers=serial_numbers)
        devices, _ = zip(*pairs)
        return devices

    @staticmethod
    def list_all_serial_numbers(
        serial_numbers=None,
        *,
        device_name=None,
        manufacturer="TeensyToAny",
    ):
        """Find all the currently connected TeensyToAny serial numbers.

        Parameters
        ----------
        serial_numbers: list of str, or None
            If provided, will only match the serial numbers that are contained
            in the provided list.

        Returns
        -------
        serial_numbers: list of serial numbers
            List of connected serial numbers.

        Note
        ----
        If a list of serial numbers is not provided, then this function may
        match Teensy 3.1/3.2 microcontrollers that are connected to the
        computer but that may not be associated with the TeensyToAny boards.

        """
        pairs = TeensyToAny.device_serial_number_pairs(
            serial_numbers=serial_numbers,
            device_name=device_name,
            manufacturer=manufacturer,
        )
        _, serial_numbers = zip(*pairs)
        return serial_numbers

    @staticmethod
    def get_latest_available_firmware_version(
        *, mcu="TEENSY40", online=True, local=True, timeout=2
    ):
        latest = None
        if local:
            local_versions = TeensyToAny._find_local_versions(mcu=mcu)
            if len(local_versions) > 0:
                latest = local_versions[-1]
        try:
            if online:
                latest = TeensyToAny._get_latest_available_firmware_online(
                    timeout=timeout
                )
        except Exception:  # pylint: disable=broad-except
            pass

        if latest is None:
            raise RuntimeError(
                "Failed to fetch the latest release information. "
                "Please check your internet connection and try again."
            )

        return latest

    @staticmethod
    def _get_latest_available_firmware_online(*, timeout=2):
        import requests  # pylint: disable=import-outside-toplevel

        repo_url = "https://api.github.com/repos/ramonaoptics/teensy-to-any"
        releases_url = f"{repo_url}/releases/latest"

        response = requests.get(releases_url, timeout=timeout)

        if response.status_code != 200:
            raise RuntimeError(
                "Failed to fetch the latest release information. "
                f"Status code: {response.status_code}"
            )
        release_data = response.json()
        latest_release_version = release_data["tag_name"]

        return latest_release_version

    @staticmethod
    def _device_serial_number_pairs(
        serial_numbers=None,
        *,
        device_name=None,
        manufacturer="TeensyToAny",
    ):
        warn(
            "The TeensyToAny._device_serial_number_pairs function is deprecated. "
            "Use TeensyToAny.device_serial_number_pairs instead.",
            stacklevel=2,
        )
        return TeensyToAny.device_serial_number_pairs(
            serial_numbers=serial_numbers,
            device_name=device_name,
            manufacturer=manufacturer,
        )

    @staticmethod
    def device_serial_number_pairs(
        serial_numbers=None,
        *,
        device_name=None,
        manufacturer="TeensyToAny",
    ):
        if device_name is None:
            device_name = TeensyToAny._device_name
        com = comports()
        pairs = [
            (c.device, c.serial_number)
            for c in com
            if (
                (c.vid, c.pid) in TeensyToAny.VID_PID_s
                and (
                    (serial_numbers is None and c.manufacturer == manufacturer)
                    or (serial_numbers and c.serial_number in serial_numbers)
                )
            )
        ]
        if len(pairs) == 0:
            raise RuntimeError(f"Could not find any {device_name} device.")
        return pairs

    @property
    def mcu(self):
        return self._ask("mcu")

    def _update_firmware(
        self, *, mcu=None, variant: str = None, force=False, timeout=2
    ):
        current_version = self.version
        serial_number = self.serial_number
        if mcu is None:
            mcu = self.mcu

        if mcu is None:
            raise RuntimeError(
                "The current microcontroller is unknown, please specify it "
                "before attempting to update the firmware."
            )

        latest_version = self.get_latest_available_firmware_version(
            mcu=mcu, timeout=timeout
        )
        if not force:
            if Version(current_version) >= Version(latest_version):
                return

        self.close()
        self._requested_serial_number = serial_number
        try:
            self.program_firmware(
                serial_number,
                mcu=mcu,
                version=latest_version,
                variant=variant,
                timeout=timeout,
            )
            # Reraise any exceptions that were caught
        finally:
            self.open()

    @staticmethod
    def _find_local_versions(*, mcu=None):
        firmware_dir = TeensyToAny._generate_firmware_directory(mcu=mcu)
        if not firmware_dir.is_dir():
            return []

        versions = [
            d.name
            for d in firmware_dir.iterdir()
            if d.is_dir() and (d / "firmware.hex").is_file()
        ]

        versions.sort(key=Version)
        return versions

    @staticmethod
    def _generate_firmware_filename(*, mcu, version, variant: str = None):
        firmware_dir = TeensyToAny._generate_firmware_directory(mcu=mcu)
        if variant is None:
            firmware_filename = firmware_dir / f"{version}" / "firmware.hex"
        else:
            firmware_filename = firmware_dir / f"{version}" / f"firmware_{variant}.hex"
        return firmware_filename

    @staticmethod
    def _generate_firmware_directory(*, mcu):
        from pathlib import Path  # pylint: disable=import-outside-toplevel

        from appdirs import AppDirs  # pylint: disable=import-outside-toplevel

        app = AppDirs("teensytoany", "ramonaoptics")
        cache_dir = Path(app.user_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        firmware_dir = cache_dir / f"{mcu.lower()}"
        firmware_dir.mkdir(parents=True, exist_ok=True)
        return firmware_dir

    @staticmethod
    def download_firmware(*, mcu, version, variant: str = None, timeout=2):
        firmware_filename = TeensyToAny._generate_firmware_filename(
            mcu=mcu, version=version, variant=variant
        )

        import requests  # pylint: disable=import-outside-toplevel

        release_url = f"https://github.com/ramonaoptics/teensy-to-any/releases/download/{version}/"
        if variant is None:
            file_url = release_url + f"firmware_{mcu.lower()}.hex"
        else:
            file_url = release_url + f"firmware_{mcu.lower()}_{variant}.hex"
        response = requests.get(file_url, timeout=timeout)
        if response.status_code != 200:
            raise RuntimeError("Failed to download firmware")

        firmware_filename.parent.mkdir(parents=True, exist_ok=True)
        # Open the file for binary writing
        with open(firmware_filename, "wb") as file:
            # Write the content to the file in chunks
            for chunk in response.iter_content(chunk_size=4096):
                file.write(chunk)

        return firmware_filename

    @staticmethod
    def program_firmware(
        serial_number=None,
        *,
        mcu=None,
        version=None,
        variant: str = None,
        verbose=False,
        wait=False,
        timeout=2,
    ):
        if serial_number is None:
            available_serial_numbers = TeensyToAny.list_all_serial_numbers()
            if len(available_serial_numbers) == 0:
                raise RuntimeError("No TeensyToAny devices found.")
            if len(available_serial_numbers) > 1:
                raise RuntimeError(
                    "Multiple TeensyToAny devices found. Please specify the "
                    "serial number of the device you would like to program."
                )
            serial_number = available_serial_numbers[0]

        if mcu is None:
            raise RuntimeError("mcu must be provided and cannot be left as None.")

        if version is None:
            version = TeensyToAny.get_latest_available_firmware_version(timeout=timeout)

        if os.name == "nt":
            # We do supporting updating, but it is "scary" to do so since
            # there is no serial number specificity
            raise RuntimeError(
                "We do not supporting programing TeensyToAny devices on Windows"
            )

        firmware_filename = TeensyToAny._generate_firmware_filename(
            mcu=mcu, version=version, variant=variant
        )

        if not firmware_filename.is_file():
            TeensyToAny.download_firmware(
                mcu=mcu, version=version, variant=variant, timeout=timeout
            )

        if verbose:
            verbose = [
                "-v",
            ]
        else:
            verbose = []

        if wait:
            wait = [
                "-w",
            ]
        else:
            wait = []
        cmd_list = (
            [
                "teensy_loader_cli",
                "-s",
            ]
            + verbose
            + wait
            + [
                f"--mcu={mcu}",
                f"--serial-number={serial_number}",
                str(firmware_filename),
            ]
        )

        subprocess.check_call(cmd_list)
        # Wait for the device to reboot
        sleep(1)

    def __init__(
        self,
        serial_number=None,
        *,
        baudrate=115200,
        timeout=0.205,
        open=True,  # pylint: disable=redefined-builtin
    ):
        """A class to control the TeensyToAny Debugger.

        Parameters
        ----------
        serial_number: optional
            If provided, will attempt to open the specified serial number

        timeout: float
            Timeout before reading a command fails. A default value of 0.205
            was chosen so that the Serial connection adequately waits for
            hardware timeouts that may be as long as 0.200 seconds.

        """

        self._requested_serial_number = serial_number
        self._baudrate = baudrate
        self._timeout = timeout
        self._serial = None
        self.serial_number = None
        self._version = None
        if open:
            self.open()

    def open(self):
        try:
            self._open()
        except Exception as e:
            self.close()
            raise e

    def __enter__(self):
        """Context manager for opening the device."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Context manager for closing the device."""
        self.close()
        if exc_type is not None:
            raise exc_value.with_traceback(traceback)
        return False

    def _open(self):
        if self._requested_serial_number is None:
            serial_numbers = None
        else:
            serial_numbers = [self._requested_serial_number]

        port, found_serial_number = self.device_serial_number_pairs(
            serial_numbers=serial_numbers, device_name=self._device_name
        )[0]

        self._serial = Serial(port=port, baudrate=self._baudrate, timeout=self._timeout)
        self.serial_number = found_serial_number

        # Ignore other commands that might be pending?
        self._serial.reset_output_buffer()
        self._serial.reset_input_buffer()
        self._serial.flush()

        # Cache the version number so we don't keep asking it for speed
        response_version = self._ask("version")
        self._version = response_version
        good_version = False
        try:
            good_version = Version(self.version) > Version("0.0.0")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        if not good_version:
            raise RuntimeError(
                f"Unkown version '{response_version}'. "
                "Please contact Ramona Optics for help with this error."
            )

    def close(self):
        if self._serial is not None:
            self._serial.close()

        self._serial = None
        self.serial_number = None
        self._version = None

    def __del__(self):
        # Do we want to call close on this delete instance????
        self.close()

    def _write(self, data) -> None:
        """Write data to the port.

        If it is a string, encode it as 'utf-8'.
        """

        if self._serial is None:
            raise RuntimeError("Device must be opened first")

        if isinstance(data, str):
            data = (data + "\n").encode("utf-8")
        self._serial.write(data)

    def _read(self, *, size=1024, decode=True) -> str:
        """Read data from the serial port.

        Returns
        -------
        data: bytes or string
            string of data read.

        """
        if self._serial is None:
            raise RuntimeError("Device must be opened first")

        data = self._serial.read_until(LF, size=size)

        if decode:
            data = data.decode()

        return data

    def _ask(self, data, *, size=1024, decode=True) -> str:
        self._write(data)
        returned = self._read(size=size, decode=decode)
        if len(returned) == 0:
            raise RuntimeError(f"Failed to read a response for command: {data}")

        returned_list = returned.split(" ", 1)
        error = returned_list[0]
        message = None if len(returned_list) == 1 else returned_list[1]
        error = int(error)
        if error != 0:
            if message is None:
                message = os.strerror(error)
            raise RuntimeError(f"Responded with Error Code {error}: {message}")

        if message is not None:
            message = message.strip()
        return message

    def i2c_init(self, baud_rate: int = 100_100, timeout=200_000, register_space=1):
        cmd = f"i2c_init {baud_rate:d} {timeout:d} {register_space:d}"
        self._ask(cmd)

    def i2c_read_uint8(self, address: int, register_address: int):
        cmd = f"i2c_read_uint8 0x{address:02x} 0x{register_address:x}"
        returned = self._ask(cmd)
        return int(returned, base=0)

    def i2c_read_uint16(self, address: int, register_address: int):
        cmd = f"i2c_read_uint16 0x{address:02x} 0x{register_address:x}"
        returned = self._ask(cmd)
        return int(returned, base=0)

    def i2c_write_uint8(self, address: int, register_address: int, data: int):
        data = data & 0xFF
        cmd = f"i2c_write_uint8 0x{address:02x} 0x{register_address:x} 0x{data:x}"
        self._ask(cmd)

    def i2c_write_uint16(self, address: int, register_address: int, data: int):
        data = data & 0xFFFF
        cmd = f"i2c_write_uint16 0x{address:02x} 0x{register_address:x} 0x{data:x}"
        self._ask(cmd)

    def i2c_write_read(self, address: int, data: Sequence, num_bytes: int) -> Sequence:
        if len(data) != 2:
            raise ValueError("data must be of length 2")
        if num_bytes not in [1, 2]:
            raise ValueError("Can only read 1 or 2 bytes at a time.")

        register_address = int.from_bytes(data, byteorder="big", signed=False)
        if num_bytes == 2:
            returned = self._ask(
                f"i2c_read_uint16 0x{address:02x} 0x{register_address:04x}"
            )
        else:
            returned = self._ask(
                f"i2c_read_uint8 0x{address:02x} 0x{register_address:04x}"
            )
        register_data = int(returned, base=0)
        # The other I2C function has this interface
        return int.to_bytes(
            int(register_data), length=num_bytes, byteorder="big", signed=False
        )

    def i2c_write_payload(
        self, address: int, register_address: int, payload: Sequence
    ) -> None:
        if Version(self.version) >= Version("0.0.14"):
            data = " ".join([f"0x{val:02x}" for val in payload])
            cmd = f"i2c_write_payload 0x{address:02x} 0x{register_address:02x} {data}"
            self._ask(cmd)

        else:
            if len(payload) == 1:
                # Trying to write the network chips
                data = int(payload[0])
                self._ask(f"i2c_write_no_register_uint8 0x{address:02x} 0x{data:02x}")
            elif len(payload) == 3:
                # uint8
                register_address = int.from_bytes(
                    payload[0:2], byteorder="big", signed=False
                )
                data = int.from_bytes(payload[2:3], byteorder="big", signed=False)
                self._ask(
                    f"i2c_write_uint8 "
                    f"0x{address:02x} 0x{register_address:04x} 0x{data:02x}"
                )
            elif len(payload) == 4:
                register_address = int.from_bytes(
                    payload[0:2], byteorder="big", signed=False
                )
                data = int.from_bytes(payload[2:4], byteorder="big", signed=False)
                self._ask(
                    f"i2c_write_uint16 "
                    f"0x{address:02x} 0x{register_address:04x} 0x{data:04x}"
                )
            else:
                raise NotImplementedError()

    def i2c_read_payload(
        self, address: int, register_address: int, num_bytes: int
    ) -> Sequence:
        if Version(self.version) < Version("0.0.14"):
            if num_bytes != 1:
                raise NotImplementedError()
            returned = self._ask(f"i2c_read_no_register_uint8 0x{address:02x}")
            register_data = int(returned, base=0)
            return int.to_bytes(
                int(register_data), length=num_bytes, byteorder="big", signed=False
            )

        cmd = f"i2c_read_payload 0x{address:02x} 0x{register_address:02x} {num_bytes}"
        returned = self._ask(cmd)
        register_data = [
            int(val, base=0) for val in returned.split()
        ]  # returns big endian
        return register_data

    def i2c_read_payload_no_register(self, address: int, num_bytes: int):
        cmd = f"i2c_read_payload_no_register 0x{address:02x} {num_bytes}"
        returned = self._ask(cmd)
        register_data = [
            int(val, base=0) for val in returned.split()
        ]  # returns big endian
        return register_data

    def i2c_ping(self, address: int):
        """Return None if device found. Raises error if no device found."""
        cmd = f"i2c_ping 0x{address:02x}"
        self._ask(cmd)

    def i2c_begin_transaction(self, address: int):
        """Begin a transaction with the I2C device. This is required before writing or
        reading data."""
        cmd = "i2c_begin_transaction 0x{address:02x}"
        self._ask(cmd)

    def i2c_write(self, address: int, data: Sequence) -> None:
        """Write data to the I2C device."""
        if not isinstance(data, Sequence):
            raise TypeError("data must be a sequence of integers")
        data_str = " ".join([f"0x{val:02x}" for val in data])
        cmd = f"i2c_write 0x{address:02x} {data_str}"
        self._ask(cmd)

    def i2c_end_transaction(self):
        """End a transaction with the I2C device. This is required after writing or
        reading data."""
        cmd = "i2c_end_transaction"
        self._ask(cmd)

    def i2c_1_init(self, baud_rate: int = 100_100, timeout=200_000, register_space=1):
        cmd = f"i2c_1_init {baud_rate:d} {timeout:d} {register_space:d}"
        self._ask(cmd)

    def i2c_1_read_uint8(self, address: int, register_address: int):
        cmd = f"i2c_1_read_uint8 0x{address:02x} 0x{register_address:x}"
        returned = self._ask(cmd)
        return int(returned, base=0)

    def i2c_1_read_uint16(self, address: int, register_address: int):
        cmd = f"i2c_1_read_uint16 0x{address:02x} 0x{register_address:x}"
        returned = self._ask(cmd)
        return int(returned, base=0)

    def i2c_1_write_uint8(self, address: int, register_address: int, data: int):
        data = data & 0xFF
        cmd = f"i2c_1_write_uint8 0x{address:02x} 0x{register_address:x} 0x{data:x}"
        self._ask(cmd)

    def i2c_1_write_uint16(self, address: int, register_address: int, data: int):
        data = data & 0xFFFF
        cmd = f"i2c_1_write_uint16 0x{address:02x} 0x{register_address:x} 0x{data:x}"
        self._ask(cmd)

    def i2c_1_write_read(
        self, address: int, data: Sequence, num_bytes: int
    ) -> Sequence:
        if len(data) != 2:
            raise ValueError("data must be of length 2")
        if num_bytes not in [1, 2]:
            raise ValueError("Can only read 1 or 2 bytes at a time.")

        register_address = int.from_bytes(data, byteorder="big", signed=False)
        if num_bytes == 2:
            returned = self._ask(
                f"i2c_1_read_uint16 0x{address:02x} 0x{register_address:04x}"
            )
        else:
            returned = self._ask(
                f"i2c_1_read_uint8 0x{address:02x} 0x{register_address:04x}"
            )
        register_data = int(returned, base=0)
        # The other I2C function has this interface
        return int.to_bytes(
            int(register_data), length=num_bytes, byteorder="big", signed=False
        )

    def i2c_1_write_payload(
        self, address: int, register_address: int, payload: Sequence
    ) -> None:
        if Version(self.version) >= Version("0.0.14"):
            data = " ".join([f"0x{val:02x}" for val in payload])
            cmd = f"i2c_1_write_payload 0x{address:02x} 0x{register_address:02x} {data}"
            self._ask(cmd)

        else:
            if len(payload) == 1:
                # Trying to write the network chips
                data = int(payload[0])
                self._ask(f"i2c_1_write_no_register_uint8 0x{address:02x} 0x{data:02x}")
            elif len(payload) == 3:
                # uint8
                register_address = int.from_bytes(
                    payload[0:2], byteorder="big", signed=False
                )
                data = int.from_bytes(payload[2:3], byteorder="big", signed=False)
                self._ask(
                    f"i2c_1_write_uint8 "
                    f"0x{address:02x} 0x{register_address:04x} 0x{data:02x}"
                )
            elif len(payload) == 4:
                register_address = int.from_bytes(
                    payload[0:2], byteorder="big", signed=False
                )
                data = int.from_bytes(payload[2:4], byteorder="big", signed=False)
                self._ask(
                    f"i2c_1_write_uint16 "
                    f"0x{address:02x} 0x{register_address:04x} 0x{data:04x}"
                )
            else:
                raise NotImplementedError()

    def i2c_1_read_payload(
        self, address: int, register_address: int, num_bytes: int
    ) -> Sequence:
        cmd = f"i2c_1_read_payload 0x{address:02x} 0x{register_address:02x} {num_bytes}"
        returned = self._ask(cmd)
        register_data = [
            int(val, base=0) for val in returned.split()
        ]  # returns big endian
        return register_data

    def i2c_1_read_payload_no_register(self, address: int, num_bytes: int):
        cmd = f"i2c_1_read_payload_no_register 0x{address:02x} {num_bytes}"
        returned = self._ask(cmd)
        register_data = [
            int(val, base=0) for val in returned.split()
        ]  # returns big endian
        return register_data

    def i2c_1_ping(self, address: int):
        """Return None if device found. Raises error if no device found."""
        cmd = f"i2c_1_ping 0x{address:02x}"
        self._ask(cmd)

    def i2c_1_begin_transaction(self, address: int):
        """Begin a transaction with the I2C device. This is required before writing or
        reading data."""
        cmd = "i2c_1_begin_transaction 0x{address:02x}"
        self._ask(cmd)

    def i2c_1_write(self, address: int, data: Sequence) -> None:
        """Write data to the I2C device."""
        if not isinstance(data, Sequence):
            raise TypeError("data must be a sequence of integers")
        data_str = " ".join([f"0x{val:02x}" for val in data])
        cmd = f"i2c_1_write 0x{address:02x} {data_str}"
        self._ask(cmd)

    def i2c_1_end_transaction(self):
        """End a transaction with the I2C device. This is required after writing or
        reading data."""
        cmd = "i2c_1_end_transaction"
        self._ask(cmd)

    def gpio_digital_write(self, pin, value):
        """Call the ardunio DigitalWrite function.

        Parameters
        ----------
        pin: int
            Pin number to control.
        value: 0, 1, "HIGH", "LOW"
            Value to assign to the pin

        """
        self._ask(f"gpio_digital_write {pin} {value}")

    def gpio_pin_mode(self, pin, mode, value=None):
        """Call the arduino PinMode function.

        Parameters
        ----------
        pin: int
            Pin number to control.
        mode: 0, 1, "INPUT", "OUTPUT"
            Mode which to set the pin to.
        value: None, 0, 1, "HIGH", "LOW"
            Value to assign to the pin. The value will be set if it is not None.

        """
        if value is None:
            cmd = f"gpio_pin_mode {pin} {mode}"
        else:
            cmd = f"gpio_pin_mode {pin} {mode} {value}"

        self._ask(cmd)

    def gpio_digital_pulse(self, pin, value, *, duration, value_end=None):
        """Call the ardunio DigitalWrite function.

        Parameters
        ----------
        pin: int
            Pin number to control.
        value: 0, 1, "HIGH", "LOW"
            Value to assign to the pin
        duration: float
            The duration of the pulse in seconds.
        value_end:
            The value of the pin at the end of the pulse. If not provided, it
            will be the opposite of the value at the beginning of the pulse.

        """

        if value_end is None:
            if isinstance(value, str):
                if value.upper() == "LOW":
                    value_end = "HIGH"
                else:
                    value_end = "LOW"
            elif not value:
                value_end = 1
            else:
                # I want the "safe default" to be "low"
                value_end = 0

        cmd = f"gpio_digital_pulse {pin} {value} {value_end} {duration}"

        # We want to ensure that the command won't timeout
        # For this, we check that the pulse duration is less than
        # 80% of the time, or provide a 50 ms buffer. Whichever is bigger.
        maximum_duration = max(self._timeout * 0.8, self._timeout - 50e-3)
        if duration > maximum_duration:
            with self.increased_timeout(duration + 0.1):
                self._ask(cmd)
        else:
            self._ask(cmd)

    def gpio_digital_read(self, pin):
        """Call the arduino DigitalRead function.

        Parameters
        ----------
        pin: int
            Pin number to read.

        Returns
        -------
        value: int, 0, 1
            Read value.

        """
        returned = self._ask(f"gpio_digital_read {pin}")
        return bool(int(returned, base=0))

    def register_write_uint16(self, register_address, value):
        """Write value directly to teensy register.

        Parameters
        ----------
        register_address: int
            Register address to write to.
        value: int
            Value to write to address. Should be between 0 and 65535.
        """
        self._ask(f"register_write_uint16 {register_address} {value}")

    def register_read_uint16(self, register_address):
        """Read value directly from a teensy register.

        Parameters
        ----------
        register_address: int
            Register address to write to.

        Returns
        -------
        value: int
            Read value. Will be from 0 to 65535.

        """
        returned = self._ask(f"register_read_uint16 {register_address}")
        return int(returned, base=0)

    @property
    def version(self):
        return self._version

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        if self._serial is not None:
            self._serial.timeout = value
        self._timeout = value

    @contextmanager
    def increased_timeout(self, value):
        old_timeout = self.timeout
        try:
            self.timeout = value
            yield
        finally:
            self.timeout = old_timeout

    def spi_begin(self):
        self._ask("spi_begin")

    def spi_end(self):
        self._ask("spi_end")

    def spi_set_miso(self, pin):
        self._ask(f"spi_set_miso {pin}")

    def spi_set_mosi(self, pin):
        self._ask(f"spi_set_mosi {pin}")

    def spi_set_sck(self, pin):
        self._ask(f"spi_set_sck {pin}")

    def spi_settings(
        self,
        frequency: int = 1_000_000,
        bit_order: str = "MSBFIRST",
        data_mode: str = "SPI_MODE0",
    ):
        """

        Parameters
        ----------
        bit_order: 'MSBFIRST' or 'LSBFIRST'
        data_mode: 'SPI_MODE0', 'SPI_MODE1', 'SPI_MODE2', or 'SPI_MODE3'

        References
        ----------
          1. https://www.pjrc.com/teensy/td_libs_SPI.html
          2. https://www.arduino.cc/en/Reference/SPISetDataMode

        """
        return self._ask(f"spi_settings {frequency} {bit_order} {data_mode}")

    def spi_begin_transaction(self):
        return self._ask("spi_begin_transaction")

    def spi_end_transaction(self):
        return self._ask("spi_end_transaction")

    def spi_transfer(self, data):
        return self._ask(f"spi_transfer {data}")

    def spi_transfer_bulk(self, data):
        returned = self._ask(
            "spi_transfer_bulk " + " ".join(str(d) for d in data)
        ).split(" ")
        return [int(i, base=0) for i in returned]

    def spi_read_byte(self, data):
        """Read a byte of data over SPI.

        Parameters
        ----------
        data: int
            Command or register address to send before reading.

        Returns
        -------
        value: int, (0-255)
            Read value.

        """
        value = self._ask(f"spi_read_byte {data}")
        return int(value, base=0)

    def analog_write_frequency(self, pin: int, frequency: int):
        frequency = int(frequency)
        self._ask(f"analog_write_frequency {pin} {frequency}")

    def analog_write_resolution(self, resolution: int):
        resolution = int(resolution)
        self._ask(f"analog_write_resolution {resolution}")

    def analog_write(self, pin: int, value: int):
        # https://www.arduino.cc/reference/en/language/functions/analog-io/analogwrite/
        value = int(value)
        self._ask(f"analog_write {pin} {value}")

    def analog_pulse(
        self, pin: int, value: int, *, duration: float, value_end: int = 0
    ):
        """Pulse the analog value for a specific duration of time

        Parameters
        ----------
        pin: int
            Pin number to control.
        value: int
            Value to write to analogWrite during the pulse
        duration: float
            The duration of the pulse in seconds.
        value_end: int
            The value at the end of the pulse.

        """

        cmd = f"analog_pulse {pin} {value} {value_end} {duration}"
        # We want to ensure that the command won't timeout
        # For this, we check that the pulse duration is less than
        # 80% of the time, or provide a 50 ms buffer. Whichever is bigger.
        maximum_duration = max(self._timeout * 0.8, self._timeout - 50e-3)
        if duration > maximum_duration:
            with self.increased_timeout(duration + 0.1):
                self._ask(cmd)
        else:
            self._ask(cmd)

    def analog_read(self, pin: int):
        """Call the arduino analogRead function.

        Parameters
        ----------
        pin: int
            Pin number to read.

        Returns
        -------
        value: int, (0-255)
            Read value.

        """
        returned = self._ask(f"analog_read {pin}")
        return int(returned, base=0)

    def eeprom_read_uint8(self, index: int) -> int:
        """Read an 8-bit unsigned integer from the EEPROM.

        Parameters
        ----------
        index: int
            The index in the EEPROM to read the uint8 value.

        Returns
        -------
        int
            The 8-bit unsigned integer read from the EEPROM.

        Raises
        ------
        ValueError
            If the index is out of bounds.
        """
        cmd = f"eeprom_read_uint8 {index}"
        returned = self._ask(cmd)
        return int(returned, base=0)

    def eeprom_write_uint8(self, index: int, data: int):
        """Write an 8-bit unsigned integer to the EEPROM.

        Parameters
        ----------
        index: int
            The index in the EEPROM to write the uint8 value.
        data: int
            The 8-bit unsigned integer to write to the EEPROM.

        Returns
        -------
        None

        Raises
        ------
        ValueError
            If the index is out of bounds or the data is too long to fit in the EEPROM.
        """
        cmd = f"eeprom_write_uint8 {index} {data}"
        self._ask(cmd)

    def startup_commands_available(self):
        """Return the number of startup commands available."""
        returned = self._ask("startup_commands_available")
        return int(returned, base=0)

    def read_startup_command(self, index):
        """Read the startup command at the specified index."""
        returned = self._ask(f"read_startup_command {index}")
        return returned

    def demo_commands_available(self):
        """Return the number of demo commands available."""
        returned = self._ask("demo_commands_available")
        return int(returned, base=0)

    def read_demo_command(self, index):
        """Read the demo command at the specified index."""
        returned = self._ask(f"read_demo_command {index}")
        return returned

    def demo_commands_enabled(self):
        """Return whether the demo commands are enabled on startup."""
        returned = self._ask("demo_commands_enabled")
        return bool(int(returned, base=0))

    def enable_demo_commands(self):
        """Enable the demo commands on startup."""
        self._ask("enable_demo_commands")

    def disable_demo_commands(self):
        """Disable the demo commands on startup."""
        self._ask("disable_demo_commands")

    def fastled_add_leds(self, led_class, has_white, pin, n_leds):
        has_white = int(bool(has_white))
        self._ask(f"fastled_add_leds {led_class} {has_white} {pin} {n_leds}")

    def fastled_set_brightness(self, brightness):
        self._ask(f"fastled_set_brightness {brightness}")

    def fastled_get_brightness(self):
        returned = self._ask("fastled_get_brightness")
        return int(returned, base=0)

    def fastled_show(self, brightness=None):
        if brightness is not None:
            self._ask(f"fastled_show {brightness}")
        else:
            self._ask("fastled_show")

    def fastled_set_rgb(self, led_index, r, g, b):
        self._ask(f"fastled_set_rgb {led_index} {r} {g} {b}")

    def fastled_set_hsv(self, led_index, h, s, v):
        self._ask(f"fastled_set_hsv {led_index} {h} {s} {v}")

    def fastled_set_hue(self, led_index, hue):
        self._ask(f"fastled_set_hue {led_index} {hue}")
