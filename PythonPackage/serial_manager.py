import os
import serial
import multiprocessing as mp
import time
import threading
from datetime import datetime
import queue
from collections import deque


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

        # 跨进程就绪事件：日志子进程真正打开串口成功后 set()，
        # 主进程 wait() 以确认串口已可用(Windows spawn 启动子进程较慢，命令不能过早发送)
        self._ready_event = mp.Event()

        # 后台行监听状态（持续接收并缓存最近的行，供 get_last_line() 使用）
        self._listener_thread = None
        self._listener_stop_event = threading.Event()
        self._lines = deque(maxlen=200)  # 缓存最近 200 行
        self._last_line = None
        self._line_lock = threading.Lock()

    def __getstate__(self):
        """Windows 下 multiprocessing 使用 spawn 方式启动子进程，
        进程目标(self 的绑定方法 _logging_worker)需要对整个实例做 pickle。
        这里剔除线程/事件/锁/进程/串口等不可序列化字段，
        避免报错 "cannot pickle '_thread.lock' object"。
        """
        state = self.__dict__.copy()
        for key in ('log_process', 'serial_conn',
                    '_listener_thread', '_listener_stop_event', '_line_lock'):
            state.pop(key, None)
        return state

    def __setstate__(self, state):
        """反序列化后补齐被剔除的字段，保证对象结构完整"""
        self.__dict__.update(state)
        self.log_process = None
        self.serial_conn = None
        self._listener_thread = None
        self._listener_stop_event = threading.Event()
        self._line_lock = threading.Lock()

    def start_logging(self):
        """启动日志保存进程"""
        if self.log_process and self.log_process.is_alive():
            print("日志进程已经在运行")
            return

        self._ready_event.clear()
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

    def wait_ready(self, timeout=30):
        """等待串口日志子进程真正打开串口。

        Windows 下子进程用 spawn 启动，会重新 import 调用方顶部的重库(torch/cv2等)，
        start_logging() 返回后串口可能还要数秒才能打开。若过早发送，
        命令会堆积在 send_queue 里发不出去。此方法等待子进程 set 就绪事件。
        :param timeout: 最长等待秒数
        :return: 串口已就绪返回 True；子进程已退出或超时返回 False
        """
        if self.log_process and not self.log_process.is_alive():
            print(f"串口日志进程已退出(可能 {self.port} 被占用或打开失败)")
            return False
        if self._ready_event.wait(timeout):
            print("串口已就绪")
            return True
        print(f"等待串口就绪超时({timeout}s)，请检查 {self.port} 是否被占用")
        return False

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

    def search(self, target, timeout=30, encoding='utf-8', max_buffer_size=65536):
        """持续从串口读取数据并搜索目标字符串，搜索到立即停止并返回 True。

        注意：搜索采用字节级累积，即使目标字符串被拆成多段接收也能正确匹配。
        使用期间请勿同时调用 receive_data()/check_receive_queue()，以免争抢数据。

        :param target: 要搜索的字符串，例如 "factory auto test loop start"
        :param timeout: 搜索总超时(秒)，超时仍未找到则返回 False
        :param encoding: 目标字符串的编码，默认 utf-8
        :param max_buffer_size: 累积缓冲区上限(字节)，超过后只保留最近的数据，防止内存无限增长
        :return: 搜索到返回 True，超时或出错返回 False
        """
        if not self.log_process or not self.log_process.is_alive():
            print("串口日志进程未启动，无法读取数据，请先调用 start_logging()")
            return False

        target_bytes = target.encode(encoding)
        buffer = b""
        deadline = time.time() + timeout
        print(f"开始搜索目标字符串: {target}, 超时: {timeout}s")

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                data = self.receive_queue.get(timeout=remaining)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"读取串口数据出错: {e}")
                return False

            if isinstance(data, str):  # 防御性：兼容传入字符串的情况
                data = data.encode(encoding, errors='replace')
            buffer += data
            # 控制缓冲区大小，只保留最近的数据
            if len(buffer) > max_buffer_size:
                buffer = buffer[-max_buffer_size:]

            if target_bytes in buffer:
                print(f"搜索到目标字符串: {target}")
                return True

        print(f"搜索超时({timeout}s)，未找到目标字符串: {target}")
        return False

    def start_listening(self, keep_lines=200):
        """启动后台监听线程：持续消费 receive_queue，按行解析并缓存最近的行。

        日志文件仍由独立子进程(_logging_worker)记录，本线程只额外维护内存中的
        "最近行"缓存，供 get_last_line()/get_recent_lines() 读取，不阻塞任何流程。

        注意：监听期间会接管 receive_queue 的读取，请勿同时使用
        receive_data()/check_receive_queue()/search()，以免争抢数据。

        :param keep_lines: 缓存最近行数
        :return: 成功返回 True
        """
        if not self.log_process or not self.log_process.is_alive():
            print("串口日志进程未启动，无法监听数据，请先调用 start_logging()")
            return False
        if self._listener_thread and self._listener_thread.is_alive():
            return True

        self._lines = deque(maxlen=keep_lines)
        self._listener_stop_event.clear()
        self._listener_thread = threading.Thread(
            target=self._listener_worker,
            daemon=True,
            name="SerialListenerThread",
        )
        self._listener_thread.start()
        print(f"数据监听线程已启动(缓存最近 {keep_lines} 行)")
        return True

    def stop_listening(self):
        """停止后台监听线程"""
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_stop_event.set()
            self._listener_thread.join(timeout=2)
            print("数据监听线程已停止")
            return True
        return False

    def get_last_line(self):
        """获取最近收到的完整一行数据(文本)。未收到过完整行返回 None。
        若监听未启动且日志进程在运行，会自动启动监听。
        """
        if not self._listener_thread or not self._listener_thread.is_alive():
            self.start_listening()
        with self._line_lock:
            return self._last_line

    def get_recent_lines(self, count=10):
        """获取最近收到的 count 行数据(文本)，按时间从旧到新返回列表。
        若监听未启动且日志进程在运行，会自动启动监听。
        """
        if not self._listener_thread or not self._listener_thread.is_alive():
            self.start_listening()
        with self._line_lock:
            lines = list(self._lines)
        return lines[-count:]

    def _listener_worker(self):
        """监听线程工作函数：消费 receive_queue，按行拆分并更新最近行缓存"""
        buffer = b""
        while not self._listener_stop_event.is_set():
            try:
                data = self.receive_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if isinstance(data, str):  # 防御性：兼容字符串
                data = data.encode('utf-8')
            buffer += data
            if len(buffer) > 65536:  # 防止缓冲无限增长，只保留尾部
                buffer = buffer[-65536:]

            # 按换行符切出完整行（不完整的尾部残留在 buffer，等下一包拼上）
            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line_bytes = line_bytes.rstrip(b"\r")
                if line_bytes:
                    line = self._decode_line_bytes(line_bytes)
                    with self._line_lock:
                        self._lines.append(line)
                        self._last_line = line

    @staticmethod
    def _decode_line_bytes(line_bytes):
        """尝试按 utf-8 / gbk / latin-1 顺序解码一行原始字节"""
        try:
            return line_bytes.decode('utf-8')
        except UnicodeDecodeError:
            try:
                return line_bytes.decode('gbk')
            except UnicodeDecodeError:
                return line_bytes.decode('latin-1', errors='replace')

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

            # 通知主进程：串口已真正打开，可以开始发送命令了
            self._ready_event.set()

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
                        # print(f"发送: {hex_str}")
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
                                # print(f"接收(hex): {hex_data}")
                            else:
                                # 尝试多种编码格式
                                decoded_success = False
                                encodings_to_try = ['utf-8', 'gbk', 'latin-1', 'cp1252', 'iso-8859-1']

                                for encoding in encodings_to_try:
                                    try:
                                        received_str = received_data.decode(encoding, errors='strict')
                                        # 如果解码成功，检查是否包含过多乱码字符
                                        if encoding == 'utf-8' or encoding == 'gbk':
                                            # 检查文本质量，避免使用错误的编码
                                            problematic_chars = sum(
                                                1 for c in received_str if ord(c) > 127 and c not in '°±×÷')
                                            if problematic_chars > len(received_str) * 0.3:  # 如果超过30%的非常规字符，可能编码错误
                                                continue

                                        decoded_success = True
                                        # 按行处理日志记录
                                        lines = received_str.split('\n')
                                        for line in lines:
                                            line = line.strip()
                                            if line:
                                                timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                                log_entry = f"[{timestamp}] {line}\n"
                                                log_f.write(log_entry)
                                                log_f.flush()
                                                # print(f"接收({encoding}): {line}")
                                        break
                                    except UnicodeDecodeError:
                                        continue

                                # 如果所有编码都失败，记录十六进制
                                if not decoded_success:
                                    hex_data = received_data.hex()
                                    timestamp = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
                                    log_entry = f"[{timestamp}] 接收(hex): {hex_data}\n"
                                    log_f.write(log_entry)
                                    log_f.flush()
                                    # print(f"接收(hex): {hex_data}")

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