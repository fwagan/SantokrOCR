#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modbus 模拟器一键启动脚本

在 terminal 里运行（当前 conda 环境即可，无需 cmd）：

  python start_mock_modbus.py            # 默认绑定 COM98
  python start_mock_modbus.py COM90      # 指定端口
  python start_mock_modbus.py COM90 --rate 20   # 端口 + 透传其它模拟器参数

直接复用同目录的 mock_modbus_server.py，本脚本只负责补默认端口参数。
"""

import os
import subprocess
import sys

DEFAULT_PORT = "COM98"  # 与本机 VSPE 虚拟串口对对应


def main() -> int:
    args = list(sys.argv[1:])

    # 第一个非 "-" 开头的参数当作端口，其余原样透传给模拟器
    port = DEFAULT_PORT
    if args and not args[0].startswith("-"):
        port = args.pop(0)

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "mock_modbus_server.py")
    cmd = [sys.executable, script, "--port", port] + args
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
