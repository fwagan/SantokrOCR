# Modbus RTU 温度读取器模拟器

在**无真实硬件**（USB Modbus 温度读取器：CH340 + MAX31865 + PT100）时，模拟从站响应
SantokrOCR 的温度读取请求，用于测试 app 的 Modbus 链路。协议参数与真实设备完全一致：

| 项目 | 值 |
|------|-----|
| 协议 | Modbus RTU，9600 baud，8N1 |
| 从站地址 | 1 |
| 功能码 | 04（Read Input Registers） |
| 寄存器 | 地址 0 |
| 数据格式 | signed int16 × 10（读取后 ÷10 得温度℃） |
| 探头断开标志 | 0x7FFF (32767) |

温度默认模拟"烘焙升温曲线"：从室温（~25℃）按设定速率线性升温（℃/min），可选高斯噪声、
可选探头断开（返回 0x7FFF）。

```
tools/modbus_mock_server/
├── mock_modbus_server.py     # 模拟器主程序
├── requirements_test.txt     # 测试依赖（独立于主 requirements.txt）
└── README.md                 # 本文档
```

---

## 一、安装依赖

环境已装 pymodbus 则跳过（检查）：

```bat
python -c "import pymodbus; print(pymodbus.__version__)"
```

未安装时执行（已测试于 pymodbus 3.13.1）：

```bat
pip install -r tools/modbus_mock_server/requirements_test.txt
```

> 依赖只声明在 `requirements_test.txt`，**不会写入主 `requirements.txt`**。

---

## 二、安装 com0com 并创建虚拟串口对

