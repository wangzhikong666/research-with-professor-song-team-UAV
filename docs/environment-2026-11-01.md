# 实机检查与阶段 1 记录

最新补充：本页包含早期 RGB 失败历史；用户切换至 xHCI 后，彩色正常预览恢复并已做双路短测，详见 [xHCI结果与限制](rgb-xhci-validation-2026-11-01.md)。

检查日期：2026-11-01（用户时区 Asia/Tokyo）。以下均为当前 Ubuntu 实际检查；没有使用模拟相机数据。

## 环境事实

- 工作区：`/home/wangzhikong666/文档/ChatGPT/接触作业型飞行机械臂研究`。
- 独立项目：工作区内 `research-with-professor-song-team-UAV`，已从用户 GitHub 仓库克隆。
- 工作区和祖先目录未找到适用的 AGENTS.md；仓库初始只有一行标题 README。
- Ubuntu 22.04.5 LTS，x86_64，VMware；内核 6.8.0-138-generic。
- Python 3.10.12，g++ 11.4.0，CMake 3.22.1；已有 OpenCV 4.5.4、NumPy 1.21.5、libusb 1.0.25。
- 初始未发现 OpenNI/Orbbec SDK、相应自定义 udev 规则或 freeglut 运行库。
- 会话为 Wayland，DISPLAY=:0。沙箱无法初始化 libusb，须在获准的沙箱外命令中访问实际 USB。
- 终端已有大小写代理变量；未输出潜在敏感值，未更改全局网络配置。沙箱外 Git/curl/apt 下载成功。

## USB 变化

最初只有 `2bc5:050f USB 2.0 Camera`；USB 描述符显示 UVC/MJPEG/YUY2。
用户手动在 VMware 转接第二个设备后，出现 `2bc5:060f ORBBEC Depth Sensor`。
两者均为 EHCI 480M USB 2.0，`050f` 绑定 uvcvideo。
包内厂商 udev 规则确认这两个 PID 对应 Astra Pro Plus。
当前 `/dev/video0` 为彩色视频节点，`/dev/video1` 同属该 UVC 设备；不能把后者当深度图设备。

## SDK 与权限

官方 OpenNI README 明确支持 Astra Pro Plus；选用唯一发布项 `v2.3.0.86-beat6` 中的 Linux x64 包。
来源、大小和本地校验和见 sdk_manifest.json。Orbbec SDK v2 支持表明确不支持该型号。
官方网页开发入口此次抓取超时，实际依据来自官方 GitHub README、发布 API、安装文档、FAQ 及已下载包内文件。

未运行厂商安装脚本、未修改固件。freeglut3 2.8.1-6 仅解压到项目内；没有安装整套 SDK/ROS/CUDA/YOLO。
官方包的可执行文件缺少执行位，仅对使用的示例增加当前用户执行位。
官方深度示例首先报 Access denied；经用户明确批准，仅安装 `060f` 的 0660/plugdev/uaccess 规则，随后打开成功。
一次 NiViewer 报 Resource busy 是另一深度进程尚未退出；关闭采集后再启动成功。

## 深度实测

用户确认镜头对准纸箱，且 NiViewer 有深度画面。独立 SDK 探针保存 90 帧日志和最后一帧原始数据：

| 项目 | 实测 |
| --- | --- |
| 流分辨率 | 640×480 |
| 标称帧率 | 30 FPS |
| 像素格式 | OpenNI enum 100 / DEPTH_1_MM / uint16 |
| 深度尺度 | 1 mm / raw unit |
| 默认镜像 | true |
| 注册模式 | 0 / OFF |
| SDK min/max | 0 / 10000；不是已验证物理量程 |
| 接收帧数 | 90 |
| 设备时间跨度 | 6.734174 s |
| 按设备时间计算的接收速率 | 13.2162 FPS |
| 最大相邻设备时间间隔 | 0.13335 s |
| SDK 帧序号缺口 | 2 |
| 非递增时间戳 | 0 |
| waitForAnyStream 等待失败 | 0 |
| 最后一帧有效比例 | 19.6654% |
| 中心像素 raw | 0，无效 |

数据在 `data/depth-first/`：depth_raw.pgm、depth_config.json、depth_frames.csv、depth_report.json、depth_preview.png。
原始深度以 uint16 大端 PGM 保存，已用 OpenCV IMREAD_UNCHANGED 回读确认类型、尺寸。
有效区域可见结构，大面积黑色区域不能补成目标位置。中心像素 0 不代表整幅图无有效深度。
收到帧的序号缺口不能涵盖全部时间间隔异常，仍须分析 USB/设备端节奏。
SDK 提示 USB 事件线程无法设置优先级，此警告可能影响采集，但尚未证明它是当前低速的原因。

