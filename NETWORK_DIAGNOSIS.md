# 网络失联排查记录（2026-09-19）

## 根因确认（2026-09-19，重启后只读测量）

开发板断电重启后（路由已修复），在 **不启动 M4** 的情况下做了两组只读测量，确认了根因。

### 1. STM32MP1 的 GPIO 时钟有 MP 和 MC 两套门控

- `RCC_MP_AHB4ENSETR`（偏移 `0xA28`）控制 Cortex-A7/Linux 侧的外设时钟；
- `RCC_MC_AHB4ENSETR`（偏移 `0xAA8`）控制 Cortex-M4 侧的外设时钟；
- 两者按位或后决定外设时钟是否有效，任意一侧使能，A7 和 M4 都能访问该外设。

实测：Linux 空闲时 `MP_AHB4ENSETR=0`、`MC_AHB4ENSETR=0`，此时用 `/dev/mem` 读 GPIO 寄存器全为 0。通过写 `MP_AHB4ENSETR` 打开对应 GPIO 组时钟后，A7 可以读到真实寄存器值；只写 `MC_AHB4ENSETR` 时 A7 仍读到 0（Linux 的 pinctrl/clk 框架管理的是 MP 位）。这也解释了第四次实验中 `before_*`/`after_stop` 全 0 的现象。

### 2. 以太网引脚的健康配置

用 `tools/gpio_bank_dump.py` 打开 GPIOA/C/E/G 的 MP 时钟后读取，所有以太网引脚均为复用模式：

```text
PA1 PA2 PA7 PC1 PC2 PC4 PC5 PE2 PG4 PG5 PG13 PG14
MODER=10 (复用)   AF=11 (ETH1)
```

即健康状态下以太网引脚的 `MODER` 必须是 `10`。

### 3. GPIOB 被清零的算术证据

把第四次实验 `after_start` 的 `GPIOB MODER=0xf50003c0` 与上述健康约定对照：

| 引脚 | 健康值 | after_start 实测 | 说明 |
|---|---|---|---|
| PB0/PB1/PB11（以太网 AF11） | 10 | 00 | 被清为输入 |
| PB2/PB5/PB6/PB7/PB8/PB9（UART/audio/cec/dcmi） | 10 | 00 | 被清为输入 |
| PB10（未使用） | 11 | 00 | 被清为输入 |
| PB3/PB4/PB14/PB15（sdmmc 模拟） | 11 | 11 | 被 Linux sdmmc 运行时 PM 重新应用 |
| PB12/PB13（M4 软件 I²C） | 10 | 01 | M4 设置为输出 |

`0xf50003c0` 恰好等于「全寄存器先被清零，再恢复 sdmmc 模拟脚和 PB12/PB13」。这说明 M4 固件在 `gpio_i2c_init()` 中写入 `RCC->MC_AHB4ENSETR` 后立即读取 `GPIOB->MODER` 做读-改-写；**GPIOB 时钟尚未生效，读回 0，于是把 MODER/OSPEEDR/PUPDR 中其他引脚全部写 0**，破坏了以太网引脚的 AF11 复用。Linux 只恢复了 sdmmc 的引脚，以太网配置不再被重放，因此网络不恢复，直到重启。

### 4. 为什么表现为“首次启动依赖”

`MC_AHB4ENSETR` 的 GPIOB 位在 M4 停止后仍保留（Linux 的 clk 框架只管理 MP 位）。因此：

- 整板重启后的第一次启动 M4：MC 位为 0，时钟刚使能即被访问，读回 0 → 清零 → 断网；
- 同一启动周期内再次启动 M4：MC 位已为 1，时钟早已就绪，读值正确 → 只改 PB12/PB13 → 网络正常。

这解释了第三次重启中同一固件多次通过、以及软件重启后首次启动再次失败的现象。

### 5. 修复方向

