# MPU6050 + ICM20608 双传感器融合实施流水线

## 1. 项目目标

在现有 `gui/app.py` 上增量开发，不重写 GUI，不另建第二套监视界面。项目最终需要：

1. M4 继续以 50 Hz 采集外接 MPU6050，通过 RPMsg 传给 Linux。
2. Linux 通过 SPI1 采集板载 ICM20608。
3. 自动估计两颗传感器的时间差、坐标轴对应关系、正负方向和零偏。
4. 将 ICM20608 数据转换到 MPU6050/线圈坐标系。
5. MPU6050 Y 轴饱和时，使用 ICM20608 补全 Y 轴；未饱和时允许加权融合。
6. 在现有 GUI 中显示原始数据、补偿状态、融合数据和标定质量。
7. 所有验收均使用真实 MP157、MPU6050 和 ICM20608 数据，不使用演示样本代替实机验收。

## 2. 已知硬件与软件基线

- MP157 开发板、板载 ICM20608、外接 MPU6050 和接收线圈已刚性固定。
- ICM20608 连接 SPI1：PZ0=SCK、PZ2=MOSI、PZ3=CS、PZ1=MISO。
- Linux 设备树已创建 `spi0.0`，`modalias` 为 `spi:icm20608`。
- 系统已提供 `/lib/modules/$(uname -r)/kernel/drivers/char/icm20608.ko`，但尚未加载。
- M4 当前运行 `m4_rpmsg.elf`，MPU6050 采样数据由 `/dev/ttyRPMSG0` 传输。
- MPU6050 Y 轴长期接近 `19.613 m/s²`，按饱和故障处理。
- GUI 入口是 `run_gui.sh`，主程序是 `gui/app.py`。
- Dropbear 已改为常驻 `dropbear.service`，便于 GUI 反复停止和启动。

> **Dropbear 是什么**：Dropbear 是面向嵌入式设备的轻量级 SSH 服务器/客户端实现，用来替代体积较大的 OpenSSH。开发板 OpenSTLinux 用它提供 22 端口的 SSH 服务：`dropbear` 为服务端、`dbclient` 为客户端、`dropbearkey` 用于生成主机密钥（本项目为 `/etc/dropbear/dropbear_rsa_host_key`），Mac 侧因此可以用 `ssh mp157`、`scp` 访问开发板。它版本较旧、只提供 `ssh-rsa` 主机密钥，所以 Mac 的 `~/.ssh/config` 中需要 `HostKeyAlgorithms +ssh-rsa`。本项目把服务从 socket 激活改为常驻 `dropbear.service`，避免 GUI 反复停止/启动期间出现服务未及时拉起导致的连接失败。

## 3. 总体数据流

```text
MPU6050 --I²C--> M4 --RPMsg--> Linux 采集器 --\
                                                  \
ICM20608 --SPI1----------> Linux ICM 驱动 ------> 时间对齐/标定/融合
                                                        |
                                                        v
                                              SSH 单一数据流
                                                        |
                                                        v
                                              现有 Mac GUI
```

融合和坐标标定首先放在 Linux/Mac 软件层实现，不立即修改 M4 硬实时采样主循环。这样可以先验证算法，同时降低 SPI1/GPIOZ 资源分配风险。

## 4. 阶段一：建立可回退基线

### 操作

1. 确认 Git 工作区干净。
2. 记录当前可用提交号。
3. 记录修改前的真实 MPU6050 样本、SSH 状态和 M4 状态。
4. 后续每个阶段单独提交，不将驱动、标定、融合和 GUI 改造混在一个提交中。

### 完成标准

- `git status` 无未知改动。
- 可以从基线提交恢复当前单 MPU6050 GUI。

### 阶段一执行结果（2026-09-19，已完成）

**Git 基线**

| 提交 | 说明 |
|---|---|
| `831bf0f` | `chore: establish MP157 project baseline`，代码基线；包含 `run_gui.sh`、`gui/app.py`、`tools/monitor_rpmsg_imu.py`、`tools/read_rpmsg_imu.py` 等 |
| `1c6d64c` | `docs: add dual-IMU fusion implementation pipeline`，本流水线文档 |
| `6838b00` | `docs: fix KaTeX parse error and clarify Dropbear in fusion pipeline`，公式渲染与 Dropbear 说明修正 |
| `91a0581` | `test: record phase-1 baseline (MPU6050 samples, SSH and M4 state)`，阶段一基线数据 |

