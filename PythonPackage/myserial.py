import os
import serial
import multiprocessing as mp
import time
import threading
from datetime import datetime
import queue


class SerialManager:
    def __init__(self, port, baudrate=9600, timeout=1, log_file="serial_log.txt", receive_as_hex=True):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.log_file = log_file
        self.receive_as_hex = receive_as_hex  # 控制接收数据显示格式

        # 用于进程间通信的队列
        self.send_queue = mp.Queue()  # 主进程 -> 日志进程：发送数据
        self.receive_queue = mp.Queue()  # 日志进程 -> 主进程：接收数据
        self.control_queue = mp.Queue()  # 控制命令

        self.log_process = None
        self.serial_conn = None

    def start_logging(self):
        """启动日志保存进程"""
        if self.log_process and self.log_process.is_alive():
            print("日志进程已经在运行")
            return

        self.log_process = mp.Process(
            target=self._logging_worker,
            args=(self.port, self.baudrate, self.timeout, self.log_file,
                  self.send_queue, self.receive_queue, self.control_queue,
                  self.receive_as_hex)
        )
        self.log_process.daemon = True
        self.log_process.start()
        print(f"串口日志进程已启动，日志保存到: {self.log_file}")

    def stop_logging(self):
        """停止日志保存进程"""
        if self.log_process and self.log_process.is_alive():
            self.control_queue.put("STOP")
            self.log_process.join(timeout=5)
            if self.log_process.is_alive():
                self.log_process.terminate()
            print("串口日志进程已停止")

    def send_data(self, data):
        """主进程发送数据到串口"""
        if isinstance(data, str):
            data = data.encode('utf-8')
        self.send_queue.put(data)

    def send_hex_data(self, hex_data):
        """发送16进制数据
        参数:
            hex_data: 可以是以下格式之一:
                - 16进制字符串，例如: "30 40 10" 或 "304010"
                - 整数列表，例如: [0x30, 0x40, 0x10] 或 [48, 64, 16]
                - 字节数组
        """
        if isinstance(hex_data, str):
            # 处理16进制字符串
            # 移除空格
            hex_str = hex_data.replace(' ', '')
            # 确保长度是偶数
            if len(hex_str) % 2 != 0:
                raise ValueError("Hex string length must be even")
            try:
                data = bytes.fromhex(hex_str)
            except ValueError as e:
                raise ValueError(f"Invalid hex string: {e}")
        elif isinstance(hex_data, (list, tuple)):
            # 处理整数列表
            try:
                data = bytes(hex_data)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid integer list: {e}")
        elif isinstance(hex_data, bytes):
            # 直接使用字节数据
            data = hex_data
        else:
            raise TypeError("hex_data must be a string, list, tuple or bytes")

        self.send_queue.put(data)
        print(f"发送16进制数据: {data.hex()}")

    def receive_data(self, timeout=1, as_hex=None):
        """主进程从串口接收数据
        as_hex: 是否返回16进制格式，None表示使用类默认设置
        """
        if as_hex is None:
            as_hex = self.receive_as_hex

        try:
            data = self.receive_queue.get(timeout=timeout)
            if as_hex and isinstance(data, bytes):
                return data.hex()
            elif isinstance(data, bytes):
                try:
                    return data.decode('utf-8', errors='replace')
                except UnicodeDecodeError:
                    return data.decode('gbk', errors='replace')
            return data
        except queue.Empty:
            return None

    def check_receive_queue(self, as_hex=None):
        """检查接收队列中是否有数据（非阻塞方式）"""
        if as_hex is None:
            as_hex = self.receive_as_hex

        try:
            data = self.receive_queue.get_nowait()
            if as_hex and isinstance(data, bytes):
                return data.hex()
            elif isinstance(data, bytes):
                try:
                    return data.decode('utf-8', errors='replace')
                except UnicodeDecodeError:
                    return data.decode('gbk', errors='replace')
            return data
        except queue.Empty:
            return None

    def _logging_worker(self, port, baudrate, timeout, log_file,
                        send_queue, receive_queue, control_queue, receive_as_hex):
        """日志进程的工作函数"""
        try:
            # 打开串口连接
            ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE
            )

            print(f"串口 {port} 已打开，波特率: {baudrate}")

            # 打开日志文件
            with open(log_file, 'a', encoding='utf-8') as log_f:
                # 写入开始标记
                start_time = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                log_f.write(f"[BEGIN] {start_time}\n")

                running = True

                while running:
                    # 检查控制命令
                    try:
                        cmd = control_queue.get_nowait()
                        if cmd == "STOP":
                            running = False
                            break
                    except queue.Empty:
                        pass

                    # 1. 发送数据到串口
                    try:
                        send_data = send_queue.get_nowait()
                        ser.write(send_data)
                        # 记录发送的16进制数据
                        timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                        hex_str = send_data.hex()
                        log_entry = f"[{timestamp}] 发送(hex): {hex_str}\n"
                        log_f.write(log_entry)
                        log_f.flush()
                        print(f"发送: {hex_str}")
                    except queue.Empty:
                        pass

                    # 2. 从串口读取数据
                    if ser.in_waiting > 0:
                        received_data = ser.read(ser.in_waiting)
                        if received_data:
                            # 将接收到的原始字节数据放入队列供主进程读取
                            receive_queue.put(received_data)

                            # 根据设置选择显示格式
                            if receive_as_hex:
                                # 以16进制格式显示和记录
                                hex_data = received_data.hex()
                                timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                log_entry = f"[{timestamp}] 接收(hex): {hex_data}\n"
                                log_f.write(log_entry)
                                log_f.flush()
                                print(f"接收(hex): {hex_data}")
                            else:
                                # 尝试解码为UTF-8字符串用于日志记录
                                try:
                                    received_str = received_data.decode('utf-8', errors='replace')

                                    # 按行处理日志记录
                                    lines = received_str.split('\n')
                                    for line in lines:
                                        line = line.strip()
                                        if line:
                                            timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                            log_entry = f"[{timestamp}] {line}\n"
                                            log_f.write(log_entry)
                                            log_f.flush()
                                            print(f"接收: {line}")
                                except UnicodeDecodeError:
                                    # 如果UTF-8解码失败，记录十六进制
                                    hex_data = received_data.hex()
                                    timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                    log_entry = f"[{timestamp}] 接收(hex): {hex_data}\n"
                                    log_f.write(log_entry)
                                    log_f.flush()
                                    print(f"接收(hex): {hex_data}")

                    # 短暂休眠以避免过度占用CPU
                    time.sleep(0.01)

        except serial.SerialException as e:
            print(f"串口错误: {e}")
        except Exception as e:
            print(f"日志进程错误: {e}")
        finally:
            if 'ser' in locals() and ser.is_open:
                ser.close()
            # 写入结束标记
            end_time = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            with open(log_file, 'a', encoding='utf-8') as log_f:
                log_f.write(f"\n[END] {end_time}\n")
            print("串口日志进程结束")


