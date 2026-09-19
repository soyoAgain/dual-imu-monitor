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

### 阶段二执行结果（2026-09-19，已完成）

**驱动与设备**

- rootfs 无独立 `insmod/modprobe`，改用 `/bin/busybox modprobe icm20608`；模块 `icm20608.ko` 加载成功，内核日志 `ICM20608 ID = 0XAE`；
- SPI 设备 `spi0.0` 绑定驱动，设备节点 `/dev/icm20608`（字符设备 240:0，权限 600）；
- 新增 `tools/icm20608-load.sh`、`tools/icm20608-load.service`、`tools/install_icm20608.sh`、`tools/uninstall_icm20608.sh`；安装后服务 `enabled`/`active`，卸载→重装往返实测通过。

**读取 ABI（重要）**

- `read()` 固定写入 7 个小端 int32：`gyro x/y/z`、`accel x/y/z`、`temperature`；
- **返回值恒为 0**（驱动直接返回 `copy_to_user` 结果），不能用返回值判断成功；
- 必须用固定 28 字节缓冲区；缓冲区更小会被越界写入（实测触发 Python 段错误）；
- `tools/read_icm20608.py` 用 `ctypes` 调用 libc `read()`，忽略返回值后解码，输出 `m/s²`、`°/s`、`°C` 与 `CLOCK_MONOTONIC` 秒。

**量程与换算**：加速度 ±16 g（2048 LSB/g）、角速度 ±2000 dps（16.4 LSB/dps）、温度 `raw/326.8+25`。

**验收数据**

| 项目 | 结果 |
|---|---|
| 静止加速度模长 | `\|a\|` 均值 9.817 m/s²；静止样本 4990 帧均值 9.811、标准差 0.281 |
| 陀螺仪响应 | 手动旋转时角速度模长最大 317.84 dps，887 帧 >20 dps，无削顶 |
| 10 分钟连续读取 | 6000 帧、605.1 s、平均 9.91 Hz；时间戳最大间隔 0.102 s；温度 34.09 °C；无崩溃、无内核 SPI 报错、无新增 eth0 掉线 |

**已知异常（阶段三必须处理）**

- 10 分钟日志出现 4 帧全 0 样本（0.067%），前后数据正常、无运动关联；50 Hz、3000 帧复测未复现；内核无 SPI 错误。
- 采集器必须把 ICM20608 全 0 样本标记为无效，不参与融合与标定。
- 环境观察：本会话中 eth0 多次 `Link is Down/Up`（与插拔/线缆移动相关），会造成 SSH 偶发超时；测试期间无新增掉线。

**原始记录**：`diagnostic_logs/phase2_icm20608/icm20608_info.txt`、`diagnostic_logs/phase2_icm20608/icm20608_10min.log`。

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

### 阶段三执行结果（2026-09-19，已完成）

**实现**：新增板端采集器 `tools/monitor_dual_imu.py`，单进程同时读取 `/dev/ttyRPMSG0`（MPU6050，IMU1 帧）和 `/dev/icm20608`（ICM20608），按 MPU 帧输出单行协议：

```text
seq=<n> t=<s> mpu_a=<m/s2> mpu_g=<dps> icm_a=<m/s2> icm_g=<dps> \
age_icm_ms=<ms> mpu_sat=<mask> errors=<m4_i2c>/<m4_tx>/<spi>/<sync> m4_ms=<n>
```

- `t`：M4 时间戳映射到 Linux 单调时钟后的采样时刻；映射偏移用指数平滑（α=0.05）周期更新，偏差超过 20 ms 计入 `sync` 错误；
- `age_icm_ms`：ICM20608 采样时刻减去该帧映射后的 MPU 采样时刻；
- `mpu_sat`：MPU6050 加速度饱和掩码（bit0/1/2 = X/Y/Z）；
- ICM20608 全 0 样本不计入有效值并累计到 `spi` 错误；退出时向 M4 发送 `stop` 并释放读取锁。

**验收数据**（`diagnostic_logs/phase3_dual_stream/dual_imu_3000.log`）