阶段一记录后 `git status` 干净；`run_gui.sh --demo` 冒烟测试退出码 0、无异常。`831bf0f` 中可直接恢复单 MPU6050 GUI。

**基线数据文件**：`diagnostic_logs/phase1_baseline/`

| 文件 | 内容 |
|---|---|
| `git_state.txt` | 记录时的 HEAD、近期提交、工作区状态、基线提交中的 GUI 文件清单 |
| `ssh_state.txt` | 主机名 `ATK-MP157`、Linux `5.4.31-g886e225be`、Dropbear `active`/`enabled`、`.ssh` 权限 700/600 |
| `m4_state.txt` | `remoteproc0` 为 `running`、固件 `m4_rpmsg.elf`、`/dev/ttyRPMSG0` 存在；开机约 12.9 s 由服务自动启动 M4 |
| `mpu6050_samples.txt` | 126 帧真实 RPMsg 样本（含逐帧原始值）及统计 |

**样本统计（静止，126 帧）**

```text
accel_x: mean=  -224.21  std=25.3
accel_y: mean= 32767.00  std= 0.0   （126/126 帧饱和，已知故障）
accel_z: mean= 14559.27  std=40.9
gyro_x/y/z: mean= 609.25 / 176.43 / -39.85
temperature: 27.39 °C
累计错误：i2c=13, tx=0
```

**结论与遗留观察**

- 完成标准全部满足：基线可回退、工作区干净、真实样本与板端状态已归档。
- M4 现由 `m4-rpmsg.service` 开机自动启动，与阶段二及之后的“重启自动恢复”验收前提一致。
- `i2c_errors=13` 为 M4 启动以来的累计值（此前测试中为 0），当前逐帧读取正常；阶段二/三需继续观察是否增长，若持续增长再单独排查。

## 5. 阶段二：验证 ICM20608 Linux 驱动

### 操作

1. 手动加载 `icm20608.ko`，记录设备节点名称、主次设备号和内核日志。
2. 检查驱动的读取 ABI：文本、二进制结构体或 `ioctl`。
3. 编写 `tools/read_icm20608.py`，输出统一 SI 单位：
   - 加速度：`m/s²`；
   - 角速度：`°/s`；
   - 温度：`°C`；
   - 时间：Linux `CLOCK_MONOTONIC` 秒。
4. 新增 `tools/install_icm20608.sh` 和可回退的卸载脚本，在实机启动时自动加载模块。

### 完成标准

- 静止时 ICM20608 加速度模长接近重力加速度。
- 转动装置时三轴陀螺仪有合理响应。
- 持续读取 10 分钟无 SPI 错误、进程崩溃或 SSH 断开。

## 6. 阶段三：建立双传感器统一数据流

### 操作

1. 将现有 `tools/monitor_rpmsg_imu.py` 扩展为双传感器采集器，或新增专用 `tools/monitor_dual_imu.py`。
2. 保留 MPU6050 连续序号和 M4 时间戳。
3. 为每条 ICM20608 样本记录 Linux 单调时间戳。
4. 将 M4 时间轴映射到 Linux 单调时间轴，周期性更新偏移量。
5. 采用一条 SSH 输出流，不让 GUI 同时建立两个远程采集会话。

### 建议输出协议

```text
seq=<n> t=<seconds>
mpu_a=<x>,<y>,<z> mpu_g=<x>,<y>,<z>
icm_a=<x>,<y>,<z> icm_g=<x>,<y>,<z>
age_icm_ms=<value> mpu_sat=<mask> errors=<m4_i2c>/<m4_tx>/<spi>/<sync>
```

实际实现时保持单行帧，以避免 SSH 分包导致解析半帧。

### 完成标准

