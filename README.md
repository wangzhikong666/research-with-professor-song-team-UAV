# 无人机机械臂研究：Astra Pro Plus 桌面定位

当前任务：固定相机观测静态桌面物体，先验证采集，再学习三维定位。暂不涉及飞行控制或真实机械臂运动。

## 当前状态（2026-11-01）

- Ubuntu 22.04.5 LTS / x86_64 / VMware；内核 6.8.0-138-generic。
- 相机：Orbbec Astra Pro Plus，彩色 USB `2bc5:050f`，深度 USB `2bc5:060f`；Windows 对照后两设备均已重新直通 Ubuntu，彩色 uvcvideo 绑定正常。
- SDK：官方 OpenNI 2.3.0.86-beta6 Linux x64，项目内解压；实际运行库报告 2.3.0.86。
- 深度 NiViewer 已启动，用户确认有深度画面；保存了真实 16 位原始深度。
- Ubuntu 彩色正常预览已恢复：切换虚拟 USB 控制器到 xHCI 后，同配置 MJPG 320×240 原始缓冲30/30可解码；640×480预览30秒，485帧、读取失败0次，用户确认连续更新。实测接收约16.7 FPS，仍有1条JPEG警告，长时间稳定性未验收。见 [xHCI验证](docs/rgb-xhci-validation-2026-11-01.md)。
- 已读取工厂参数并实测深度选点、SDK XYZ 转换和无效点拒绝；完整几何校正仍未通过，详见 [阶段2记录](docs/stage2-depth-point-2026-11-01.md)。
- 深度学习期间曾按用户决定暂缓 RGB，现已恢复彩色基本预览。相机后移至合适距离后，固定场景的深度前景自动定位已完成一次真实“无书包 → 放回书包自动分割与XYZ → 移走后坐标失效”流程。见 [阶段3本次验收](docs/stage3-backpack-auto-2026-11-01.md)。这是新增前景定位，不是书包类别识别；RGB–Depth 对齐及绝对精度测试尚未完成。此前背景失配失败记录保留于 [早期诊断](docs/stage3-depth-foreground-2026-11-01.md)。
- OpenNI 实际只暴露深度和红外流，不暴露彩色流；NiViewer 中的黑白画面不代表 UVC 彩色采集已验证。
- 已新增同一程序中的深度+红外实时预览及原始深度记录，实机验证可边看边保存；这不代表 RGB 彩色采集或完整精度验收已通过。
- 此前单深度模式在用户确认放稳后，完成300帧短段及1800帧约一分钟记录：约30 FPS、无全零帧或序号缺口，中心Z为898–901 mm，后者时间样本标准差约1.50 mm。详见 [单深度记录](docs/depth-only-stability-2026-11-01.md)。这是该场景的重复读数变化，不是绝对误差。
- 在相机后移、用户指认书包后，对固定像素 `(419,292)` 完成两轮各300帧 SDK XYZ 重复性记录：有效样本 Z 为735–741 mm、标准差约1.12/1.15 mm，但分别6.67%/12%样本被拒绝，仍需改进单点间歇失效。详见 [书包固定点记录](docs/backpack-point-repeatability-2026-11-01.md)。量尺对照由用户决定延后，绝对准确度未验证。
- 用户已在 Workstation 17.5.0 build-22583795 将 USB 兼容性从2.0改为3.1并开机，实查相机均位于 xHCI、仍480M。独立 RGB 恢复后又完成并发短测：彩色15秒235帧、读取失败0次；深度300帧、无整帧全零或SDK序号缺口。两路未经硬件同步或像素对齐。旧 EHCI 失败对照保留于 [RGB历史断点](docs/rgb-resume-2026-11-01.md)，新摘要见 [验证JSON](docs/rgb-xhci-validation-2026-11-01.json)。
- 后续已读取实际内外参并启用现有 SDK 硬件 D2C，统一镜像关闭，完成3组人工图像对应点初检，偏差5/1/5像素（含点击误差）。见 [RGB–Depth初检](docs/rgbd-registration-check-2026-11-01.md)。这不是完整标定或毫米级精度验收；注册像素禁止直接套用原始深度XYZ转换，硬件同步仍未验证。
- 已实机打通“RGB 拖框 → 对应的有效原始深度点 → SDK XYZ”，保存真实 RGB、原始 uint16 深度及对应点。框内多层表面或深度不足时仍会间歇暂停输出，不能视为稳定跟踪。见 [RGB 选区定位记录](docs/rgb-roi-xyz-2026-11-01.md)。
- 已进一步增加 RGB 右键指定参考表面，优先固定原始深度像素，允许半径4像素内的合格邻点；无效输出为空，恢复后自动重新测量。同1441个实测帧中，新策略有效643帧、旧策略79帧，但原始深度仍频繁缺失。RGB开启/关闭各450帧的对照均复现局部零深度；书包表面覆盖白纸后，RGB开启时参考像素零深度从72.22%降至28%，仍未完全稳定。见 [参考表面与空洞诊断](docs/rgb-surface-reference-2026-11-01.md)。

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

