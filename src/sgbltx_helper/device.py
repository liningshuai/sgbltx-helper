"""设备发现与明确选择。

设计说明 6/8：不得默认选择第一个设备；先核对分辨率再做任何输入。
本模块的选择逻辑是**纯函数**，便于确定性测试。
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import DeviceError, DeviceProfile, ReasonCode


@dataclass(frozen=True)
class AdbDeviceInfo:
    """一个候选 ADB 设备。"""

    name: str
    adb_path: str
    address: str
    screencap_methods: int = 0
    input_methods: int = 0

    def describe(self) -> str:
        return f"{self.address}  ({self.name})  adb={self.adb_path}"


def discover_devices(adb_path: str | None = None) -> list[AdbDeviceInfo]:
    """通过 MaaFramework Toolkit 搜索设备。

    优先使用 Maa 官方工具包（与控制器同一套探测逻辑），失败时返回空列表并
    由调用方决定如何提示——本项目不使用裸 ADB CLI 作为运行依赖。
    """
    from maa.toolkit import Toolkit

    try:
        found = Toolkit.find_adb_devices(adb_path) if adb_path else Toolkit.find_adb_devices()
    except Exception:  # noqa: BLE001 - 探测失败不应让 CLI 崩栈
        return []

    return [
        AdbDeviceInfo(
            name=d.name,
            adb_path=str(d.adb_path),
            address=d.address,
            screencap_methods=int(d.screencap_methods),
            input_methods=int(d.input_methods),
        )
        for d in found
    ]


def _normalize(address: str) -> str:
    """把 emulator-5554 / 127.0.0.1:5555 / localhost:5555 归一化后比较。"""
    addr = address.strip().lower()
    if addr.startswith("localhost:"):
        addr = "127.0.0.1:" + addr.split(":", 1)[1]
    return addr


def select_device(profile: DeviceProfile, devices: list[AdbDeviceInfo]) -> AdbDeviceInfo:
    """从候选设备中挑出档案指定的那一台。

    Raises:
        DeviceError: 无设备 / 档案未指定序列号 / 指定序列号不存在 / 匹配不唯一。
    """
    if not devices:
        raise DeviceError(
            "未发现任何 ADB 设备：请确认模拟器已启动、ADB 调试已开启。",
            ReasonCode.NO_DEVICE,
        )

    wanted = _normalize(profile.serial)
    if not wanted:
        listing = "；".join(d.describe() for d in devices)
        raise DeviceError(
            "档案未指定设备序列号（profile.device.serial 为空），拒绝执行。"
            f"当前可用设备：{listing}。请把其中一台的地址填入 config/local.json 后重试。",
            ReasonCode.DEVICE_NOT_SPECIFIED,
        )

    matched = [d for d in devices if _normalize(d.address) == wanted]
    if not matched:
        listing = "；".join(d.describe() for d in devices)
        raise DeviceError(
            f"档案指定的设备 {profile.serial!r} 不在当前设备列表中，拒绝执行。"
            f"当前可用设备：{listing}。",
            ReasonCode.DEVICE_NOT_FOUND,
        )
    if len(matched) > 1:
        raise DeviceError(
            f"档案指定的设备 {profile.serial!r} 匹配到多台，拒绝执行。",
            ReasonCode.DEVICE_NOT_SPECIFIED,
        )

    return matched[0]


def check_resolution(profile: DeviceProfile, actual: tuple[int, int]) -> None:
    """核对实际分辨率。不支持的分辨率必须拒绝执行。"""
    expected = profile.expected_resolution
    if actual == (0, 0):
        raise DeviceError(
            "无法读取设备分辨率（首次截图前该值无效），拒绝执行。",
            ReasonCode.RESOLUTION_MISMATCH,
        )
    if tuple(actual) != tuple(expected):
        raise DeviceError(
            f"分辨率不匹配：档案 {expected[0]}x{expected[1]}，"
            f"实际 {actual[0]}x{actual[1]}。拒绝执行，避免在错误坐标上操作。",
            ReasonCode.RESOLUTION_MISMATCH,
        )