1. 固件在使能 GPIOB 时钟后，等待时钟就绪再进行读-改-写（例如先轮询 `GPIOB->MODER` 直到读到非零稳定值，或固定延时后再访问）；`hello.c` 对 GPIOF 的读-改-写有同样问题，应一并修复。
2. 更彻底：在设备树中禁用 UART5 并把 PB12/PB13 配置为 GPIO，使 M4 不再需要修改 Linux 已配置的 MODER。
3. 临时规避：启动 M4 前由 Linux 先打开 GPIOB 的 MP 时钟（本记录的只读测量已验证 MP 门控可独立控制）。

## 修复与实机验证（2026-09-19，已完成）

### 修复内容

在 `m4/src/mpu6050.c`、`m4/src/mpu6050_rpmsg.c`、`m4/src/hello.c` 三处加入 `gpio_read_stable()`：使能 GPIO 时钟后先读取目标寄存器，直到读到非零稳定值（上限 100000 次）再参与读-改-写，避免时钟未就绪时读回 0 清零其他引脚。修复后重新编译三个目标：

```text
m4_hello.elf / m4_mpu6050.elf / m4_rpmsg.elf    构建成功
m4/build-no-tx/m4_rpmsg.elf（M4_RPMSG_DISABLE_TX=ON）  构建成功
```

### 验证步骤与结果

开发板断电重启后（GPIOB 的 MP/MC 时钟位均为 0，即竞态条件成立），按以下顺序验证：

1. **修复版无发送固件作为本次启动的第一次 M4 启动**（与第四次实验的失败条件相同）：
   - 部署 `m4_mpu6050_no_tx_fix1.elf`，解绑 UART5 后启动；
   - `remoteproc0=state=running`，Mac 侧 `ping 15/15`，平均 0.749 ms；
   - 读取 GPIOB 寄存器确认配置未被破坏：

   ```text
   GPIOB MODER=0xf5baabea
   PB0  MODER=2(复用) AF=11(ETH1)
   PB1  MODER=2(复用) AF=11(ETH1)
   PB11 MODER=2(复用) AF=11(ETH1)
   PB12 MODER=1(输出) AF=14   PB13 MODER=1(输出) AF=14
   GPIOB OTYPER=0x00003040（PB12/PB13 开漏）
   ```

2. **修复版完整固件**：部署 `m4_rpmsg_fix1.elf` 启动后运行 250 帧验收：

   ```text
   packets=250 sequence=0..249
   mean_period_ms=20.000 rate_hz=50.000
   dropped=0 bad_checksums=0
   i2c_errors=0 tx_errors=0
   PASS
   ```

   测试期间与结束后 ping 均 5/5，网络保持正常。

3. **恢复现场**：停止 M4、重新绑定 UART5，`ping 5/5`；修复版完整固件同时覆盖为 `/lib/firmware/m4_rpmsg.elf`，供 GUI 与启动脚本使用。

原始记录：`diagnostic_logs/fifth_restart/`（串口日志、ping 日志）。

### 结论

- 以太网失联根因确认为 M4 固件的 GPIOB 时钟就绪竞态，修复后同一“首次启动”条件下不再复现；
- `accel_y=32767` 的满量程异常与本次修复无关，仍需单独排查；
- RPMsg 流控已于 2026-09-19 补齐，验证结果见下节；MPU6050 Y 轴异常仍是独立待办。

## RPMsg 流控修复与 GUI 验证（2026-09-19，已完成）

为避免 GUI 关闭后 M4 继续发送并耗尽 `rpmsg_tty` 缓冲，完成以下修改：

- M4 默认停止发送，收到精确的 `start` 后才开始 50 Hz 采样发送，收到 `stop` 后立即停发；
- 移除兼容层对控制命令的无条件回显；
- `monitor_rpmsg_imu.py`、`read_rpmsg_imu.py` 和姿态程序退出前发送 `stop`；
- 使用 `/tmp/rpmsg_imu_reader.lock` 排他锁，禁止两个进程同时访问 `/dev/ttyRPMSG0`；
- GUI 停止时先向 SSH 伪终端发送 `Ctrl+C`，等待远端程序正常清理，超时后才强制终止；
- GUI 同时接受 `m4_rpmsg.elf` 和内容相同的 `m4_rpmsg_fix1.elf`。