RGB 选区获取三维表面点：

```bash
.venv/bin/python camera/astra_pro_plus/rgb_roi_xyz.py \
  --output data/rgb-roi-xyz-new --seconds 900
```

关闭其他相机程序后运行。左侧是真实 RGB，先左键拖框指定区域，再在框内右键点击希望定位的表面。青色圆圈为用户参考位置，绿十字是当前实测深度点经 SDK 映射后在 RGB 中的位置，白十字是对应的原始深度像素。它们不表示物体中心。右侧是原始深度伪彩色；黑色表示零深度或不在应用的600–4000 mm范围内。参考像素的原始值和实际采样偏移会在窗口显示。右键可重新选表面，C 清空，S 保存当前测量对应的 RGB、原始深度和候选点掩膜，Q 退出。

使用原始深度模式（注册关闭），以 SDK `PIXEL_D2C_REGISTRATION` 检查候选点属于 RGB 框及参考位置附近，再将其**原始深度像素**交给 SDK XYZ 转换。初次确认参考表面后，固定参考像素及初始深度约束，不再逐帧重新选择全框最大表面。邻点仅在假设局部主要属于同一表面时使用，输出明确区分原像素与邻点；始终使用当前实际测量值，不填洞、不保持旧XYZ。无效深度、邻域不足、深度改变或帧过期时，`latest.json` 输出 `valid:false, position_m:null`。选区固定在图像上，不提供类别识别或运动目标跟踪。新策略及限制见 [改进记录](docs/rgb-surface-reference-2026-11-01.md)，前一版保留在 [初版记录](docs/rgb-roi-xyz-2026-11-01.md)。

同帧对照与审计（诊断模式有额外计算开销）：

```bash
.venv/bin/python camera/astra_pro_plus/rgb_roi_xyz.py \
  --output data/reference-compare-new --compare-baseline
.venv/bin/python camera/astra_pro_plus/analyze_surface_reference.py data/reference-compare-new
```

`--restore-selection` 可读取先前保存的选区字典，恢复框和参考点击位置；它不恢复旧XYZ或旧深度，要求相机和目标仍保持原位，新会话重新获取实际深度并确认参考。

RGB–Depth 初步检查：

```bash
bash scripts/build_ir_preview.sh
.venv/bin/python camera/astra_pro_plus/rgbd_registration.py --output data/rgbd-registration-new
```

先点左侧注册深度图中的有效特征，再点右侧RGB中的同一物理特征；程序冻结同一组图像保存点检数据，不输出未经核实的注册像素XYZ。详见 [像素域、时间和标定限制](docs/rgbd-registration-check-2026-11-01.md)。

1. 接入与预览：两设备已识别，原始深度已保存，xHCI下真实彩色预览恢复、独立RGB和深度可并发短测；彩色全速/长时可靠性、硬件同步和像素对齐尚未验收，保存的数据不声明为同步注册RGB-D帧对。
2. 手动深度选点：基础功能已实机验证，读取了实际参数，使用 SDK 转换并明确镜像、单位和轴方向；工厂主点与SDK模型差异及畸变状态仍待几何验证。
3. 简单物体自动定位：当前固定相机/椅子场景中，新增书包前景自动分割、掩膜内有效表面选点、SDK 坐标及移走后拒绝输出的基础流程已实机通过一次。泛化能力尚未验收，不是类别识别或抓取点估计。
4. 误差验证：已记录固定像素重复性与失效率；绝对准确度对照按用户意见延后，不阻塞当前自动分割学习。
5. 机械臂准备：硬件和安装方式明确后标定，明确定义坐标、单位、时间与质量信息。

## Git

