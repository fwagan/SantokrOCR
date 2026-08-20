"""
Modbus 温度读取 — PT100 已接好，验证读数
"""
import time

import serial

PORT = "COM3"
BAUD = 9600
SLAVE = 1

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

def read_temperature():
    """功能码 04, 寄存器 0, 返回温度值(℃)"""
    frame = bytes([SLAVE, 0x04, 0x00, 0x00, 0x00, 0x01]) + crc16(bytes([SLAVE, 0x04, 0x00, 0x00, 0x00, 0x01]))
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
    ser.bytesize = 8
    ser.parity = serial.PARITY_NONE
    ser.stopbits = 1
    ser.write(frame)
    time.sleep(0.05)
    resp = ser.read(10)
    ser.close()
    if len(resp) < 5:
        return None
    raw = int.from_bytes(resp[3:5], 'big')
    # 处理有符号
    if raw == 0x7FFF:
        return "NO_PROBE"
    if raw >= 0x8000:
        raw -= 65536
    return raw / 10  # 温度（℃）

def query_device(frame, resp_len=10):
    """发送查询命令，返回原始响应（带调试）"""
    full_frame = frame + crc16(frame)
    ser = serial.Serial(PORT, BAUD, timeout=0.5)
    ser.bytesize = 8
    ser.parity = serial.PARITY_NONE
    ser.stopbits = 1
    ser.write(full_frame)
    time.sleep(0.15)
    resp = ser.read(resp_len)
    ser.close()
    if resp:
        print(f"  发送: {full_frame.hex()} → 响应: {resp.hex()} ({len(resp)} bytes)")
    else:
        print(f"  发送: {full_frame.hex()} → 无响应")
    return resp

def read_slave_address():
    """查询从站地址 (FF 06 02 65 0C 02)
       响应: FF 06 02 65 0D XX CRCL CRCH → XX 为从站地址 (index 5)"""
    resp = query_device(bytes([0xFF, 0x06, 0x02, 0x65, 0x0C, 0x02]))
    if len(resp) >= 8:
        return resp[5]
    return None

def read_version():
    """查询版本号 (FF 06 03 65 0C 03)
       响应: FF 06 03 65 0D AA BB CC CRCL CRCH
       AA=型号(5), BB=主版本(6), CC=次版本(7)"""
    resp = query_device(bytes([0xFF, 0x06, 0x03, 0x65, 0x0C, 0x03]), resp_len=12)
    if len(resp) >= 10:
        model = resp[5]
        ver_major = resp[6]
        ver_minor = resp[7]
        models = {1: "DS18B20", 2: "PT100", 3: "AHT2415", 4: "LCD"}
        return models.get(model, f"未知({model})"), f"V{ver_major}.{ver_minor}"
    return None

def read_sensor_config():
    """查询传感器配置 (FF 06 05 65 0C 04)
       响应: FF 06 05 65 0D XX YY CRCL CRCH
       XX=芯片(5), YY=热电偶类型(6)"""
    resp = query_device(bytes([0xFF, 0x06, 0x05, 0x65, 0x0C, 0x04]))
    if len(resp) >= 8:
        mode = resp[5]
        stype = resp[6]
        modes = {0: "MAX31865", 1: "MAX31856", 2: "DS18B20"}
        types = {0: "B", 1: "E", 2: "J", 3: "K", 4: "N", 5: "R", 6: "S", 7: "T"}
        return modes.get(mode, f"未知({mode})"), types.get(stype, f"未知({stype})")
    return None

def main():
    print("=" * 60)
    print("USB Modbus 温度读取器 — 设备信息")
    print("=" * 60)

    # 查询从站地址
    addr = read_slave_address()
    print(f"从站地址: {addr}")

    # 查询版本
    ver = read_version()
    if ver:
        print(f"传感器型号: {ver[0]}")
        print(f"固件版本:   {ver[1]}")

    # 查询传感器配置
    cfg = read_sensor_config()
    if cfg:
        print(f"芯片:        {cfg[0]}")
        print(f"热电偶类型:  {cfg[1]}")

    print()
    print("=" * 60)
    print("实时温度监控（按 Ctrl+C 退出）")
    print("=" * 60)
    print(f"{'时间':>8} | {'原始':>6} | {'温度(℃)':>8}")
    print("-" * 60)

    try:
        while True:
            temp = read_temperature()
            ts = time.strftime("%H:%M:%S")
            if temp is None:
                print(f"{ts:>8} | {'无响应':>6}")
            elif temp == "NO_PROBE":
                print(f"{ts:>8} | 0x7FFF |  无探头")
            else:
                print(f"{ts:>8} |        | {temp:>7.1f} ℃")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n监控停止")

if __name__ == "__main__":
    main()