修复版固件实机连续执行两轮 100 帧验收，结果均为 50.000 Hz、零丢帧、零坏校验、零 I²C/TX 错误；第二轮能够在第一轮 `stop` 后重新 `start`。并发启动第二个读取器时立即返回退出码 2，不再竞争 RPMsg TTY。真实 SSH 数据模式 GUI 运行后自动关闭，远端监视进程无残留，随后读取器能够再次启动。最终 ping 10/10、丢包率 0%，内核日志无新增 `No memory`、Oops 或 `Internal error`。

## 第四次重启实验结果（2026-09-19，已完成）

**状态：** 已完成。开发板断电重启后处于首次启动条件（M4 未启动、未运行 GUI）。Mac 先启动串口记录（`tools/serial_logger.py`），基线 ping 5/5、SSH 正常、`remoteproc0=offline`。随后上传并后台运行 `tools/diagnose_first_start.sh`，脚本使用 `/lib/firmware/m4_mpu6050_no_tx.elf`，完整执行并输出 `DIAG_EXIT=0`。

### 结果

- 启动 M4 后 ping 15/15 丢包，网络再次失联；M4 停止后网络仍不恢复。
- 脚本结束把日志输出到串口后，串口终端也不再响应；Mac 的 `en7` 仍为 `100baseTX <full-duplex>`、`status: active`，说明 Mac 侧物理链路正常。最终需要断电重启才能恢复。
- 原始记录：`diagnostic_logs/fourth_restart/serial.log`、`ping_baseline.log`、`ping.log`。

### GPIOB 四阶段寄存器

| 阶段 | MODER | OTYPER | OSPEEDR | PUPDR | IDR | ODR | AFRL | AFRH |
|---|---|---|---|---|---|---|---|---|
| before_unbind | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| before_start | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| after_start | 0xf50003c0 | 0x00003040 | 0x50840140 | 0x50000140 | 0x00000006 | 0x00001000 | 0xd5a008bb | 0x00eeb05d |
| after_stop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

eth0 统计在四个阶段完全相同：RX 54050 字节/208 包，TX 28882 字节/193 包。

### 解读

1. `before_*` 和 `after_stop` 全 0 并不表示配置被清空，而是 **Linux 运行时把 GPIOB 时钟门控**，此时通过 `/dev/mem` 读寄存器得到 0；`after_start` 非 0 是因为 M4 固件使能了 GPIOB 时钟。这也意味着用这种方法无法直接读到 Linux 的健康基线。
2. `after_start` 的 AFRL/AFRH 与第三次重启的 pinmux 基线一致（PB0/PB1/PB11 = AF11，PB12/PB13 = AF14），但 **PB0/PB1/PB11 的 MODER 为 00（输入）**；若它们要作为 ETH1 的 AF11 工作，MODER 应为 10（复用）。这正好是以太网被破坏的可疑点。
3. 启动前后 eth0 收发包计数完全不变，说明 M4 启动后 MAC 既没有发出响应，也没有收到 Mac 的请求，问题位于 RGMII 引脚配置/数据路径，而不是 Linux 协议栈或防火墙。
4. 同一固件在第三次重启中多次通过，说明故障依赖首次启动状态；最可能的机制是 **M4 的 GPIOB 读-改-写竞态**：`gpio_i2c_init()` 先写 `RCC->MC_AHB4ENSETR` 使能时钟，紧接着读 `GPIOB->MODER` 做读-改-写；若时钟尚未就绪，读回 0，就会把 PB0/PB1/PB11 等引脚的 MODER/OSPEEDR/PUPDR 一并清零，破坏 Linux 已配置好的以太网复用；若时钟已就绪（例如 Linux 其他驱动刚访问过 GPIOB），读值有效，固件只改动 PB12/PB13，网络不受影响。第三次重启中此前多次解绑/读写 GPIOB 可能已使时钟保持开启，因此未复现。
5. 该假设仍需下一轮实验确认：断电重启后先强制使能 GPIOB 时钟读取健康基线，再启动同一固件复测 MODER 是否被清零。

### 附带发现：Mac 侧过期路由会伪装成“开发板失联”