com0com 提供 Windows 虚拟串口对（null modem）。下载：[SourceForge com0com](https://sourceforge.net/projects/com0com/)

> **x64 Windows 提示**：com0com 3.0.0.0 起驱动已签名，可直接安装；老版本（2.x）在 x64 上
> 需要关闭"驱动强制签名"后才能加载，如遇设备不出现请优先用 3.x。

### 方式 A：命令行（setupc.exe，需管理员权限）

安装驱动并创建 COM10 ↔ COM11 配对：

```bat
"<com0com安装目录>\setupc.exe" install PortName=COM10 PortName=COM11
"<com0com安装目录>\setupc.exe" list
```

> 各版本命令名有差异（有的用 `install` 建对、没有 `create` 命令），拿不准时先执行 `setupc.exe /?` 查看帮助。

### 方式 B：GUI

1. 运行 com0com 图形界面；
2. 添加一对串口（Add pair），把两个端口名改为 `COM10` 和 `COM11`；
3. Apply 应用。

> **Win11 24H2+（驱动签名）提示**：com0com 3.0.0.0 的驱动证书在较新 Win11 上可能被拒载
> （设备管理器 Code 52 "无法验证驱动数字签名"），且该问题无免重启的软件解法。若遇此情况，
> 直接改用下方的 VSPE 方案，不必折腾 com0com。

### 方式 C：用 VSPE 替代（Win11 24H2+ 推荐）

[VSPE](https://eterlogic.com/Products.VSPE.html)（Eterlogic，免费版限 1 对，够用）适配新版 Win11，
是 com0com 签名被拒时的替代方案：

1. 安装并运行 VSPE（**普通权限**运行即可，勿用管理员，否则普通权限的 app/python 打不开虚拟口）；
2. 新建一个 **Pair** 连接器（不是 Splitter/Connector/Hub），把它启动；
3. 在 Pair 属性里把两个端口名设为高位号（如 `COM98`/`COM99`），避免与真实串口设备撞号；
4. 用下方命令确认两个口可见。

> 若 VSPE 建好后端口报 `PermissionError(13)` 打不开，先**完全重启 VSPE**（常发生在某程序
> 打开虚拟口失败后，VSPE 内部端口状态卡住）。

### 验证配对已生效

```bat
python -c "import serial.tools.list_ports as p; print([x.device for x in p.comports()])"
```

应看到 `COM10`、`COM11`。本模拟器绑定**其中一端**（如 COM10），app 配置**另一端**（COM11）——
配对对称，哪端给谁都行，但**两端不能被两个程序同时占用**。

---

## 三、运行模拟器

**一键启动**（terminal 里运行，当前 conda 环境即可；确保 VSPE 在运行、Pair 已 Start）：

```bat
python tools/modbus_mock_server/start_mock_modbus.py                  :: 默认 COM98
python tools/modbus_mock_server/start_mock_modbus.py COM90            :: 指定端口
python tools/modbus_mock_server/start_mock_modbus.py COM90 --rate 20  :: 端口 + 透传其它参数
```

手动运行：

```bat
python tools/modbus_mock_server/mock_modbus_server.py --port COM10
```

启动后控制台每秒输出一次当前模拟温度；按 `Ctrl+C` 优雅退出。

### 参数一览

| 参数 | 默认 | 说明 |
|------|------|------|
| `--port` | 必填 | 模拟器绑定的串口（配对一端，如 `COM10`） |
| `--baud` | 9600 | 波特率 |
| `--slave-id` | 1 | 从站地址 |
| `--start-temp` | 25.0 | 起始温度（℃） |
| `--rate` | 10.0 | 升温速率（℃/min） |
| `--step` | 1.0 | 采样步长（秒） |
| `--noise` | 0.0 | 高斯噪声幅度（℃），0 无噪声 |
| `--probe-disconnect` | 关 | 恒模拟探头断开（恒返回 0x7FFF） |
| `--disconnect-after` | 无 | 运行 N 秒后模拟探头断开 |
| `--verbose` | 关 | 打印收发原始帧（调试） |
| `--self-test` | 关 | 无串口自检（见第五节） |

示例：快速升温 + 噪声 + 运行 30s 后断开：

```bat
python tools/modbus_mock_server/mock_modbus_server.py --port COM10 --start-temp 30 --rate 20 --noise 0.5 --disconnect-after 30
```

---

## 四、配置给 SantokrOCR

1. 启动模拟器（如 `--port COM10`）；
2. 打开 app 的 **⚙ 设备配置 → temp1（豆温）**：
   - `启用` 打开
   - `端口`：选配对**另一端**（`COM11`）
   - `波特率`：9600；`数据位/校验/停止位`：8/N/1
   - `从站地址`：1；`寄存器`：0
3. 保存后在 app 里开始采集，应读到随时间上升的温度。

> 说明：`temp2` 通道默认从站地址是 2，本模拟器只响应从站 1。若测试第二通道，需再开一个
> 模拟器实例（`--slave-id 2`）挂到另一对虚拟串口上。

---

## 五、用项目现有 probe_modbus.py 验证

`probe_modbus.py` 位于项目根目录，是真实设备的裸串口探测脚本。用它验证模拟器：

1. 把 `probe_modbus.py` 顶部的 `PORT = "COM3"` 改成配对另一端（如 `COM11`）；
2. 模拟器运行中，执行：

   ```bat
   python probe_modbus.py
   ```

3. 预期结果：
   - `从站地址 / 传感器型号 / 芯片 / 热电偶类型` 等**厂商自定义查询显示 None** ——
     这些走私有命令（从站 FF、功能码 06），标准 Modbus slave 不响应，属预期；
   - **实时温度监控正常** —— 走功能码 04，应看到温度每秒上升（如 `25.0℃ → 26.0℃ → …`）。

> probe_modbus.py 的实时循环 `ser.read(10)` 用的是超时读，7 字节响应会让每次读取等待约
> 0.5s，属正常现象（真实设备同样如此）。

app 的"自动检测设备"（`core/modbus_config.py` 的 `auto_detect_device`）同样走功能码 04，
能直接发现挂好的模拟器。

---

## 六、无串口自检（无需 com0com）

想先确认模拟器核心逻辑（温度模型 + 寄存器映射 + app 同款客户端调用）而不装 com0com：

```bat
python tools/modbus_mock_server/mock_modbus_server.py --self-test
```

通过 TCP 回环走完整 server→client 链路，验证：正常升温、探头断开返回 0x7FFF、负温度换算。

---

## 七、常见问题

- **`错误：无法打开串口 COMxx`**：com0com 未安装/未建对，或 `--port` 写的是配对外的口。
- **app 读不到温度 / 一直无响应**：确认模拟器在运行、app 端口是配对的另一端、波特率一致；
  检查 `--verbose` 输出，看 app 请求帧是否到达。
- **com0com 端口没出现在列表**：x64 需用 3.x（签名驱动），或用管理员运行 `setupc install`。
- **端口号被占用**：模拟器独占一端，app 独占另一端；若两者都开在同一端口会冲突。

---

## 八、已知限制

- 只实现**功能码 04 读输入寄存器**（温度读取所需）；probe_modbus.py 的厂商私有查询
  （从站地址/版本/传感器配置）不响应。
- 单从站默认地址 1；需要多从站时用多个实例（不同 `--slave-id`）挂不同虚拟串口对。
- 温度是单调线性升温模型，用于链路/协议测试，不模拟烘焙回温、滑行等真实曲线形态。
