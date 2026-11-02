# c2_v1_sdk

独立、零运行时依赖的 Python 电机协议 SDK，当前版本 `0.1.0`，Python >= 3.11。
依据用户提供的 ADM／GD 固件和旧测试工具，提取共同的 CAN FD MIT 控制路径。
导入、创建总线和注册节点均不访问硬件。

日常只需三个操作：

| 操作 | 接口 | 行为 |
|---|---|---|
| 下发 MIT | `motor.send_mit(position=..., velocity=..., torque=..., kp=..., kd=...)` | 完整校验后发一帧，驱动器尝试自动使能 |
| 取交互数据 | `bus.recv(timeout=0.1)` | 取下一帧，返回原始帧及能解析的状态；超时返回 `None` |
| 显式失能 | `motor.disable()` | 发一帧 SDO `0x2002=2`，不等待或虚构失能确认 |

CANable 管理 USB 和通用 CAN；本库借用已经打开的连接，不负责选择设备、开关 CAN、周期调度或 CSV。
当前实现没有后台线程，没有接收时跳过帧的循环，也不自动重试控制帧。

## 安装与测试

在本 SDK 根目录执行：

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

CANable SDK 由调用脚本单独安装。本库也可使用其他实现了 `send(frame)`／`recv(timeout)` 的连接。
开发测试位于 `tests/`，不依赖适配器，不执行枚举、启动 CAN 或发包。
其中 26 项核心检查只需标准库，另 2 项在安装 CANable SDK 时验证真实编解码与模拟 USB 的组合。

## 与 CANable 组合

调用脚本先明确选择适配器并用 `canable.open(..., fd=True)` 打开 CAN FD。
下面的函数借用已打开连接；**仅定义函数不会执行控制**，节点号和全部控制量由调用方提供。

```python
import canable
from c2_v1_sdk import CanableTransport, MotorBus

def create_nodes(node_ids):
    # 前置条件：调用方已 canable.open(serial_number=..., fd=True, ...)。
    bus = MotorBus(CanableTransport(canable))
    return bus, {node: bus.motor(node) for node in node_ids}

def send_and_observe(bus, motor, *, position, velocity, torque, kp, kd):
    motor.send_mit(position=position, velocity=velocity, torque=torque, kp=kp, kd=kd)
    event = bus.recv(timeout=0.1)
    # event 可能属于同总线另一节点，或是心跳、SDO、CAN 错误等。
    # 脚本可先记录 event.frame，再检查 event.state / event.decode_error。
    return event

def request_disable(motor):
    motor.disable()
    # 继续用同一 bus.recv() 接收；event.control_reply 可显示对象写入应答。
    # ACK 没有事务号，不能严格关联一次写入，更不能证明功率级已关断。
```

CANable 对象接口也可直接注入：`MotorBus(open_canable_object)`，对象来自
`canable.transport.Canable`。模块接口使用 `CanableTransport(canable)` 适配参数形式。
本库没有 `close()`：调用方负责先收尾、再关闭 CANable；关闭 USB 不等于失能电机。

同总线多个节点使用同一个 `MotorBus`，重复 `bus.motor(id)` 返回同一节点。
所有节点注册、发送和接收在创建 `MotorBus` 的同一线程进行，不让各节点各自读取同一通道。
当前 `CanableTransport` 面向同步五函数接口；已有单 ID native 周期引擎可复用纯编解码，
本轮没有把它扩展成多节点调度器，也未验证实际控制频率。

## 返回值与完整性

`bus.recv()` 每次最多调用一次底层接收，并返回 `ReceivedFrame`：

- `frame`：包含原始载荷、帧标志及原样保留的 transport `timestamp_ns/sequence`。
- `state`：已注册节点的有效 TPDO5；包含 `node_id/position/velocity/torque`、
  `motor_temperature/driver_temperature`、原始 `error_code` 和 `raw_data`。
- `control_reply`：对象 `0x2002:00` 的 SDO 应答，`accepted` 表示 `0x60` 或 `0x80`。
  响应不回显可用的事务号／写入值；该对象的迟到或其他控制字写入应答无法区分。
- `decode_error`：命中本协议但格式非法时的说明；原始帧仍返回。
- `receive_index`：本 `MotorBus` 实例的逐帧交付序号；`received_at_ns` 是本次接收观察时间。

