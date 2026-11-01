# 无人机机械臂研究：Astra Pro Plus 桌面定位

当前任务：固定相机观测静态桌面物体，先验证采集，再学习三维定位。暂不涉及飞行控制或真实机械臂运动。

## 当前状态（2026-11-01）

- Ubuntu 22.04.5 LTS / x86_64 / VMware；内核 6.8.0-138-generic。
- 相机：Orbbec Astra Pro Plus，彩色 USB `2bc5:050f`，深度 USB `2bc5:060f`；Windows 对照后两设备均已重新直通 Ubuntu，彩色 uvcvideo 绑定正常。
- SDK：官方 OpenNI 2.3.0.86-beta6 Linux x64，项目内解压；实际运行库报告 2.3.0.86。
- 深度 NiViewer 已启动，用户确认有深度画面；保存了真实 16 位原始深度。
- 阶段 1 尚未通过：Ubuntu 彩色 V4L2 图异常，官方 libuvc 两轮能打开但未返回帧；用户已确认 Windows 原生外接彩色正常、连续更新，优先排查虚拟机采集链路。深度接收速率也低于标称，详见 [实测记录](docs/environment-2026-11-01.md)。
- 已读取工厂参数并实测深度选点、SDK XYZ 转换和无效点拒绝；完整几何校正仍未通过，详见 [阶段2记录](docs/stage2-depth-point-2026-11-01.md)。
- 按用户决定暂缓 RGB 排查。相机后移至合适距离后，固定场景的深度前景自动定位已完成一次真实“无书包 → 放回书包自动分割与XYZ → 移走后坐标失效”流程。见 [阶段3本次验收](docs/stage3-backpack-auto-2026-11-01.md)。这是新增前景定位，不是书包类别识别；RGB–Depth 对齐及绝对精度测试尚未完成。此前背景失配失败记录保留于 [早期诊断](docs/stage3-depth-foreground-2026-11-01.md)。
- OpenNI 实际只暴露深度和红外流，不暴露彩色流；NiViewer 中的黑白画面不代表 UVC 彩色采集已验证。
- 已新增同一程序中的深度+红外实时预览及原始深度记录，实机验证可边看边保存；这不代表 RGB 彩色采集或完整精度验收已通过。
- 当前优先使用单深度实时模式。用户确认放稳后，完成300帧短段及1800帧约一分钟记录：约30 FPS、无全零帧或序号缺口，中心Z为898–901 mm，后者时间样本标准差约1.50 mm。详见 [单深度记录](docs/depth-only-stability-2026-11-01.md)。这是当前场景的重复读数变化，不是绝对误差。
- 在相机后移、用户指认书包后，对固定像素 `(419,292)` 完成两轮各300帧 SDK XYZ 重复性记录：有效样本 Z 为735–741 mm、标准差约1.12/1.15 mm，但分别6.67%/12%样本被拒绝，仍需改进单点间歇失效。详见 [书包固定点记录](docs/backpack-point-repeatability-2026-11-01.md)。量尺对照由用户决定延后，绝对准确度未验证。
- 用户随后决定继续 RGB 排查。已停止深度流，使用现有 GStreamer 对照：MJPG 320×240 的30个原始缓冲均不可解码（15个全零），YUYV低分辨率未交付完整帧。宿主机为 Workstation 17.5.0 build-22583795，待用户进行 EHCI→xHCI 单一条件对照，尚未改控制器。详见 [RGB 排查断点与重启后续做](docs/rgb-resume-2026-11-01.md)。当前不能认定 Ubuntu 22.04 不支持或相机损坏。

## 目录

- `camera/astra_pro_plus/`：本设备采集与诊断代码。
- `scripts/`：环境检查、局部环境准备、编译、官方示例启动。
- `docs/`：环境实测、SDK 来源与版本、权限规则、阶段验收。
- `third_party/`：本地 SDK 与运行库，不进入 Git。
- `data/`：原始深度、彩色图及实测日志，不进入 Git。
- `logs/`、`build/`、`.venv/`：本地产物，不进入 Git。

## 官方依据与选择