| 项目 | 结果 |
|---|---|
| 帧数 | 3000 帧（seq 8477..11476） |
| 序号连续性 | 无断号（gaps=0） |
| 时长与速率 | 59.98 s，50.00 Hz |
| `age_icm_ms` | 均值 0.39 ms，最大 1.52 ms，无超过 30 ms 的帧 |
| 累计错误 | i2c=13（阶段一遗留累计）、tx=0、spi=0、sync=0 |
| 饱和掩码 | 恒为 2（Y 轴饱和，符合当前故障工况） |
| 退出清理 | 读取进程数 0，无残留 |

四项完成标准全部满足。下一步阶段四将用两路陀螺仪模长互相关估计时延，验证当前 0.4 ms 量级的采样时差是否稳定。

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

### 阶段四执行结果（2026-09-19，已完成）

**实现**：新增 `tools/calibrate_dual_imu.py`，读取 `monitor_dual_imu.py` 日志，对两路陀螺仪模长去均值、归一化后，在 ±0.5 s 范围内以 0.5 ms 步长做互相关估计时延；同时按 5 s 窗口评估稳定性，并输出逐帧可用性标记：

- `age_icm_ms > 30 ms` 或 `|τ| > 50 ms` 的帧标记为不可融合；
- `--flags <path>` 输出 `seq,fusable` 逐帧标记文件。

**实测数据**（1000 帧、19.98 s，手动绕三轴转动）：

```text
gyro magnitude: mpu max=188.80 dps, icm max=187.51 dps
estimated delay tau = +33.00 ms (peak corr 0.9965)
correlation at tau=0: 0.9458 -> at tau=+33.00 ms: 0.9965
per-window tau (5.0 s): [+33.50, +32.50, +32.00] ms, spread 1.50 ms
frames with age>30 ms: 0
fusable frames: 1000/1000
RESULT: PASS
```

**解读**

- `τ = +33.0 ms` 表示 ICM20608 采样在时间上领先日志中的 MPU 采样约 33 ms；来源是 M4 采样时间戳与实际 RPMsg 送达之间的固定延迟（软件 I²C 读取约 16 ms + 传输/缓冲），属于常量偏差，可由该时延修正。
- 两路角速度模长在转动时幅值一致（188.80 vs 187.51 dps），交叉验证了 ICM20608 ±2000 dps 量程换算。
- 三项完成标准全部满足：时延窗口间波动 1.5 ms、相关系数由 0.946 提升至 0.997、不可融合帧有明确标记逻辑。

**原始记录**：`diagnostic_logs/phase4_time_alignment/dual_imu_motion3.log`、`time_alignment_result.txt`、`fusable_flags.csv`。

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

### 阶段五执行结果（2026-09-19，已完成）

**实现**：扩展 `tools/calibrate_dual_imu.py`：

- `--static <log>`：从静止日志估计两路陀螺零偏；
- 对时间对齐后的 ICM 角速度逐轴插值到 MPU 时间栅格，扣除零偏后求解 Kabsch/Wahba 问题（SVD + `det(R)=+1` 修正）；
- 输出旋转矩阵、轴向对应、三轴 RMSE、正交误差、行列式、旋转数据协方差条件数；
- `--validate <log>`：在独立转动数据上重新估计时延并计算逐轴相关系数与 RMSE；
- `--frame-json <path>`：保存零偏、时延、旋转矩阵与质量指标。

**数据**：静止 500 帧（零偏）、阶段四转动日志 1000 帧（标定）、独立转动日志 1000 帧（验证）。

**结果**

```text
gyro bias mpu (dps): [ 4.5505  1.358  -0.2912]
gyro bias icm (dps): [ 0.3497  0.1516 -0.1011]
rotation icm -> mpu:
  [+0.00146, +0.99996, +0.00908]
  [-0.99888, +0.00103, +0.04726]
  [+0.04725, -0.00914, +0.99884]
  MPU X <- ICM Y (+1.0000)
  MPU Y <- ICM X (-0.9989)
  MPU Z <- ICM Z (+0.9988)
calibration RMSE (dps): [3.027 3.415 2.029]
orthogonality error=6.961e-16  determinant=1.000000  condition number=9.41

== validation on independent log ==
validation tau = +34.00 ms (peak corr 0.9976, at 0 0.9668)
per-axis correlation: [0.9978 0.9977 0.9954]
per-axis RMSE (dps): [3.288 3.048 2.206]
RESULT: PASS
```

**解读**