本轮开始时曾出现 ping `Host is down`、SSH 超时，但开发板本身正常。原因是 Mac 路由表中存在一条克隆到 Wi-Fi 的主机路由：

```text
169.254.50.2  link#11  UHRLSW  en0   (REJECT)
```

修复方式（需要管理员密码）：

```bash
sudo route delete -host 169.254.50.2
```

修复前可用 `ping -b en7 169.254.50.2` 和 `ssh -b 169.254.144.222 mp157` 绕过。后续报告“失联”前，应先确认 Mac 侧路由与 ARP，避免把 Mac 路由问题当成板端故障。

### 下一步

1. 断电重启，恢复到首次启动条件。
2. 在板端先运行只读脚本：强制使能 GPIOB 时钟后读取 MODER/OSPEEDR/PUPDR/AFRL/AFRH，得到健康基线。
3. 启动 `m4_mpu6050_no_tx.elf`，复测寄存器；若 PB0/PB1/PB11 的 MODER 由 10 变 00，即可确认竞态根因。
4. 修复方向：固件在使能 GPIOB 时钟后等待/校验时钟就绪再读-改-写（例如轮询读到非零或固定延时）；更彻底的做法是在设备树中禁用 UART5 并把 PB12/PB13 固定为 GPIO，使 M4 不再需要修改共享的 MODER。

## 软件重启后的首次启动复现

在本轮所有分步测试通过后，通过 SSH sync/reboot。重启后首次直接启动同一个无发送固件，再次失联，ping 8/8 丢包。但串口明确显示开机 31.99 秒 M4 启动，44.54 秒 M4 成功停止，证明 Linux 仍能执行预先安排的命令，不能称为整板死机。M4 停止后网络仍未恢复。

串口记录：`diagnostic_logs/reboot_first_start_serial.log`。当前较强的证据指向首次启动时的状态依赖；GPIOB 时钟启用与共享寄存器读改写只是待验证假设，尚未确认。已准备 `tools/diagnose_first_start.sh`，下次恢复后在板端独立运行，记录启动前后 GPIOB 寄存器、网络状态及统计并输出至串口，即使 SSH 丢失也能得到比较结果。该脚本尚未执行。

## 第四次重启执行步骤

目的：直接比较重启后首次启动无发送固件前后的 GPIOB 寄存器与以太网状态，不先运行其他 M4 固件，以免改变首次启动条件。

**状态：** 已执行完成（2026-09-19）。由于 Mac 侧存在过期路由，实际基线检查使用 `ping -b en7` 与 `ssh -b 169.254.144.222`，串口录制改用可实时落盘的 `tools/serial_logger.py`。执行结果与四阶段寄存器数据见文首“第四次重启实验结果”。以下为原始步骤，保留备查。

1. 在 Mac 终端进入本项目目录，先开启串口录制，再重启开发板：

   ```bash
   cd ~/project/Joint_inversion_for_simulation/docs/简历和套磁信/嵌入式mp157
   mkdir -p diagnostic_logs/fourth_restart
   cd diagnostic_logs/fourth_restart
   screen -L /dev/cu.usbserial-1140 115200
   ```

   保持此终端开启，日志保存在该目录的 `screenlog.0`。同一串口只保留一个读取进程；如果已有 Codex 串口录制正在运行，使用其日志，不再同时打开 screen。不要启动 GUI、监视器或其他 M4 固件。

2. 在另一个 Mac 终端检查启动后的基线：

   ```bash
   ping -c 5 169.254.50.2
   ssh -o ConnectTimeout=5 mp157 'echo ssh_ok; cat /sys/class/remoteproc/remoteproc0/state'
   ```

   应返回 `ssh_ok` 和 `offline`。如果仍超时，先等待系统启动并复查；如果 M4 已为 `running`，此次不是所需的首次启动条件，应先查明自动启动来源。