[OpenNI 支持列表](https://github.com/orbbec/OpenNI_SDK/blob/main/README.md)明确包含 Astra Pro Plus。
[官方发布包](https://github.com/orbbec/OpenNI_SDK/releases/tag/v2.3.0.86-beat6)提供 Linux x64 包；注意发布标签拼作 `beat6`。
[Orbbec SDK 支持表](https://github.com/orbbec/OrbbecSDK)列明此设备不支持 Orbbec SDK v2，因此目前只使用 OpenNI。
[安装说明](https://github.com/orbbec/OpenNI_SDK/blob/main/Doc/English/0100_Installation.md)描述 Linux x64、USB 2.0 和 freeglut 依赖；对 Ubuntu 22.04 的兼容性以实测为准，不能仅由老文档推定。

包内官方 ColorReaderUVC 用 libuvc 读彩色；[官方 FAQ](https://github.com/orbbec/OpenNI_SDK/blob/main/Doc/English/0500_Product%20Support.md)也说明 UVC 彩色不能直接由 OpenNI 彩色流 API 读取，可用 OpenCV VideoCapture。深度和彩色分别采集，不自动意味着像素对齐或硬件同步。

## 环境准备

从仓库目录运行以下命令；实际开发时由代理直接执行，README 用于复现。

```bash
bash scripts/check_environment.sh
bash scripts/setup_local.sh
```

准备脚本只在项目内下载并解压 SDK 和 Ubuntu freeglut3 运行库、编译深度诊断程序。无需运行 SDK 的安装脚本。
SHA-256、下载地址及来源说明见 [SDK 清单](docs/sdk_manifest.json)。SDK 哈希是本次下载后的本地记录，并非厂商签名。

使用项目 `.venv`（Python 3.10.12），通过 `--system-site-packages` 读取系统已有 OpenCV 4.5.4 和 NumPy 1.21.5；当前未使用 pip，未改动系统 Python。
这是复用系统库的隔离项目解释器，系统包升级仍会影响这两项版本。
准备脚本要求已有 curl、unzip、dpkg-deb、g++、Python venv/OpenCV/NumPy；本机已具备。
网络使用当前进程环境，不永久设置代理；下载失败先检查代理是否可达，不禁用 TLS 校验。

## USB 权限

深度规则已获用户批准并应用：`/etc/udev/rules.d/99-astra-pro-plus.rules`，仅允许 plugdev 组访问 `060f`。
需要管理员在本地认证；密码不进入聊天或仓库。

```bash
sudo bash scripts/apply_usb_rule.sh
```

彩色 raw USB 规则 [99-astra-pro-plus-color.rules](docs/99-astra-pro-plus-color.rules)已获用户明确批准并应用，供官方 libuvc 示例使用，权限为 0660/plugdev/uaccess。
对应系统文件为 `/etc/udev/rules.d/99-astra-pro-plus-color.rules`。普通 V4L2 彩色访问也已有桌面用户 ACL。
脚本会重新加载规则并仅触发相应 PID，不运行全设备权限修改或 `chmod 777`。

## 官方预览与采集

深度设备同一时刻只由一个程序占用；先关闭 NiViewer，再运行深度探针。官方示例按任意键或退出窗口结束。

```bash
bash scripts/run_official.sh NiViewer
bash scripts/run_official.sh DepthReaderPoll
# 本机已批准并应用彩色 raw USB 权限
bash scripts/run_official.sh ColorReaderUVC
# 官方双流示例；对齐结果必须另行验证
bash scripts/run_official.sh SimpleViewer 1 0
```

查询当前设备实际暴露给 OpenNI 的传感器和流模式（不启动流、不写设置）：

```bash
# 若 NiViewer 正占用深度设备，先关闭它
bash scripts/build_sensor_inventory.sh
build/sensor_inventory
```

本包预编译 SimpleViewer 的 OpenCV 4.2 依赖旧版 `libpng12.so.0`，本机缺失，当前未启动该双流示例，也没有为此引入旧系统包。NiViewer 和 ColorReaderUVC 不受此依赖影响。
SimpleViewer 包含镜像、软件对齐和补洞选项；官方演示效果不等于我们已验证对齐，原始数据使用下面的独立深度探针保存。
不要运行 ExtendedAPI 的写 Flash、激光控制、固件等交互选项。

## 保存真实原始深度和诊断日志

### 黑色区域与近距离诊断

```bash
.venv/bin/python camera/astra_pro_plus/depth_coverage.py --output data/depth-coverage-new --frames 300
```

实时显示所有非零深度，并将原始零值、小于 600 mm、600–4000 mm、大于 4000 mm 分色；可点击墙面像素并保存同帧原始 PGM。详见 [黑色区域诊断](docs/depth-coverage-2026-11-01.md)。该模式不输出 XYZ，也不认证近距离读数准确度。

### 固定表面像素的 SDK XYZ 重复性

```bash
MPLCONFIGDIR="$PWD/logs/matplotlib" .venv/bin/python camera/astra_pro_plus/point_repeatability.py \
  --selection data/depth-coverage-chair-backpack-retreat-001/user_click_0004.json \
  --output data/backpack-point-repeatability-new --frames 300
```

只在相机与目标自指认以来保持不动时复用该选择文件。程序检查流配置、预热并核对初始深度，边预览边记录同一像素、原始邻域与 SDK XYZ；拒绝样本坐标为空。首次及复测结果、运行方式与限制见 [固定点记录](docs/backpack-point-repeatability-2026-11-01.md)。

### 边预览边记录（推荐用于调整目标位置）

```bash
bash scripts/build_ir_preview.sh
.venv/bin/python camera/astra_pro_plus/scene_preview.py --depth-only
```

当前优先使用单深度流模式，窗口只显示实时深度，避免在双流异常未定位时同时开启红外。中央黄色框是固定采样区，尚不是物体分割掩膜。
相机应放稳，朝向约 0.8–1 米外的哑光墙面或纸板；无需手持。按 C 记录随后 300 个新深度帧，预览继续显示；按 Q/Esc 退出。
去掉 `--depth-only` 可进入深度+红外双流诊断模式，该模式曾在连续运行中出现全零深度与低强度 IR，尚未通过稳定性验收。
红外显示现使用固定的 0–1023 原始强度计数色阶（仅显示参数，不是传感器标定值），可用 `--ir-display-max` 改变。曾使用的自动百分位拉伸会放大暗场噪声，因此已移除。
不要同时打开 NiViewer 或其他占用相机的程序。
窗口显示的中位深度来自框中有效点，只有区域主要属于同一表面时才有相应意义；不能当成物体中心或抓取点。

记录保存到新建的 `data/live-日期时间/`，包括：

- 首帧、中间帧及末帧原始 uint16 深度 PGM。
- 每帧时间戳、中心像素及中央区域统计 CSV。
- 未滤波的逐帧中央深度块 `central_depth_patches.npy`。
- 实际流配置、记录完成状态，以及当时最新的 IR 原始图。

双流模式下 IR 与深度独立更新，尚未证明曝光同步，也没有 RGB 对齐声明；单深度模式不创建 IR 流。
用户调整位置时的短段数据只能作诊断；静态重复性测量需对准后另起一段。
程序也支持由代理通过 `--control` 指定的本地 JSON 发起记录，因此用户可保持预览并直接在聊天说“已对好”。
控制文件位于本地 logs，不进入 Git；捕获目录已存在时会拒绝覆盖。
退出时若记录未达到请求帧数，会保留部分数据并明确标记 complete=false。

### 单独采集深度

```bash
bash scripts/build_depth_probe.sh
mkdir -p data/depth-session-001
build/depth_probe data/depth-session-001 900
.venv/bin/python camera/astra_pro_plus/analyze_depth.py data/depth-session-001
```

探针使用 SDK 默认实际流配置，打印可用模式，并记录实际分辨率、像素格式、尺度、镜像和注册状态。首帧、中间帧和末帧均保存，末帧为 `depth_raw.pgm`（无缩放、无滤波的 16 位大端 PGM），不能用伪彩色文件替代计算输入。
0 是无效深度；SDK 的 min/max 表示驱动/编码范围，不保证整个范围都有可用精度。
900 帧在掉帧或低速时会超过 30 秒；单次最多 5 次等待失败后退出。探针输出目录应新建，已有深度日志时会拒绝覆盖。
`depth_preview.png` 仅为 0–5 m 固定色标预览，黑色为无效像素，计算必须读原始 PGM。
分析脚本默认排除最初 1 秒设备时间再统计中央区域；短于 1 秒的功能验证记录不会产生静态统计，不应把无样本解释成零抖动。

## 彩色通道诊断

```bash
.venv/bin/python camera/astra_pro_plus/uvc_check.py --seconds 30 --preview --output data/color-session-001
# 带宽/格式对比；请使用新输出目录
.venv/bin/python camera/astra_pro_plus/uvc_check.py --seconds 10 --fourcc YUYV --width 320 --height 240 --output data/color-session-002
```

报告记录解码后图像、协商配置和主机读取速率。读取计数可能包含损坏/重复帧，不能用它冒充真实传感器 FPS。
V4L2 底层阻塞读取可能超过 `--seconds`，需要硬截止时在外部使用 `timeout`。
彩色 PNG 与单独运行的深度 PGM 不是同步 RGB-D 帧对；当前未声明 RGB–Depth 对齐。

## 阶段推进

1. 接入与预览：两 USB 设备已识别，深度已预览；彩色质量、双流稳定性和可用 RGB-D 数据仍未通过。
2. 手动深度选点：基础功能已实机验证，读取了实际参数，使用 SDK 转换并明确镜像、单位和轴方向；工厂主点与SDK模型差异及畸变状态仍待几何验证。
3. 简单物体自动定位：当前固定相机/椅子场景中，新增书包前景自动分割、掩膜内有效表面选点、SDK 坐标及移走后拒绝输出的基础流程已实机通过一次。泛化能力尚未验收，不是类别识别或抓取点估计。
4. 误差验证：已记录固定像素重复性与失效率；绝对准确度对照按用户意见延后，不阻塞当前自动分割学习。
5. 机械臂准备：硬件和安装方式明确后标定，明确定义坐标、单位、时间与质量信息。

## Git

本目录已克隆并关联用户的 GitHub 仓库。SDK、现场图像和原始采集默认不上传。
代码、项目脚本和阶段验收记录通过独立分支及 Pull Request 提交，由仓库所有者审查合并。SDK、构建产物、现场图像、原始采集、日志与虚拟环境由 `.gitignore` 排除。
本次提交使用已连接 GitHub 账号的身份；本机若需配置提交作者，仅使用仓库级 `git config user.name/user.email`，无需修改全局。