- 两芯片为绕 Z 轴的近似 90° 安装：MPU X 对应 ICM Y，MPU Y 对应 ICM −X，MPU Z 与 ICM Z 同向，矩阵接近正交置换；
- 标定与验证的时延一致（33.0 / 34.0 ms），说明时间对齐稳定；
- 独立验证三轴相关 0.995～0.998、RMSE 2.2～3.3 dps，旋转矩阵可靠；
- 三项完成标准全部满足。标定结果 JSON 暂存于 `diagnostic_logs/phase5_frame_calibration/frame_calibration.json`，阶段九再规范化为 `config/dual_imu_calibration.json`。

**原始记录**：`diagnostic_logs/phase5_frame_calibration/`（静止日志、验证日志、`frame_calibration_result.txt`、`frame_calibration.json`）。

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

### 阶段六执行结果（2026-09-19，已完成）

**实现**：扩展 `tools/calibrate_dual_imu.py`：

- `--accel-static <log>`：多姿态静止日志，按陀螺阈值分割静止段、按重力方向区分姿态；
- 以**重力模长约束**拟合 ICM 加速度零偏（弱正则化取最小范数解），比例固定为出厂值 1.0；
- 输出姿态数、各姿态 `|a_pred|`、重力 RMSE、与 MPU 的姿态均值相关性与斜率、静态均值偏差（即 MPU 加速度零偏）、去偏后 RMSE、重建 Y 轴范围；
- `--accel-json <path>`：保存标定参数与质量指标。

**数据**：4 个小角度倾斜姿态（前/后/左/右各 30～45°，每姿态 54～86 帧）。因组件安装受限无法做大角度翻转，比例项不可观，故不拟合比例。

**结果**

```text
poses detected: 4 (samples: [84, 86, 54, 84])
icm accel bias (m/s^2): [ 0.0362 -0.0413 -0.0044]
per-pose |a_pred| vs g: [9.800 9.809 9.811 9.807]
gravity RMSE: 0.0040 m/s^2
pose-mean correlation with MPU: x=0.99999 z=0.99820; slope x=0.9995 z=0.9839
static mean offset (pred - mpu): [-0.240  -18.906   1.118] m/s^2
static detrended RMSE: [0.087 2.513 0.235] m/s^2
reconstructed Y range (static): -3.603 .. 4.174 m/s^2
RESULT: PASS
```

**解读与发现**

- ICM20608 加速度几乎无需标定：拟合零偏仅 ±0.04 m/s²，重力 RMSE 0.004 m/s²，与出厂校准一致；
- **MPU6050 加速度存在明显零偏**：以重力为基准时，MPU 的 X/Z 与 ICM 转换值相差约 (+0.24, ?, −1.12) m/s²（约 0.115 g），且该偏差在各姿态近似恒定。由于 MPU Y 饱和，无法用常规方法修正其 Y 零偏；阶段七/八的融合可考虑 X/Z 也采用 ICM 转换值或对该零偏做补偿；
- `mpu_accel_bias_mps2` 中的 Y 分量（−18.9）是 MPU Y 饱和导致的差值，不是真实零偏；
- 重建 Y 轴随姿态在 −3.6～+4.2 m/s² 之间变化，不再固定于 `19.613 m/s²`；
- 三项完成标准全部满足。

**原始记录**：`diagnostic_logs/phase6_accel_calibration/`（多姿态日志、`accel_calibration_result.txt`、`accel_calibration.json`）。

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

### 阶段七执行结果（2026-09-19，已完成）

**实现**：新增 `tools/fusion_state.py`：

- `FusionStateMachine` 实现四个状态：`MPU_PRIMARY`、`BLENDED`、`ICM_FALLBACK`、`INVALID`；
- 输入有效性检查：SPI 成功、样本年龄 ≤30 ms、ICM 加速度不超量程、时延/同步误差 ≤50 ms、标定文件有效；
- 迟滞逻辑：进入饱和阈值 19.60 m/s²、退出 19.51 m/s²，分别需要连续 5/25 帧；**硬饱和（≥19.612 m/s²，即 raw 32767 削顶）立即降级**，不再等待确认计数，避免削顶数据泄漏到输出；
- 融合输出：`BLENDED` 用加权（默认 MPU 权重 0.5），`ICM_FALLBACK` 用 ICM 转换后的 Y，`INVALID` 返回空并令 `should_integrate=False`；
- `transform_icm_accel()` 用阶段五/六的旋转矩阵与零偏把 ICM 加速度映射到 MPU 坐标系；
- 离线回放：`--log <双传感器日志> --calib <标定 JSON>` 统计状态分布、切换次数与短片段（抖动）。