3. 在项目目录上传并独立启动诊断脚本：

   ```bash
   scp -o ConnectTimeout=5 tools/diagnose_first_start.sh mp157:/tmp/
   ssh -o ConnectTimeout=5 mp157 'nohup sh /tmp/diagnose_first_start.sh </dev/null >/tmp/m4-first-start-launch.log 2>&1 &'
   ```

   脚本使用板上已有的 `/lib/firmware/m4_mpu6050_no_tx.elf`。它依次记录解绑前、解绑后启动前、启动三秒后、停止后的状态，并将 `/tmp/m4-first-start.log` 输出到串口。后台运行及标准输入重定向用于让诊断在 SSH 失联后继续执行；退出清理会尝试停止本次启动的 M4，并输出日志。该清理不能保证在内核或硬件异常时仍成功。

4. 记录网络表现并取回日志：

   ```bash
   ping -c 15 169.254.50.2 | tee diagnostic_logs/fourth_restart/ping.log
   scp -o ConnectTimeout=5 mp157:/tmp/m4-first-start.log diagnostic_logs/fourth_restart/
   ```

   SSH 失联时以串口日志为准。先保存日志，避免断电清除板上 `/tmp` 内容。日志末尾应有 `DIAG_EXIT=0`；非零表示诊断未完整完成，应先看失败位置。

5. 比较四阶段记录，按证据判断：

   - GPIOB 的 `MODER`、`OSPEEDR`、`PUPDR`：重点比较 PB0、PB1、PB11 对应的两位字段，即 `[1:0]`、`[3:2]`、`[23:22]`；这些是实机以太网引脚，不应由本次 PB12/PB13 操作改变。
   - `AFRL/AFRH`：检查上述以太网引脚的复用字段；结合 `OTYPER` 排查其他意外配置变化。`IDR` 是动态输入状态，变化本身不等于配置被改写。
   - `ip -br addr` 与 `ip -s link show eth0`：确认地址、链路及收发计数变化。寄存器一致时继续检查网络服务、ARP 和时钟，不能直接认定 GPIO 是根因。
   - 如果仅启动后出现以太网引脚配置变化，将具体寄存器的前后值写入本节，再设计针对性修复；如果未变化，记录阴性结果，继续缩小范围。

**已完成：** 板端执行、日志取回及寄存器前后对比；结果见文首“第四次重启实验结果”。

## 第三次重启后的分步测试

- 实机 pinmux 确认 PB12/PB13 为 UART5 AF14；以太网占用 PB0/PB1/PB11 等引脚，没有直接占用 PB12/PB13。完整记录：`diagnostic_logs/gpio_baseline_pinmux.txt`。
- GPIO-only（两秒后恢复这两个引脚原配置）：10/10 ping 成功。
- 加入 9 个 I²C 恢复脉冲和 STOP：10/10 ping 成功。
- 加入 WHO_AM_I 读取：10/10 ping 成功。
- 加入传感器配置及 100 次连续读取：10/10 ping 成功。随后带诊断状态的复测从 MCU SRAM 读得 `(stage=3, id=104, id_error=0, samples=100, read_errors=0)`，确认执行完成且恢复引脚，ID 为 0x68。
- 原主循环加 100 次上限、关闭周期发送：诊断状态确认完成 100 次读取，无 I²C 错误，SSH 正常。
- 原主循环加 1500 次上限、关闭周期发送：完成 1500 次读取，无 I²C 错误；约 30 秒采样，35/35 ping 成功，平均 0.859 ms。记录：`diagnostic_logs/main_30s_ping.log`。
- 重新启动板上与上一轮相同的 `m4_mpu6050_no_tx.elf`（49844 字节）：运行 12 秒并成功停止，15/15 ping 成功。记录：`diagnostic_logs/original_no_tx_retest_ping.log`。
- 因此先前失联并非该无发送固件的必然结果。不能据此前一次失败确定 GPIO/I²C 为根因；首次启动状态、时序或其他状态依赖因素仍需排查。周期发送缺乏流控仍是独立存在的问题。
- 本轮曾出现 `remote FW shutdown without ack`，但远程处理器可停止且网络正常，不能直接将该提示当作失联原因。

## 第二次重启后的对照结果

