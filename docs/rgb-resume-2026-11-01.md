# RGB 排查断点：准备虚拟 USB 控制器对照

## 当前结论

当前不能认定 Ubuntu 22.04 不支持 Astra Pro Plus，也不能认定相机损坏。
Windows 原生外接彩色正常是用户此前确认的对照；Ubuntu 的 UVC 驱动已识别并可打开设备，异常出现在视频帧交付/组装阶段。
优先怀疑 VMware USB 直通与客体 UVC 路径的兼容/协商，尚未确定具体层，也未做原生 Ubuntu 对照来排除 Linux 驱动与设备交互问题。

用户在本轮明确选择继续排查 RGB；现有深度点选 XYZ 和固定背景前景自动定位已经可用，不必等待 RGB 才能演示三维点。RGB 用于后续指定目标的外观识别及 RGB–Depth 对应。

## 环境和已完成的只读核对

- 用户确认 Windows VMware Workstation 17 Pro：**17.5.0 build-22583795**。不是以客体 VMware Tools 版本代替宿主机版本。
- Ubuntu 22.04.5 / x86_64 / kernel 6.8.0-138-generic。
- 当前控制器为 `ehci-pci`，两路 Orbbec 均为 USB 2.0 high-speed 480M；050f 视频端点为等时传输，alternate 1–6，最大每微帧 3×1024 字节。
- `2bc5:050f` 的接口 0、1 均绑定 `uvcvideo`；`/dev/video0` 为视频捕获节点，`/dev/video1` 不作为第二路 RGB。
- 实际设备节点 `/dev/video0` ACL 已给予 wangzhikong666 读写权限。当前权限已够，不再安装权限规则。
- 停止了 PID 30335 的自动深度前景程序，正常退出；RGB 测试期间只有彩色采集，无深度流、无其他彩色占用。
- V4L2 QUERYCAP：driver=uvcvideo，capabilities=0x04200001，支持 streaming，不支持 read/write I/O，因此没有盲试 `io-mode=rw`。
- ENUM_FMT：MJPG compressed flags=0x1，YUYV flags=0；未声明 continuous bytestream。API 给出的缓冲应能按相应格式解释，不应把损坏片段当作完整彩色图。
- 当前沙箱内不显示实际 `/dev/video*`，检查节点、USB和相机测试必须使用已获授权的沙箱外执行。不能将沙箱看不到节点误报为 USB 丢失。
- 本轮 `dmesg` 读取受到运行环境限制，返回不允许操作；没有尝试改变内核日志权限或安全设置。

## 实测对照

使用已安装的 GStreamer 1.20.3，不新增 SDK 或包。

1. `data/color-gstreamer-mjpg320-001`：深度已停，mmap / MJPG / 320×240 / 30 FPS 请求，保存 30 个原始 JPEG 缓冲。0 个可单独解码，15 个全零，只有 13 种缓冲哈希；多个缓冲是没有 JPEG 起始或结束标记的片段。退出码 0 仅表示收到 30 个缓冲，不代表 RGB 通过。
2. `data/color-gstreamer-yuyv320-001`：同节点与 I/O，YUY2 / 320×240 / 30 FPS，12 秒截止，无完整缓冲，exit 124。不能将此结果解释为黑色的正常画面。
3. `data/color-gstreamer-debug-001`：5 个 MJPG 缓冲及 V4L2 bufferpool/allocator 日志。交付的长度不一且出现快速连续缓冲；日志 flags 未设置 V4L2_BUF_FLAG_ERROR，但内容仍异常，不能只凭错误位为空认定帧正常。
4. 将第一段的缓冲按顺序拼接并按 JPEG 起止标记提取，仅作为离线帧组装诊断；3 段仍不可解码。文件名 `reassembled_diagnostic_*`，明确不作为相机彩色采集验收或替代图像。
5. 新脚本的同条件基线保存在 `data/color-ehci-baseline-002`：30 个缓冲中 3 个可解码，仍有提前结束 JPEG 数据段警告。实际查看 `frame-000.png` 和 `frame-025.png`，有严重条纹、错色、大面积灰色缺失，视觉验收失败。0/30 与 3/30 的差异不代表恢复。`analysis.json`、`visual_review.json`、`capture.log`、`usb-tree.txt` 与请求时间供控制器切换后对照；结构化总记录见 [控制器基线](rgb-controller-baseline-2026-11-01.json)。
6. 用户随后要求先在现有设置再测一次。复查仍为 EHCI USB 2.0，两路设备均可见、uvcvideo 绑定、无采集进程占用；未改控制器。运行同一脚本保存于 `data/color-user-recheck-001`：30 个原始缓冲中 2 个可解码；实际查看两张 PNG，均为几乎满幅竖条、无可用场景画面。`visual_review.json` 标记 `FAILED_CORRUPT_IMAGES`，RGB 未恢复。采集正常结束并释放设备，尚未执行 xHCI 对照。