- 连续读取不少于 3000 帧。
- MPU6050 序号单调增长。
- ICM20608 样本年龄正常情况不超过 30 ms。
- 停止采集后不残留远程读取进程。

## 7. 阶段四：自动时间对齐

先对两颗传感器的陀螺仪模长进行去均值与归一化，通过互相关估计时延：

$$
\hat{\tau}
=
\underset{\tau}{\operatorname{arg\,max}}
\sum_t
\tilde{s}_{\mathrm{MPU}}(t)
\tilde{s}_{\mathrm{ICM}}(t+\tau)
$$

其中：

$$
s(t)=\left\lVert\boldsymbol{\omega}(t)\right\rVert_2
$$

角速度模长不受两个坐标系旋转关系影响，适合在轴向未知时用于时间同步。

### 完成标准

- 时延估计在多个数据窗口内稳定。
- 时间对齐后两路陀螺仪模长的相关系数显著上升。
- 超出时延或样本年龄阈值时，软件明确标记本帧不可融合。

## 8. 阶段五：通过相关性自动标定坐标系

### 标定数据采集

1. 静止 5～10 秒，估计两颗陀螺仪零偏。
2. 将刚性组件绕多个不同方向缓慢旋转 30～60 秒。
3. 要求至少三个方向上存在足够的角速度变化，避免只绕单轴旋转造成旋转矩阵不可观。

### 陀螺仪零偏

$$
\mathbf b_{\omega,s}
=
\frac{1}{N}
\sum_{k=1}^{N}
\boldsymbol{\omega}_{s,k}
$$

其中 $s\in\{\mathrm{MPU},\mathrm{ICM}\}$。

### 旋转矩阵估计

对时间对齐后的角速度求解 Wahba/Kabsch 问题：

$$
\hat{\mathbf R}
=
\underset{\mathbf R\in SO(3)}{\operatorname{arg\,min}}
\sum_k
\left\lVert
\left(\boldsymbol{\omega}_{\mathrm{MPU},k}-\mathbf b_{\omega,\mathrm{MPU}}\right)
-
\mathbf R
\left(\boldsymbol{\omega}_{\mathrm{ICM},k}-\mathbf b_{\omega,\mathrm{ICM}}\right)
\right\rVert_2^2
$$

采用 SVD 求解，并强制：

$$
\det(\mathbf R)=+1
$$

禁止将镜像反射矩阵当作合法坐标变换。

### 质量指标

- 旋转后陀螺仪三轴 RMSE。
- 验证集上每个轴的相关系数。
- 旋转数据协方差矩阵的条件数。
- 旋转矩阵正交误差：

$$
e_R=\left\lVert\mathbf R^\mathsf{T}\mathbf R-\mathbf I\right\rVert_F
$$

### 完成标准

- 程序可自动输出 ICM 轴到 MPU 轴的主要对应和正负方向。
- 旋转矩阵通过正交性与行列式检查。
- 使用独立的真实运动数据进行验证，不只在标定数据上评估。

## 9. 阶段六：加速度零偏、比例与 Y 轴重建

ICM20608 加速度先扣除零偏，再转换到 MPU/线圈坐标系：

$$
\mathbf a_{\mathrm{ICM}\rightarrow\mathrm{MPU}}
=
\mathbf R
\mathbf S_a
\left(
\mathbf a_{\mathrm{ICM}}-\mathbf b_{a,\mathrm{ICM}}
\right)
$$

其中：

- $\mathbf R$ 是陀螺仪估计的坐标旋转矩阵；
- $\mathbf S_a$ 是加速度轴向比例修正矩阵；
- $\mathbf b_{a,\mathrm{ICM}}$ 是 ICM20608 加速度零偏。

MPU6050 Y 轴饱和样本不参与加速度标定拟合。优先使用：

- 静止时的重力模长约束；
- MPU6050 未饱和的 X/Z 轴；
- 多姿态静止数据。

### 完成标准

- ICM20608 转换后的 X/Z 轴与 MPU6050 正常轴具有合理的相关性与数值尺度。
- 静止时融合加速度模长接近重力加速度。
- 转换后 Y 轴不再长期卡在 `19.613 m/s²`。