# 使用示例
def main():
    log_date_time = datetime.now().strftime('%Y%m%d_%H%M%S_')
    # 创建串口管理器
    log_file_path = os.path.dirname(os.path.abspath(__file__)) + "\\log"
    # 确保日志目录存在
    if not os.path.exists(log_file_path):
        os.makedirs(log_file_path)
    save_path = log_file_path + "\\" + str(log_date_time) + "log.txt"

    # 创建串口管理器，设置receive_as_hex=True使接收数据显示为16进制
    serial_mgr = SerialManager(
        port="COM15",  # 根据实际情况修改串口号
        baudrate=115200,
        log_file=save_path,
        receive_as_hex=False  # 设置为True，接收数据显示为16进制
    )

    try:
        # 启动日志进程
        serial_mgr.start_logging()

        # 等待串口初始化
        time.sleep(2)

        # 示例：发送一些测试数据
        test_messages = [
            "version\r\n"
        ]

        for msg in test_messages:
            serial_mgr.send_data(msg)
            # 短暂等待接收响应
            time.sleep(0.5)

            # 尝试接收响应（将显示为16进制格式）
            response = serial_mgr.receive_data(timeout=0.5)
            if response:
                print(f"主进程收到响应: {response}")

        # 使用新的send_hex_data函数发送16进制数据
        print("发送16进制数据...")
        serial_mgr.send_hex_data("79 66 00 02 06 0B 00 FD")

        # 短暂等待后检查接收队列
        time.sleep(0.5)
        response = serial_mgr.check_receive_queue()
        if response:
            print(f"主进程收到响应: {response}")
        else:
            print("未收到响应数据")

    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"发生错误: {e}")
    finally:
        # 停止日志进程
        serial_mgr.stop_logging()


if __name__ == '__main__':
    mp.freeze_support()
    main()