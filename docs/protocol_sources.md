# 协议源码核对

核对日期：2026-11-02（Asia/Shanghai）。核对实际工作区文件，未改动三个参考工程。
Git HEAD 不能代表带修改的文件；精确快照由 [source_manifest.json](source_manifest.json) 的 SHA-256 标识。
这些是源码／测试代码证据，尚无本 SDK 的设备通信或物理行为证据。

## 当前采用的 MIT 与使能路径

| 事实 | ADM：`mc/2.Firmware/ADM32F036_mc_app/user/` | GD：`c1_driver/Firmware_app/Source/` | 旧主机参考 |
|---|---|---|---|
| MIT ID `0x100+node`，反馈 `0x190+node` | `canfd.h` 的 RPDO5／TPDO5 | `com_can.h` 的 RPDO5／TPDO5 | 旧工具只选 0x400，本版按用户确认改 ID |
| 三个 float32 + 两个 uint16，16 B | `canfd.c::parse_frame` RPDO5 | `com_can.c::parse_frame` RPDO5 | 旧 `MitCommand.pack`／黄金字节的载荷仍适用 |
| `0x100` 接收分支不请求使能 | 只更新目标和超时状态 | 只更新目标和通信状态 | 固定 ID，不增加 auto_enable 参数 |
| enable 先写 `0x2003=3`，再写 `0x2002=1` | 0x2003 不在对象表，预计拒绝；0x2002 定义且可写 | 0x2003 定义且为 1 B；0x2002 为 2 B，回调请求进入 operation | 按 GD 的准备模式／使能步骤封装 |
| 每次处理 MIT 后填入当前反馈 | `canfd.c::parse_frame` | `com_can.c::parse_frame` | `parse_state`、扫频脚本逐包反馈 |
| 失能写 `0x2002=2` | `od.c` 条目，`motor_ctrl.c::MC_controlword_update` | `od.c` 条目，同名回调 | `control_frame(node, False)` |
| 固件另有 `0x400` 入口 | RPDO6 会请求使能 | RPDO3 会尝试写 mode=3、control_word=1 | 0.1.0 曾使用，0.2.0 按用户选择不采用 |

不按固件的 RPDO 序号命名公共接口。用户确认使用 0x100，并把使能／失能独立出来；随后指定直接使用 GD 逻辑。
没有照搬旧平台的 Bench、调度、GUI、记录器或其他设备依赖。

GD `od.c::dictionary_init` 将 operation_mode 初始化为 0，`MC_set_state(MCS_OPERATION)` 在模式为 0 时拒绝进入。
因此不能只发送控制字 1 而省略模式准备。模式采用其 0x400 分支使用的值 3，写入为 CS=0x2F 的 1 字节 SDO。
enable() 顺序提交两帧，不在中间收取 ACK；两个应答均从共享 recv 路径交付，ControlReply.index 区分 0x2003／0x2002。
ADM 的对象表没有 0x2003，模式请求预计会返回 CS_ERR，后续 0x2002=1 仍会提交。
该拒绝不会被隐藏成成功；用户提出的 ADM 可适配仍需实机核实，当前不创建自动识别或伪兼容结果。

## 单位与已发现的差异

- position／velocity：两份协议均为 rad／rad/s。GD 使用 `MotorControl.raw_pos/raw_vel`，
  其上游在 `encoder.c` 应用 `g_gear_ratio`；ADM 使用 `encoder.degree_q14/velocity_q14`。
  SDK 不再应用减速比或 polarity，实际机构配置与零点需另核实。
- torque：为前馈力矩线路值，标称 N·m。GD 中字段虽名为 `current_mit`，MIT 运算明确作为
  前馈力矩与 Kp/Kd 项相加，再调用 `TORQUE_TO_IQ`；ADM 经 `torque_ref_q14` 与 `Torque_To_Iq`。
  不能把 SDK 的 torque 当作安培，也不在 SDK 添加未经确认的电流换算。
- 反馈 torque：GD 使用 `ACTUAL_TORQUE`，由滤波 Iq 转力矩并按 polarity 处理；
  ADM 使用 `Iq_To_Torque(imt_current_to_float(...))`。没有虚构传感器实测精度。