## 10. 阶段七：实现带回滞的融合状态机

### 输入有效性

ICM20608 帧只有在以下条件同时满足时才能补偿：

- SPI 读取成功；
- 样本年龄低于阈值；
- 标定文件存在且通过版本/校验检查；
- 数值没有超出 ICM20608 配置量程；
- 时间对齐误差低于阈值。

### 状态

```text
MPU_PRIMARY      MPU Y 轴可信，以 MPU 为主
BLENDED          两路都可信，加权融合
ICM_FALLBACK     MPU Y 轴饱和，使用 ICM 转换值
INVALID          两路都不可信，不更新轨迹积分
```

### 融合输出

$$
a_y^{\mathrm{fused}}
=
\begin{cases}
a_{y,\mathrm{MPU}}, & \text{MPU\_PRIMARY} \\
w a_{y,\mathrm{MPU}}+(1-w)a_{y,\mathrm{ICM}\rightarrow\mathrm{MPU}}, & \text{BLENDED} \\
a_{y,\mathrm{ICM}\rightarrow\mathrm{MPU}}, & \text{ICM\_FALLBACK}
\end{cases}
$$

进入和退出饱和状态使用不同阈值及连续样本数，避免在阈值附近频繁切换。

### 完成标准

- 状态切换无抖动。
- ICM 数据超时时不继续使用旧样本。
- `INVALID` 状态禁止轨迹积分，防止错误数据污染位置。

## 11. 阶段八：在现有 GUI 上增量改造

### 强制约束

- 继续使用 `gui/app.py` 和 `run_gui.sh`。
- 保留当前左侧连接配置、控制台和右侧四个可视化区域。
- 不创建另一套 GUI，不用新窗口代替现有主窗口。
- 保留 SSH ControlMaster 复用和远程进程清理机制。

### 11.1 扩展数据模型

修改 `Sample`：

- 保留现有 `accel`、`gyro`作为最终融合输出，尽量避免重写后续姿态与轨迹逻辑。
- 新增 `mpu_accel`、`mpu_gyro`、`icm_accel`、`icm_gyro`。
- 新增 `fusion_state`、`icm_age_ms`、`sync_error_ms`、`saturation_mask`。

### 11.2 扩展数据解析与远程工作线程

修改 `parse_sample()` 和 `StreamWorker`：

- 解析双传感器帧。
- 对协议版本进行检查，拒绝未知字段布局。
- 只启动一个远程双传感器监视程序。
- 停止/关闭时先停止采集，再关闭 SSH 主连接。

### 11.3 左侧控制与状态区

在现有“连接与配置”区域下增加：

- `开始双传感器标定` 按钮；
- 标定阶段和进度条；
- 时间延迟、旋转 RMSE、每轴相关系数；
- 融合状态：`MPU`、`融合`、`ICM 补偿`、`无效`；
- ICM 驱动、SPI、标定文件和样本新鲜度状态；
- `显示原始数据` 开关。

### 11.4 修改现有图表，不新增弹窗

- 图1：默认显示融合三轴加速度；开启原始模式时，叠加 MPU Y、ICM 转换 Y 和融合 Y。
- 图2：默认显示融合/校正后角速度；可选叠加两颗传感器对齐数据。
- 图3：只使用有效融合加速度更新轨迹；补偿无效时暂停积分。
- 图4：保留现有姿态显示，将加速度重力校正改为使用融合加速度。

### 11.5 安全与容错

- ICM 驱动未加载时，GUI 明确显示“ICM 不可用”，不伪造补偿值。
- 标定文件不存在时，允许采集原始数据，但禁止将 ICM 数据融入轨迹。
- 任何 Qt 定时器、按钮槽或数据解析异常均不得逃逸至 Qt 主循环。
- 远程采集程序退出时，GUI 需显示真实退出原因和状态码。

### 完成标准

- 现有 GUI 布局与核心交互保留。
- 四个图表均使用真实双传感器数据更新。
- GUI 可视化当前是 MPU 数据、加权融合还是 ICM 降级补偿。
- 开始、停止、清空、再次开始和关闭窗口均通过实机测试。

