# SNOWSKY ECHO USB DAC · 24 位 / 192 kHz

这是基于 SNOWSKY ECHO V2.0.0 的社区固件补丁。它把 USB DAC 的双声道 PCM 输入从原厂的 16 位／48 kHz 扩展到最高 24 位／192 kHz。当前补丁的 USB 设备修订号为 `0x0209`。

## 支持的格式

| USB 速率 | 16 位 PCM | 24 位 PCM |
| --- | --- | --- |
| High Speed · USB Audio Class 2 | 48、96、192 kHz | 48、96、192 kHz |
| Full Speed · USB Audio Class 1 | 48、96 kHz | 48 kHz |

Windows 实机独占测试打开了全部六档 High Speed 格式。每档的稳态测量均无新增缓冲溢出或欠载。实机耳机试听确认 0209 消除了 24 位／192 kHz 测试版中的电流音和降调。Full Speed 回退经过固件仿真验证。

## 构建 0209

1. 从飞傲下载 **SNOWSKY ECHO V2.0.0**，将解压得到的 `ECHOV200.IMG` 放在仓库根目录。构建脚本会验证原厂镜像的 SHA-256。 [飞傲 V2.0.0 下载页](https://www.fiio.com/newsinfo/1151351.html)
2. 在仓库根目录运行以下命令。Windows x64 上使用 Python 3.14 和 Keystone 0.9.2 完成了复现。

```sh
python -m pip install keystone-engine==0.9.2
python -B patch_echo_uac_multiformat.py --high-speed-192-packed ECHOV200.IMG build/ECHOV200.IMG
```

| 镜像 | SHA-256 |
| --- | --- |
| 原厂 V2.0.0 | `c6a3b6c86b141c0a428771f8a611fbd44cc12d561acb033a98640b0bf24f8d57` |
| 补丁 0209 | `9b3bda90f795e37b0e18a4f9d073af440130ceb33f70435c964386777b2ce4b3` |

## 更新与回退

先备份 ECHO 的内置存储。飞傲的升级说明提示，更新可能格式化内置存储。将构建得到的 `build/ECHOV200.IMG` 复制到 ECHO **内置存储的根目录**，安全弹出设备后重启，等待自动更新完成。需要回到原厂版本时，使用原厂 `ECHOV200.IMG` 走同一更新流程。 [飞傲更新步骤](https://forum.fiio.com/note/showNoteContent.do?floor=&id=202602051101498161611)

更新后进入 USB DAC 模式。在 Windows 的「设置 → 系统 → 声音」中选择 **ECHO**，再选择所需格式。

## 验证

仿真运行镜像中的 Thumb 指令，并模拟 USB 与 DMA 外设。覆盖格式切换、24 位低位保留、环形缓冲跨界、192 kHz 的 DAC 与 I²S 配置，以及 USB 停流恢复。

```sh
python -m pip install unicorn==2.1.4
python -B ECHOV200-analysis/evidence/verify-uac-recovery.py build/ECHOV200.IMG
```

## 补丁实现

- [构建器](patch_echo_uac_multiformat.py)生成 UAC 描述符、安装播放路径补丁并重算 Rockchip CRC32。
- [播放路径](uac_multiformat.S)管理 USB 收包、环形缓冲和 DMA 交接。
- [24 位／192 kHz 解包](uac_packed192.S)将每个双声道样本帧直接转换到环形缓冲。仿真中，处理一个 288 字节包的接收指令数从 2,303 降到 945。

## 许可证

补丁源码采用 Apache License 2.0。 [许可证](LICENSE)