## 彩色实测与未解决问题

1. 官方 ColorReaderUVC 初次被 050f 原始 USB 权限阻止。用户明确批准后规则已安装并应用，设备节点现为 root:plugdev、0660；再次运行能打开设备并报告流启动成功。
2. OpenCV/V4L2 MJPG 640×480、协商 30 FPS：能返回图像，但可见条纹，JPEG 解码有数据损坏警告，主机返回次数异常高。因此不是可用彩色预览。
3. YUYV 640×480、协商 30 FPS：select timeout，没有收到图像。
4. YUYV 320×240、协商 30 FPS：约 12.2 秒仅返回一帧，图中有多条绿色横带，同样失败。
5. 近期内核日志有正常枚举记录，未看到能直接归因的 USB 带宽/传输错误。没有据此改动 USB 驱动参数。

目前尚不能断言彩色故障属于相机、VMware 传输还是 UVC 接口实现。
获得 050f raw USB 权限后已完成首轮 10 秒官方 libuvc 测试：可打开并协商 640×480 MJPEG 30 FPS，日志报告 start stream success，但没有任何 Receive image data 行，程序由外部时限结束（exit 124）。这不是有效彩色取流通过。
日志位于 `logs/color-official-libuvc-001.log`。随后完成较长 30 秒对照：同样能打开并报告流启动成功，收到帧日志仍为 0，由外部时限结束（exit 124）；日志为 `logs/color-official-libuvc-002.log`。
两轮均由终端 PTY 提供 stdin、行缓冲记录 stdout/stderr，未把管道 EOF 误当成按键退出。结构化结果见 [官方彩色测试记录](color-libuvc-2026-11-01.json)。
结束后确认 ColorReaderUVC/NiViewer/DepthReaderPoll/SimpleViewer/depth_probe 均无残留进程。
libuvc 测试后，`lsusb -t` 的 050f 接口驱动栏为空；恢复 V4L2 前应重新在 VMware 转接并检查 uvcvideo 绑定，不能把此状态下的视频节点缺失另判为相机故障。
预编译 SimpleViewer 依赖旧版 libpng12.so.0，本机缺失；当前没有运行它，也未为此安装旧版系统库。
硬件排除步骤见 [彩色异常诊断](color-diagnosis.md)。用户已把彩色 USB 交回 Windows，在原生相机应用中确认外接画面正常、连续更新；此为用户报告，并非代理直接观察。Windows 实际流配置未记录，不能声称与 Ubuntu 完全相同模式。当前优先排查虚拟化/Ubuntu 采集链路，不诊断相机损坏。
对照后 Ubuntu 仅剩 060f，050f 不在 lsusb 中；虚拟 USB 为 EHCI，未见 xHCI。Vmware Tools 12.3.5.46049 是客体工具版本，宿主机 Workstation 版本尚未知。
用户已关闭 Windows 相机并将 050f 重新交给 Ubuntu。重新枚举为 Bus 002 Device 006，uvcvideo 绑定及 /dev/video0、/dev/video1 均恢复，仍使用原 EHCI 控制器。
随后复测 V4L2 MJPG 640×480、30 FPS 10 秒：仍出现 JPEG 损坏警告；保存帧近黑且有点状异常。数据位于 `data/color-after-windows-001/`，不算彩色验收通过。
为解释用户报告的两幅黑白画面，实际执行只读 sensor_inventory：OpenNI 暴露 DEPTH 和 IR，不暴露 COLOR。用户随后提供两张 NiViewer 截图，确认首张为左深度伪彩色、右红外灰度；第二张主要显示深度，底部均显示 registration off。黑白显示与权限错误是不同问题，权限错误已经解决。
截图中深度存在大面积黑色无效区，不能仅凭画面有实时变化判为定位可用。当前待用户把哑光纸箱/纸板置于中央约 0.6–1 米，采集原始深度作受控场景对照。

## 验收结论

- USB 接入：通过。
- 官方深度预览：用户确认通过。
- 深度原始数据保存、实际配置记录：通过。
- 彩色图质量、双流稳定性、同步帧对保存：未通过/未完成。
- 阶段 1 整体：未通过。阶段 2–5 尚未开始。

## 后续原理约束

深度图存储的 Z 是沿光轴的深度，针孔反投影用于无畸变像素：
`X=(u-cx)Z/fx; Y=(v-cy)Z/fy`。
该写法使用图像向右、向下的坐标约定。OpenNI SDK 文档描述其 world 坐标 Y 向上，不能直接混用公式的 Y 符号；
本机还启用了默认镜像，进入阶段 2 时必须明确处理并验证轴方向。
不得猜测内参或由同分辨率推定对齐。SDK 转换称 world 并不意味着机械臂基座坐标。
