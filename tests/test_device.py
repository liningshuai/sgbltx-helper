"""A2 / A11：设备选择与分辨率核对。

这些是纯函数，不连接任何设备，因此是确定性测试。
"""

from __future__ import annotations

import pytest

from sgbltx_helper.contracts import DeviceError, DeviceProfile
from sgbltx_helper.device import AdbDeviceInfo, check_resolution, select_device


def profile(serial: str = "emulator-5554", resolution: tuple[int, int] = (1080, 1920)) -> DeviceProfile:
    return DeviceProfile(
        profile_id="test",
        account_alias="main",
        adb_path="C:/leidian/LDPlayer14/adb.exe",
        serial=serial,
        package_name="com.sgbltx.goodgame",
        expected_resolution=resolution,
    )


def dev(address: str, name: str = "雷电模拟器") -> AdbDeviceInfo:
    return AdbDeviceInfo(name=name, adb_path="C:/leidian/LDPlayer14/adb.exe", address=address)


class TestSelectDevice:
    def test_no_device_refuses(self) -> None:
        with pytest.raises(DeviceError, match="未发现任何 ADB 设备"):
            select_device(profile(), [])

    def test_empty_serial_refuses_even_with_single_device(self) -> None:
        """不默认选择第一个设备：即使只有一台，档案未指定也必须拒绝。"""
        with pytest.raises(DeviceError, match="未指定设备序列号"):
            select_device(profile(serial=""), [dev("emulator-5554")])

    def test_empty_serial_lists_candidates(self) -> None:
        with pytest.raises(DeviceError) as exc:
            select_device(profile(serial=""), [dev("emulator-5554"), dev("emulator-5556", "雷电模拟器-1")])
        assert "emulator-5554" in str(exc.value)
        assert "emulator-5556" in str(exc.value)

    def test_multiple_devices_without_profile_refuses(self) -> None:
        devices = [dev("emulator-5554"), dev("emulator-5556"), dev("emulator-5558")]
        with pytest.raises(DeviceError, match="未指定设备序列号"):
            select_device(profile(serial=""), devices)

    def test_unknown_serial_refuses(self) -> None:
        with pytest.raises(DeviceError, match="不在当前设备列表中"):
            select_device(profile(serial="emulator-9999"), [dev("emulator-5554")])

    def test_exact_serial_returns_that_device(self) -> None:
        devices = [dev("emulator-5554"), dev("emulator-5556", "雷电模拟器-1")]
        chosen = select_device(profile(serial="emulator-5556"), devices)
        assert chosen.address == "emulator-5556"

    def test_localhost_normalized_to_loopback(self) -> None:
        chosen = select_device(profile(serial="localhost:5555"), [dev("127.0.0.1:5555")])
        assert chosen.address == "127.0.0.1:5555"

    def test_duplicate_match_refuses(self) -> None:
        devices = [dev("emulator-5554"), dev("EMULATOR-5554")]
        with pytest.raises(DeviceError, match="匹配到多台"):
            select_device(profile(serial="emulator-5554"), devices)


class TestCheckResolution:
    def test_match_passes(self) -> None:
        check_resolution(profile(resolution=(1080, 1920)), (1080, 1920))

    def test_mismatch_refuses(self) -> None:
        with pytest.raises(DeviceError, match="分辨率不匹配"):
            check_resolution(profile(resolution=(1080, 1920)), (720, 1280))

    def test_unknown_resolution_refuses(self) -> None:
        """首次截图前 controller.resolution 返回 (0,0)，必须拒绝而不是放行。"""
        with pytest.raises(DeviceError, match="无法读取设备分辨率"):
            check_resolution(profile(), (0, 0))