旧 OpenCV 和官方 libuvc 失败记录见 `color-diagnosis.md`、`environment-2026-11-01.md`。此次低分辨率、多采集路径对照排除了“只改 OpenCV 显示就足够”和“仅深度和彩色同时取流才异常”的简单解释，但不能区分虚拟化、客体驱动和设备实际输出的具体责任。

## 下一步：用户手动改变一个条件

尚未改变虚拟机 USB 控制器，尚无 xHCI 对照结果。建议操作：

1. 保存 Ubuntu 中未保存的工作，正常关闭 Ubuntu；不要选择挂起。
2. 在 Windows 的 VMware：虚拟机 → 设置 → 硬件 → USB 控制器。
3. 将 USB 兼容性从当前 2.0 改为可用的 3.x（17.5 界面可能显示 3.1），其余设置不变。
4. 开机，将“USB 2.0 Camera”和另一个 Orbbec/Astra 深度设备连接回 Ubuntu，保持线缆及物理 USB 口不变。
5. 告诉代理“USB 3.x 已切换并重新开机”，从本文件继续。若没有 3.x 选项，先告知，暂不升级虚拟硬件。

这是可回退的控制器对照，不是保证修复，也不是把相机升级成 USB 3 设备。相机仍显示 480M 是正常预期，关键检查根控制器是否变为 xHCI。
若无法启动或有兼容问题，在关机状态下把同一个设置恢复为 2.0，记录结果。代理不操作 Windows 界面、不自行关机。

依据：[Workstation 17 官方用户指南](https://techdocs2-prod.adobecqms.net/content/dam/broadcom/techdocs/us/en/pdf/vmware/desktop-hypervisors/workstation/vmware-workstation-pro-17-0.pdf)的 USB 控制器/等时设备章节列出 USB 2.0、3.0 和 Linux xHCI 支持；本轮可检索到该文档相关章节，整份 PDF 直接获取返回 403。它说明此项对照可行，不证明该相机的故障根因。
[Broadcom USB 故障排查](https://knowledge.broadcom.com/external/article?legacyId=774)建议先确认主机对照及设备交互，本文不据此盲目添加 VMX quirks。

## 重启后代理直接执行

工作目录：`/home/wangzhikong666/文档/ChatGPT/接触作业型飞行机械臂研究/research-with-professor-song-team-UAV`。

先只读检查 `lsusb -t`、`lsusb`、050f 的 uvcvideo 绑定、实际视频节点、ACL、占用进程。用沙箱外硬件访问；如果 node 编号改变，可通过 `ASTRA_COLOR_DEVICE` 指向确认属于 2bc5:050f 的节点。
然后复用同一低分辨率基线脚本（输出目录必须新建）：

```bash
bash scripts/capture_color_gstreamer.sh data/color-xhci-mjpg320-001 MJPG 320 240
```

脚本有限时 15 秒/目标 30 个缓冲，保存原始输入、negotiated caps 日志、设备属性、USB 树和逐缓冲分析；不改 udev、模块参数、固件或网络。
已在当前环境通过 bash 语法检查和实际基线运行；`analyze_color_buffers.py` 对先前两个真实采集目录的结果与手工核对一致。
若出现可解码图像，必须先实际查看，并验证持续更新；解码成功不等于画质、帧率或 RGB–Depth 对齐验收通过。
若仍异常，不继续批量换包。下一步根据新旧差异决定是否需要宿主机日志、相同模式的原生系统对照或厂商确认。

RGB 未修复；OpenNI 深度与既有代码/数据均保留；相机固件、全局网络、系统 Python 和 SDK 选择均未改变。