- 启动完成后基线 ping 5/5 成功。
- 单独解绑 `40011000.serial`，SSH 返回 `UART_UNBOUND` 和 `AFTER_UNBIND_OK`，随后 ping 5/5 成功。此次单独解绑未造成网络失联。
- 上传无周期发送固件为 `/lib/firmware/m4_mpu6050_no_tx.elf` 并启动。串口在开机 93.77 秒记录了该文件名，93.81 秒记录 `remote processor m4 is now up`，随后创建 ttyRPMSG0。
- 启动后 SSH 超时，ping 10/10 丢包；未打开 GUI 或 RPMsg TTY 读取程序。串口没有新的缓冲失败或 Oops 日志，发送串口停止命令也没有响应。
- 原始串口记录在 `diagnostic_logs/no_tx_serial.log`。无周期数据发送仍复现，说明周期采样包发送不是此次失联的必要条件；仍不能排除 OpenAMP 握手回显等公共路径。
- 已编译 `m4/build-gpio-only/m4_rpmsg.elf`（`M4_GPIO_CONFIG_ONLY=ON`）：只配置 PB12/PB13 并置高，然后处理 OpenAMP 消息，不产生 I²C 恢复脉冲、不访问传感器、不发送采样包。尚未部署。
- 下次重启后应先保存 Linux 当前 pinmux（特别是 PB12/PB13）及 eth0 配置，再测试 GPIO-only 固件。GPIO-only 若复现，重点检查实际引脚复用和共享 GPIO 寄存器；若稳定，再逐项加入 I²C 总线恢复和读写。

## 实测证据

- 首次失联时，Mac en7 为 active、100baseTX，全双工；Mac 为 169.254.144.222/16，目标 169.254.50.2 的路由指向 en7。ping 和 SSH 都超时。
- 串口捕获到约 50 Hz 的 `rpmsg_tty ... No memory for tty_prepare_flip_string`，约 6 秒内有 293 条；查询命令没有得到响应。这不等同于已证实整机内存耗尽。
- 用户断电重启后，连续 10 次 ping 零丢包，平均 0.928 ms，SSH 返回 ssh_ok。M4 为 offline，可用内存 743 MB，启动日志未出现 RPMsg 缓冲错误或 Oops。
- 随后通过 SSH 解除 40011000.serial 绑定并启动板上现有 m4_rpmsg.elf，计划 5 秒后停止。没有启动 GUI，也没有打开 ttyRPMSG0。SSH 在返回 M4 状态之前失联，后续 ping 全部超时，停止动作未获确认。
- 串口未捕获本次启动的新日志，也未响应串口停止命令。因此本次不能声称已经捕获 Oops，或确定 M4 成功启动的时刻；故障范围仍包含 UART 解绑、固件启动、GPIO/I²C 和 RPMsg 发送路径。

## 代码发现

`m4/src/mpu6050_rpmsg.c` 每 20 ms 无条件采样并发送，没有注册 start/stop 回调；`virt_uart_compat.c` 将收到的数据回显。读取脚本退出只关闭文件描述符。因此关闭读取程序不会停止 M4 发送。这是明确的流控缺口，但目前不能证明它是网络失联的唯一根因。

## 已准备的对照固件

新增默认关闭的 CMake 选项 `M4_RPMSG_DISABLE_TX`，禁用周期采样包发送，保留 GPIO、I²C、采样和 OpenAMP 消息处理；回显回调保持原状。对照测试期间不要打开 GUI 或写入 RPMsg TTY。

```bash
cmake -S m4 -B m4/build-no-tx -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi-toolchain.cmake \
  -DM4_OPENAMP_PROBE_ONLY=OFF -DM4_RPMSG_DISABLE_TX=ON
cmake --build m4/build-no-tx --target m4_rpmsg.elf
```

已编译成功，尚未部署。下一次需要用户断电重启恢复网络，然后先单独验证 UART 解绑，再将对照固件以 `m4_mpu6050_no_tx.elf` 的独立名称部署，并在启动前持续记录串口。若不发送数据仍失联，优先拆分 GPIO/I²C 初始化；若稳定，再检查 RPMsg 发送及接收端生命周期。