未注册节点、其他 SDO、心跳、EMCY、CAN 错误、扩展帧及远程帧均原样交付。
不丢弃中间帧来等待本节点，不把解析错误或非零错误码自动变成停止实验的决定。
`motor.last_state` 只是可能过期的便利缓存，不能代替逐帧数据流。
本库不后台预读／积攒帧；长时间不接收的 USB／设备缓冲能力需要单独验证。
这不等于公共记录器已经实现，也不承诺通信到达前或任意负载下的零丢帧。

## 线路协议和单位

标准 11 位 ID、小端。此固件的节点号范围 `0..127`，0 为普通节点，不能作广播。
MIT／反馈为 CAN FD，发送默认 BRS。CAN 波特率由调用方配置，旧路径为仲裁 1 Mbit/s、数据 4 Mbit/s；
实际设备配置尚未核实，本库不自动改写驱动器参数。

| 帧 | CAN ID | 载荷 |
|---|---|---|
| MIT，含自动使能尝试 | `0x400 + node` | 16 B，`<fffHH`：position、velocity、torque、kp×100、kd×100 |
| 状态 | `0x190 + node` | 16 B，`<fffhh`：position、velocity、torque、电机温度×10、驱动温度×10 |
| 带错误状态 | 同上 | 20 B：原 16 B + uint16 error；末 2 B 未定义，保留不解释 |
| 失能请求 | `0x600 + node` | `2B 02 20 00 02 00 00 00` |
| 控制字写入应答 | `0x580 + node` | 8 B，首字节 `60` 接受／`80` 拒绝，索引 `02 20`、子索引 `00` |

position 为固件反馈坐标的 rad，velocity 为 rad/s；torque 为固件的前馈力矩值，标称 N·m，**不是安培**。
SDK 不额外换算减速比、零点、方向或电流，实际机械坐标／力矩标定仍需核实。
`kp/kd` 使用旧 Python 接口的增益值，按 `round(value * 100)` 编码，分辨率 0.01；
例如 `kp=20` 的线路值为 2000。取整遵循 Python 最近值、半值取偶。
两套固件的内部增益运算不同，统一线路值不保证相同闭环响应。
有限 float32 和增益 `0..655.35` 仅为表示范围，物理限值保持 unknown，不默认限幅。

`0x400` 路径会尝试使能，**全零 MIT 也不是无副作用的状态查询**。
`0x100` 的旧入口、任意 SDO 读写、标定、清错、写 Flash、OTA、CiA 402 状态机均不在首版接口内。
SDO `0x80` 拒绝载荷按固件原文保留，不套用标准 CANopen abort code。

反馈是在固件处理命令时填入的状态快照；没有指令／反馈序号，也不保证本次控制已被控制环执行。
USB 写入完成、CAN ACK、TPDO 收到、功率级使能和物理运动是不同证据。
`0x190` 不是 0x80 对齐的基址，必须按完整 ID 加法匹配，不能用 `id & 0x7F` 反推节点。

## 错误码差异

用 `state.error_code` 取得原始位掩码，`state.errors` 使用公共 `ErrorFlag`，
`error_names(code, firmware=None)` 输出公共名称并显式保留未知位。
GD 独有的 `0x0100 MULTI_CHECK_ERROR`、`0x0800 ENC_SLIP` 只在明确指定 `firmware="gd"` 时命名。
公共位定义见 [errors.py](c2_v1_sdk/errors.py)，来源与逐位表见 [协议核对](docs/protocol_sources.md)。

当前 ADM 源码在 TPDO 中屏蔽 `HEARTBEAT_TIMEOUT=0x0080`；当前 GD 源码没有屏蔽。
SDK 不再次屏蔽任何错误位。ADM 的 `error_code=0` 不能证明完整错误寄存器为零。
GD 头文件部分旧错误名标注“失效”，ADM 的 `OVER_CURRENT_SOFT` 实际用于温升模型，
因此枚举名不构成各硬件保护均已启用的承诺。

## 来源、版本和验证边界

核对日期：2026-11-02（Asia/Shanghai）。来源路径、Git 基线、工作区状态和相关文件 SHA-256
见 [协议核对](docs/protocol_sources.md)与 [source_manifest.json](docs/source_manifest.json)。
参考工程保持原样，不依赖它们的安装或运行时服务。

首版只做纯协议、模拟传输和独立安装验证；没有连接适配器、启动 CAN、发送控制或测试实际电机。
没有承诺 1 kHz、硬实时或各板闭环参数等效。
实际检查内容见 [离线验证记录](docs/validation.md)。

SDK 使用独立本地 Git 历史接入 motor_test，远端仓库地址待提供，尚未发布。