本目录已克隆并关联用户的 GitHub 仓库。SDK、现场图像和原始采集默认不上传。
代码、项目脚本和阶段验收记录通过独立分支及 Pull Request 提交，由仓库所有者审查合并。SDK、构建产物、现场图像、原始采集、日志与虚拟环境由 `.gitignore` 排除。
本次提交使用已连接 GitHub 账号的身份；本机若需配置提交作者，仅使用仓库级 `git config user.name/user.email`，无需修改全局。

## Astra 实时桌面搜索

在 Ubuntu 桌面打开终端，从仓库根目录运行。相机由操作者手持转动，程序通过已有的 UVC 彩色接口取图，并在当前处理帧上画中文标签。桌面原型使用 OpenCV Zoo 的 COCO 预训练 YOLOX，无需训练或准备标注数据。

本机已有 OpenCV、NumPy、Pillow 和 Noto Sans CJK。新环境需要为项目解释器提供这些依赖（Ubuntu 软件包为 `python3-opencv`、`python3-numpy`、`python3-pil`、`fonts-noto-cjk`）。首次运行前下载权重：

```bash
mkdir -p third_party/desktop-search
curl -fL 'https://huggingface.co/opencv/opencv_zoo/resolve/main/models/object_detection_yolox/object_detection_yolox_2022nov.onnx' \
  -o third_party/desktop-search/object_detection_yolox_2022nov.onnx
```

运行预览：

```bash
.venv/bin/python camera/astra_pro_plus/desktop_search.py --device /dev/video0
```

- 把鼠标放在笔记本左侧或右侧。先把镜头对准笔记本，让鼠标留在视野外。检测到笔记本时，黄色框显示「第二优先级 · 笔记本」，状态栏提示「优先查看笔记本两侧」。如桌面布置已知，可加 `--side left` 或 `--side right`。
- 手持相机转向鼠标，画面中出现鼠标时，绿色框显示「第一优先级 · 鼠标」。任何一帧识别到鼠标即记为成功，状态栏保留「已找到鼠标」，取流和逐帧画框继续运行。按 Q 或 Esc 退出，重新运行可开始下一轮。
- 暂无目标或线索时显示「继续扫描」。提示由当前画面的检测结果触发，转向由操作者执行。
- 框旁的百分比是检测置信度。只有置信度达到 60% 的检测才会画框并参与搜索判断。需要更严格时，可加 `--confidence 0.75` 等参数。
- 如果彩色节点变化，用 `v4l2-ctl --list-devices` 查找 Astra 的彩色视频节点，再传给 `--device`。终端会输出状态变化和本轮汇总。

### 同屏显示三维坐标

关闭占用相机的其他预览后，启动 RGB-D 模式：

```bash
.venv/bin/python camera/astra_pro_plus/desktop_search.py --device /dev/video0 --xyz
```

标签第二行显示相对深度相机的 XYZ，单位为米，X 向右、Y 向下、Z 向前。框内十字标记的是检测框中心附近的实测表面采样点。每个位置会用 SDK 检查原始深度像素与彩色画面的对应关系。坐标用于预览，物体中心和机械臂坐标需要另行测量或标定。

两路画面按主机收到帧的时间配对，最大时间差为 50 毫秒，曝光同步和绝对精度尚未验收。手持时可先停稳再读坐标。缺少匹配深度帧或有效测点时显示「深度不可用」，下一帧重新测量。若缺少 OpenNI 桥接库，先按已有 SDK 环境说明运行 `bash scripts/build_ir_preview.sh`。

深度设备连接、读取或坐标转换失败时，程序保留彩色识别，窗口底部显示失败阶段，终端保留 SDK 原始错误。底部也会提示等待配对、深度画面全零或目标区域缺少深度。设备连接恢复后，重新启动程序即可重新连接深度通道。

2026-11-11 实机复测：Ubuntu 重启后独立深度探针可正常打开，初始场景连续 300 帧全零。用户调整拍摄位置后，实时 RGB-D 预览处理 60 帧，60 帧配对成功且均含非零深度，14 帧为检测目标输出了 XYZ，第 16 帧识别到鼠标。此记录验证了实时识别与表面坐标链路，坐标绝对精度仍需量尺或标定目标核验。初始化失败、读取中断、SDK 转换失败及全零深度的退化显示已通过模拟故障验证。

用已有图片或视频检查识别结果时，可在同一个程序中用 `--image` 或 `--video` 替换相机输入：

```bash
.venv/bin/python camera/astra_pro_plus/desktop_search.py \
  --image /path/to/desk.jpg --headless --save-preview data/desktop-search-preview.png
```