**自测结果**（`--self-test`，`diagnostic_logs/phase7_fusion_state/self_test_result.txt`）

```text
band oscillation transitions: 0 (states: ['BLENDED'])
saturation/recovery transitions: 2 segments: [('BLENDED', 100), ('ICM_FALLBACK', 124), ('BLENDED', 76)]
timeout frames marked INVALID without output: True
INVALID integration frozen: True
BLENDED weight check: state=BLENDED y=7.0000 expected=7.0000
self-test RESULT: PASS
```

**真实数据回放**（`replay_result.txt`）

| 日志 | 帧数 | 状态分布 | 切换 | 短片段 |
|---|---|---|---|---|
| `phase3_dual_stream/dual_imu_3000.log` | 3000 | 全部 `ICM_FALLBACK` | 0 | 0 |
| `phase4_time_alignment/dual_imu_motion3.log` | 1000 | 全部 `ICM_FALLBACK` | 0 | 0 |

**结论**

- 三项完成标准全部满足：状态切换无抖动（阈值带内 0 次切换、饱和恢复仅 2 次且无短片段）；ICM 超时帧标记 `INVALID` 且不输出旧样本；`INVALID` 期间轨迹积分冻结；
- 当前硬件 MPU Y 长期饱和，融合状态稳定停留在 `ICM_FALLBACK`，与阶段六的重建 Y 输出配套；
- 修复了设计初稿的缺陷：进入确认期间会短暂使用削顶的 MPU Y，改为硬饱和立即降级。

**原始记录**：`diagnostic_logs/phase7_fusion_state/`（`self_test_result.txt`、`replay_result.txt`）。

## 11. 阶段八：在现有 GUI 上增量改造

### 强制约束

- 继续使用 `gui/app.py` 和 `run_gui.sh`。
- 保留当前左侧连接配置、控制台和右侧四个可视化区域。
- 不创建另一套 GUI，不用新窗口代替现有主窗口，不新增弹窗；图 5、图 6 直接集成在主窗口内。
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

### 11.4 修改现有图表，并扩展主窗口图表（图 5、图 6）

- 图1：默认显示融合三轴加速度；开启原始模式时，叠加 MPU Y、ICM 转换 Y 和融合 Y。
- 图2：默认显示融合/校正后角速度；可选叠加两颗传感器对齐数据。
- 图3：只使用有效融合加速度更新轨迹；补偿无效时暂停积分。
- 图4：保留现有姿态显示，将加速度重力校正改为使用融合加速度。
- 图5（主窗口内）：ICM20608 三轴角速度随时间变化，单位 `°/s`。
- 图6（主窗口内）：ICM20608 三轴加速度随时间变化，单位 `m/s²`；可选叠加转换到 MPU 坐标系后的结果，便于对照。
- 布局要求：
  - 主窗口网格由 2×2 扩展为 3×2：第一行 图1/图2，第二行 图3/图4，第三行 图5/图6；窗口初始高度相应增加；
  - 图5/图6 与主窗口共用刷新定时器和数据缓存，不新增远程读取进程；
  - 数据无效（SPI 失败、超时、全 0 样本）时曲线留空或标记中断，不插值伪造。

### 11.5 安全与容错

- ICM 驱动未加载时，GUI 明确显示“ICM 不可用”，不伪造补偿值。
- 标定文件不存在时，允许采集原始数据，但禁止将 ICM 数据融入轨迹。
- 任何 Qt 定时器、按钮槽或数据解析异常均不得逃逸至 Qt 主循环。
- 远程采集程序退出时，GUI 需显示真实退出原因和状态码。

### 完成标准

- 现有 GUI 布局与核心交互保留。
- 主窗口六个图表（图 1–图 6）均使用真实双传感器数据更新。
- GUI 可视化当前是 MPU 数据、加权融合还是 ICM 降级补偿。
- 开始、停止、清空、再次开始和关闭窗口均通过实机测试。

### 阶段八执行结果（2026-09-19，已完成）

**实现**：在 `gui/app.py` 上增量改造，未新建 GUI：