## 12. 阶段九：标定参数持久化

建议在项目中保存可审查的 JSON 格式，例如 `config/dual_imu_calibration.json`，包含：

- 协议版本和标定算法版本；
- 两颗传感器的芯片身份或设备标识；
- 标定时间；
- 时间偏移 $\hat{\tau}$；
- 旋转矩阵 $\mathbf R$；
- 陀螺仪零偏与加速度零偏；
- 加速度比例修正参数；
- RMSE、相关系数和条件数；
- 数据样本数和通过/失败标志。

只有质量指标通过后才原子替换旧标定文件。不覆盖最后一份已知可用标定。

## 13. 阶段十：真实硬件验收矩阵

| 测试 | 操作 | 必须观察的结果 |
|---|---|---|
| 静止 | 装置静止 60 s | 融合加速度模长合理，速度在 ZUPT 作用下回零 |
| 单轴旋转 | 分别绕三个物理方向旋转 | 转换后两路陀螺仪同向、幅值接近 |
| 多轴运动 | 缓慢组合旋转 | 时延与旋转矩阵质量不显著退化 |
| MPU Y 饱和 | 保持当前故障工况 | 状态进入 `ICM_FALLBACK`，融合 Y 不再卡在满量程 |
| ICM 超时 | 安全地停止 ICM 数据源 | GUI 报警，轨迹停止使用旧 ICM 样本 |
| 停止/再启 | GUI 连续停止和启动 10 次 | SSH、Dropbear、RPMsg 和 SPI 均不失效 |
| 重启 | 重启 MP157 后直接启动 GUI | Dropbear、M4、ICM 驱动和双传感器采集自动恢复 |
| 长时间 | 连续采集至少 30 min | 无崩溃、无进程残留、无持续丢帧、内存不持续增长 |

演示数据、手工构造样本和模拟运输不能代替上述验收。

## 14. 阶段十一：文档与 Git 提交节点

建议提交顺序：

```text
docs: add dual-IMU fusion implementation pipeline
feat: add ICM20608 Linux acquisition
feat: add synchronized dual-IMU stream
feat: add automatic temporal and frame calibration
feat: add guarded MPU6050 Y-axis fallback
feat: integrate dual-IMU status and calibration into GUI
test: verify dual-IMU GUI against real hardware
docs: document calibration and recovery workflow
```

每个提交前执行：

1. 语法和静态检查。
2. 对应阶段的实机测试。
3. `git diff --check`。
4. 检查没有把工具链、构建产物、临时数据和未脱敏日志加入 Git。

## 15. 预计修改的文件

| 文件 | 用途 |
|---|---|
| `tools/read_icm20608.py` | 读取并换算 ICM20608 原始数据 |
| `tools/monitor_dual_imu.py` | 同步 MPU6050 与 ICM20608，输出统一帧 |
| `tools/calibrate_dual_imu.py` | 时延、陀螺零偏、旋转矩阵与质量指标 |
| `tools/install_icm20608.sh` | 安装/启用 ICM20608 Linux 采集环境 |
| `tools/uninstall_icm20608.sh` | 回退 ICM20608 配置 |
| `config/dual_imu_calibration.json` | 已验证的标定参数 |
| `gui/app.py` | 在现有 GUI 内扩展双传感器数据模型、状态、标定和可视化 |
| `gui/README.md` | 记录双传感器用法和限制 |
| `README.md` | 记录安装、标定、启动、验收和故障恢复 |

## 16. 项目完成定义

以下条件全部满足才能宣布项目完成：

- ICM20608 驱动可随 Linux 启动并稳定输出。
- 双传感器帧具有可检查的时间对齐质量。
- 坐标旋转矩阵通过独立真实数据验证。
- MPU6050 Y 轴饱和时，融合 Y 轴可信且不再固定在满量程。
- GUI 能清晰显示数据来源、标定质量、融合状态和故障原因。
- GUI 开始、停止、清空、重新开始、关闭和 MP157 重启后重连全部通过。
- 真实硬件连续运行测试通过，不存在未处理的严重错误。
- Git 工作区干净，实施过程和回退方法已文档化。