- 增益线路值：旧主机明确用 `round(gain*100)`。GD 接收后 `/100.0f`；ADM 先 `*16400`，
  控制环再乘 `MIT_TERM_SCALE = MOTOR_RATED_CUR*1.41421356/40960`。
  保留旧主机线路尺度，**不保证两板等效的物理增益**。
- 温度：两个 int16，均除以 10 得 °C；支持负值。
- error：ADM 发 `error_code & ~ERR_HEARTBEAT_TIMEOUT`，当前 GD 发完整 `ERROR_CODE`。
  旧 `mc_tools/docs/motor_protocol.md` 写“两者均屏蔽心跳”已与当前 GD 源码不符，本 SDK 以当前源码为准。
- 带错误状态长 20 B，只定义字节 16..17 的 uint16；18..19 未赋明确值。SDK 保留原始载荷、不解释尾部。
- SDO 响应是在请求帧上改 CS 和 ID；可能保留请求数据，未定义标准 abort-code 结构。
  对象 `0x2002` 应答没有可靠事务号或写入值确认，不赋予 `disabled=True` 等推断。

协议结构相容不等于控制参数等效。精确电机型号、当前硬件固件、机械限值、力矩标定、
允许控制频率／抖动以及失能物理效果仍为 unknown。

## 错误位定义

来源分别为两份 `motor_ctrl.h::tErrorCode`。名称遵循固件符号去除 `ERR_` 前缀，
位可以组合，不能按连续编号解释。

| 位 | 公共名称 | 源码备注／边界 |
|---|---|---|
| `0x0001` | OVER_VOLTAGE | GD 头文件标注失效 |
| `0x0002` | UNDER_VOLTAGE | GD 标注失效；ADM 当前欠压处理代码被注释 |
| `0x0004` | FOLLOWING_ERROR | GD 标注失效 |
| `0x0008` | OVER_TEMP_DRV | 驱动器过温 |
| `0x0010` | OVER_TEMP_MOTOR | 电机过温 |
| `0x0020` | OVER_CURRENT_SOFT | GD 标注失效；ADM 当前用于热模型温升保护 |
| `0x0040` | OVER_LOAD | GD 头文件标注失效，但当前实现有负载／堵转相关处理，不能仅据注释断言无保护 |
| `0x0080` | HEARTBEAT_TIMEOUT | ADM TPDO 屏蔽；GD TPDO 当前保留；各自还有不同断联处理 |
| `0x0100` | MULTI_CHECK_ERROR | 只在当前 GD 头文件定义，并标注失效 |
| `0x0200` | NO_SN | SN／授权校验相关 |
| `0x0400` | ENC_MISSING | 编码器缺失／异常 |
| `0x0800` | ENC_SLIP | 只在当前 GD 头文件定义 |
| `0x4000` | ENC_CALIB | 编码器标定相关 |
| `0x8000` | ADC_SELFTEST | ADC 自检 |

`error_names(code)` 只命名公共位。明确指定 `firmware="gd"` 才命名 GD 两个附加位；
其他位以 `UNKNOWN_0x....` 返回。不存在以未知位、被固件隐藏的位或缓存状态判定电机正常的逻辑。

## 收敛时舍弃的旧行为

旧 `MotorBus._dispatch` 会跳过非目标帧，旧逐帧记录队列使用 `deque(maxlen=capacity)` 并计数被覆盖样本。
这与 motor_test 已确认的正常接收数据保留原则不符，因此没有复制到本 SDK。
本 SDK 的唯一接收入口逐帧交付原始数据及可用的解析结果，不预读、跳过或静默覆盖。
真实采集和 CSV 的持续负载能力要在公共记录器与具体脚本中继续设计和验证。

旧 `enable()` 额外发全零 0x400 MIT；本 SDK 的 enable 改为 GD 的模式／控制字 SDO，完全不发送 MIT 或覆盖五参数。
退出、超时和错误不会隐式补发控制／失能；`disable()` 需调用方明确选择，且本身不提供物理关断确认。