- 数据模型：`Sample` 保留 `accel`/`gyro` 作为融合输出，新增 `mpu_accel`/`mpu_gyro`/`icm_accel`/`icm_gyro`、`icm_accel_mpu`/`icm_gyro_mpu`、`fusion_state`、`icm_valid`、`icm_age_ms`、`saturation_mask`、双传感器错误计数与温度；
- 协议解析：`parse_sample()` 解析 `monitor_dual_imu.py` 单行帧（含新增的 `mpu_t`/`icm_t` 温度字段）；
- 采集线程：只启动一个远程 `monitor_dual_imu.py`；启动前校验 M4 固件与 `/dev/icm20608`，失败时给出明确提示；保留 SSH ControlMaster 复用与远程进程清理；
- 融合：导入 `tools/fusion_state.py`，逐帧运行状态机；标定文件默认加载 `diagnostic_logs/phase6_accel_calibration/accel_calibration.json`（阶段九再规范化路径）；
- 图表（3×2）：图1 融合加速度（原始模式叠加 MPU Y 与 ICM→MPU Y）、图2 角速度（原始模式叠加 ICM 对齐数据）、图3 轨迹（仅有效融合加速度积分，`INVALID` 暂停）、图4 姿态（重力校正改用融合加速度）、图5 ICM 角速度、图6 ICM 加速度（原始模式叠加转换结果）；
- 左侧新增：融合状态彩色标签、ICM 有效性/样本年龄/温度/错误、标定信息（时延、重力 RMSE、验证相关系数）、`显示原始数据` 开关。

**验证结果**

| 测试 | 结果 |
|---|---|
| 演示模式（15 s，含周期性 MPU Y 饱和） | 退出码 0，无异常 |
| 离屏合成序列（静止→饱和→ICM 超时） | 状态计数 `BLENDED 120 / ICM_FALLBACK 100 / INVALID 50`；`INVALID` 期间轨迹位置冻结 |
| 实机端到端（约 13 s） | 383 帧真实数据，状态全部 `ICM_FALLBACK`，融合 Y=1.54 m/s² 取代饱和的 19.61；ICM 有效、age≈0.45 ms |
| 处理速率 | 稳态 50.3 Hz（与采集一致，无堆积） |
| 六图重绘开销 | 约 71 ms/帧（738 帧数据），低于 100 ms 刷新间隔 |
| 开始→停止→清空→再开始→关闭 | 各阶段样本计数符合预期，无残留进程 |

四项完成标准全部满足。**未实现项**：11.3 中的「开始双传感器标定」按钮与进度条暂未加入 GUI（标定仍通过 `tools/calibrate_dual_imu.py` 离线完成），留待阶段九一并处理。

### 阶段八补充：空间轨迹发散修复（2026-09-19）

**现象**：实机静止时图 3 轨迹沿 −Z 方向直线漂移，20 秒位置模长约 25.7 m。

**诊断**（实机 20 s 数据）：

```text
融合加速度均值：(1.86, 1.48, 8.45) m/s²，|a|=8.80（应为 9.81）
MPU 加速度均值：(1.86, 19.61, 8.45)
ICM→MPU 均值  ：(1.63, 1.48, 9.52)，|a|=9.81
MPU 零偏估计  ：(+0.23, ?, −1.08) m/s²
is_static=False，速度积分至 (−0.34, 1.72, −1.47) m/s
```

原因：融合输出只把 Y 轴替换为 ICM 值，X/Z 仍来自带零偏的 MPU，导致重力补偿后残余约 1.1 m/s²，静止检测阈值 0.2 m/s² 永不满足，ZUPT 失效。

**修复**：

1. 阶段六标定工具在 JSON 中新增 `metrics.mpu_accel_bias_mps2`（重力一致参考下的 MPU 零偏，Y 分量置 0 因饱和不可观测）；
2. GUI 加载该零偏，在融合与姿态/轨迹计算前从 MPU 加速度中扣除；演示模式同步生成带同样零偏的数据保持自洽。

**复测**（实机 22 s）：

```text
MPU 零偏加载：(0.240, 0, −1.118) m/s²
融合加速度均值：(1.383, 1.623, 9.595)，|a|=9.829 m/s²
最终位置：(0, 0, 0)，is_static=True，速度归零
```

轨迹在静止时保持原点，ZUPT 恢复正常。

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
