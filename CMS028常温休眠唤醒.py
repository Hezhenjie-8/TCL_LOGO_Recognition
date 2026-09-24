import torch
import torchvision.transforms as transforms
from torchvision import models
from datetime import datetime
import serial
import time
import os
import cv2
import random
import re
from PIL import Image
import socket
import requests
from PythonPackage.serial_manager import SerialManager
from PythonPackage import Relay_library
import json
import shutil
import subprocess

log_date_time = datetime.now().strftime('%Y%m%d_%H%M%S_')
log_file_path = os.path.dirname(os.path.abspath(__file__)) + "\\logging"
if not os.path.exists(log_file_path):
    os.makedirs(log_file_path)
save_path = log_file_path + "\\" + str(log_date_time)

TCL_machine_serial = SerialManager(port="COM5", baudrate=115200, log_file=f"{save_path}TCL_machine_serial.log", receive_as_hex=False)

Relay_open = Relay_library.Relay_OPEN
Relay_close = Relay_library.Relay_CLOSE

log_date_time = datetime.now().strftime('%Y%m%d_%H%M%S_')
pc_name = socket.gethostname()
# url = 'https://open.feishu.cn/open-apis/bot/v2/hook/03fff426-b5ca-4a98-99b6-1318739b46fc'


# 从 config.json 读取配置
def config_get(key, default=None):
    """
    从 config.json 读取配置项，调用示例：get("ipAddress")
    :param key: 配置键名，如 "ipAddress"
    :param default: 键不存在或读取失败时返回的默认值
    :return: 配置值
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        return config_data.get(key, default)
    except FileNotFoundError:
        print(f"配置文件不存在: {config_path}")
        return default
    except Exception as e:
        print(f"读取配置文件 config.json 失败: {e}")
        return default

def wait_serial_ready(timeout=30):
    """等待串口后台进程真正打开串口(内部通过跨进程就绪事件实现)。

    Windows 下日志子进程用 spawn 启动，会重新 import 脚本顶部的 torch/cv2 等重库，
    串口从 start_logging() 到真正打开需要数秒。若不等就绪就 send，命令会堆积在
    send_queue 里发不出去(表现为"每次第一条刷key都失败、第二条才成功")。
    :param timeout: 最长等待秒数
    :return: True 表示串口已就绪；False 表示超时或 COM 口被占用
    """
    ok = TCL_machine_serial.wait_ready(timeout=timeout)
    if ok:
        my_write_log("串口已就绪")
    else:
        my_write_log(f"等待串口就绪失败({timeout}s)，请检查 {TCL_machine_serial.port} 是否被占用")
    return ok

# 继电器的函数
def Relay_cmd(open_or_close_cmd):
    retries = 5
    try:
        ser_Relay = serial.Serial("COM16", 115200, timeout=2)  # 继电器的COM
        for i in range(3):
            ser_Relay.write(open_or_close_cmd.encode('utf-8'))
            time.sleep(0.5)
            len_return_data = ser_Relay.inWaiting()
            return_data = ser_Relay.read(len_return_data)
            my_write_log(str(return_data))
            if "success" in return_data.decode('utf-8'):
                my_write_log("继电器命令成功")
                ser_Relay.close()
                return True
            else:
                my_write_log("继电器命令失败,1s后尝试重发")
                time.sleep(1)
        return False
        ser_Relay.close()
    except Exception as e:
        if retries <= 0:
            raise e  # 重试次数用完，抛出异常
        retries -= 1
        print(f"Error: {e}. Retrying in 3 seconds...")
        time.sleep(3)  # 等待3秒后重试
        ser_Relay = serial.Serial("COM16", 115200, timeout=2)  # 继电器的COM
        ser_Relay.write(open_or_close_cmd.encode('utf-8'))
        time.sleep(0.2)
        len_return_data = ser_Relay.inWaiting()
        return_data = ser_Relay.read(len_return_data)
        my_write_log(str(return_data))
        ser_Relay.close()

def create_timestamped_folder(base_path):
    """创建以当前时间命名的文件夹"""
    folder_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_path = os.path.join(base_path, folder_name)
    os.makedirs(full_path, exist_ok=True)
    return full_path

def my_write_log(string):
    print(string)
    date_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f:')
    string = str(date_time) + string + "\n"
    # 在当前文件的绝对路径下，创建log文件夹
    log_file_path = os.path.dirname(os.path.abspath(__file__)) + "\\log"  # 获取当前文件的绝对路径
    if not os.path.exists(log_file_path):
        os.makedirs(log_file_path)
    save_path = log_file_path + "\\" + str(log_date_time) + "log.txt"
    with open(save_path, 'a', encoding='utf-8') as f:
        f.write(string)
        f.close()


def Torchstart(image_path1, image_path2):
    # Load MobileNet pretrained model
    model = models.mobilenet_v3_large(pretrained=True).features.eval()

    # Define preprocessing pipeline
    def preprocess(image_path):
        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor()
        ])
        return transform(Image.open(image_path)).unsqueeze(0)

    # Extract features
    feat1 = model(preprocess(image_path1))
    feat2 = model(preprocess(image_path2))

    # Calculate cosine similarity
    similarity = torch.nn.functional.cosine_similarity(
        feat1.view(1, -1),
        feat2.view(1, -1)
    )
    return similarity.item() * 100  # Convert to Python float and scale to percentage

def get_bootanimation_path():
    # 断电上电 -> 35s后执行 logcat |grep BootAnimation 抓取返回值
    # 抓取到 ZipFileName 后发送 ctrl+C 结束 logcat，只提取 bootanimation 的 zip 路径并返回
    # 获取不到类似数据则断电上电重试，最多重试3次，全部失败返回 False
    max_attempts = 3
    # 匹配 /tclconfig/ 开头的整段路径(字母/数字/下划线/点/横线/斜杠，1个或多个字符)
    # 例：printenv 返回 tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
    #     应提取整段 /tclconfig/.../logo，而不是只取 /tclconfig/ 后的单个字符
    zip_path_pattern = re.compile(r"/tclconfig/[A-Za-z0-9_.\-/]+")

    for attempt in range(1, max_attempts + 1):
        # 1. 断电上电
        ok = enter_uboot_model()  # 进入uboot模式
        if ok:
            my_write_log("进入uboot模式成功")
        else:
            my_write_log("进入uboot模式失败")
        my_write_log(f"第{attempt}/{max_attempts}次获取开机动画路径完成")

        TCL_machine_serial.send_data("printenv tcl_logo_path\r")
        my_write_log("已发送命令: printenv tcl_logo_path，正在等待抓取返回值...")

        # 3. 轮询串口返回的新数据，查找 ZipFileName 并提取 zip 路径
        bootanim_zip = ""
        seen_lines = set()  # 只处理本次命令之后新出现的行，避免误用缓存中的旧数据
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(1)
            for line in TCL_machine_serial.get_recent_lines(count=10):
                if line in seen_lines:
                    continue
                seen_lines.add(line)
                if "tcl_logo_path=" in line:
                    match = zip_path_pattern.search(line)
                    if match:
                        bootanim_zip = match.group(0)
                        my_write_log(f"抓取到返回值: {line}")
                        my_write_log(f"提取到的tcl_logo_path路径: {bootanim_zip}")
                        break
            if bootanim_zip:
                break

        # 4. 发送 ctrl+C (hex 03) 结束 logcat 命令
        TCL_machine_serial.send_hex_data("03")
        my_write_log("已发送 ctrl+C 结束 logcat")

        if bootanim_zip:
            return bootanim_zip
        my_write_log(f"第{attempt}/{max_attempts}次未抓取到BootAnimation的ZipFileName路径，2s后重试")
        time.sleep(2)

    my_write_log("断电上电重试3次后，仍未获取到bootanimation zip路径")
    return False

def run_adb_command(args, timeout=30, expect_result=True):
    """
    执行一条 adb 命令(通用)，带"该命令是否有结果返回"的配置。

    注意: 不是每条 adb 命令执行后都会有 stdout/stderr 结果，例如:
        adb shell input keyevent KEYCODE_DPAD_DOWN
    只是向设备发送一个按键，设备立刻有反应但命令本身没有任何输出返回。
    这类命令的成败判断只看 returncode，不能因为没有输出就误判失败，
    因此需要通过 expect_result 告诉函数该命令是否应该有结果返回。

    :param args: 完整命令参数列表，如 ["adb", "-s", "192.168.1.1:5555", "shell", "input", "keyevent", "KEYCODE_DPAD_DOWN"]
    :param timeout: 执行超时(秒)，超时返回 (-1, "")
    :param expect_result: 该命令是否应有结果输出
        True  = 命令应有输出(如 adb connect / adb pull / adb shell ls)，正常返回 (returncode, 输出文本)，
                由调用方根据输出内容进一步判断；
        False = 命令没有结果返回(如 adb shell input keyevent / adb shell screencap 等纯执行类命令)，
                成功与否只看 returncode，输出固定返回 ""。
    :return: (returncode, 输出文本)
    :rtype: (int, str)
    """
    # 用str()兜底，避免参数里混入None时 join 直接抛 TypeError
    cmd_str = " ".join(str(a) for a in args)
    try:
        # Windows下默认用GBK解码adb输出，遇非GBK字节会在reader线程抛UnicodeDecodeError导致输出丢失，
        # 故显式指定 utf-8 解码 + errors="replace" 兜底
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
        if not expect_result:
            # 无结果返回的命令: 不做"无输出即失败"的判断，只看返回码
            my_write_log(f"adb 命令已执行(无结果输出命令，只看返回码): {cmd_str} -> 返回码: {result.returncode}")
            return result.returncode, ""
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        my_write_log(f"adb 命令执行超时(>{timeout}s): {cmd_str}")
        return -1, ""
    except Exception as e:
        my_write_log(f"adb 命令执行异常: {cmd_str} -> {e}")
        return -1, ""


def adb_connect(ip_address=None):
    """
    连接 adb 设备(adb connect)。
    拆分自原 adb_connect_and_pull_tclconfig，仅负责建立 adb 连接。

    :param ip_address: 设备 IP；不传时自动从 config.json 读取 ipAddress
    :return: 连接成功返回设备描述符(如 "192.168.1.1:5555")，失败返回 False
    :rtype: str / bool
    """
    if not ip_address:
        ip_address = config_get("ipAddress")
    if not ip_address:
        my_write_log("config.json 中未配置 ipAddress，无法进行 adb 连接")
        return False
    my_write_log(f"从 config.json 获取到 ipAddress: {ip_address}")

    device = f"{ip_address}:5555"
    connect_code, connect_output = run_adb_command(["adb", "connect", ip_address], timeout=30,
                                                   expect_result=True)
    my_write_log(f"adb connect 返回码: {connect_code}, 输出: {connect_output}")
    if connect_code != 0 or "connected" not in connect_output.lower():
        my_write_log(f"adb 连接失败，请检查设备是否开启网络 adb 调试: {ip_address}")
        return False
    return device


def wait_for_device_online(device, timeout=60):
    """
    等待 adb 设备重新上线(adb root 会重启 adbd，设备会短暂 offline)。

    注意: "already connected to xxx" 只表示连接记录存在，不代表设备在线，
    必须用 adb get-state 确认真实状态为 device，否则后续 pull 会报 device offline。

    :param device: 设备描述符，如 "192.168.0.155:5555"
    :param timeout: 最长等待秒数
    :return: True 设备已在线；False 超时
    :rtype: bool
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, output = run_adb_command(["adb", "-s", device, "get-state"], timeout=10,
                                       expect_result=True)
        # adb get-state 在线时输出就是 "device"，离线是 "offline"
        state = (output or "").strip().lower()
        if code == 0 and state == "device":
            my_write_log(f"设备已上线: {device}")
            return True
        my_write_log(f"等待设备上线中... 当前状态: {state or 'offline'}")
        # 状态异常时先断开再重连，避免 "already connected" 造成的假在线
        run_adb_command(["adb", "disconnect", device], timeout=10, expect_result=True)
        run_adb_command(["adb", "connect", device], timeout=15, expect_result=True)
        time.sleep(3)
    my_write_log(f"等待设备上线超时({timeout}s): {device}")
    return False


def adb_connect_and_pull_tclconfig(tcl_logo_path=None):
    """
    总入口(保持原函数名): adb connect 连接设备 -> adb root ->
    将设备上的 /tclconfig pull 到根目录的 machine 文件夹中。

    说明:
        - 依赖本机 PATH 中可用的 adb 命令
        - adb 连接默认端口 5555，如设备端口不同请修改 adb_connect 中的 device 定义
        - adb root 会重启 adbd，导致连接短暂断开，需等待约5s、
          待设备重新连接后再获取 adb root 的返回(确认已运行在 root 状态)
    :return: 成功返回 True，失败返回 False
    """
    # 入参校验: 拉取源不能为空，否则命令拼不出来(会抛 NoneType 异常)
    if not tcl_logo_path:
        my_write_log("adb pull 失败: tcl_logo_path 为空，无法确定要拉取的设备路径")
        return False
    tcl_logo_path_1 = tcl_logo_path

    # 1. adb 连接设备
    device = adb_connect()
    if not device:
        return False

    # 2. 执行 adb root（adbd 会重启，设备短暂离线，首次返回可能拿不全）
    root_code, root_output = run_adb_command(["adb", "-s", device, "root"], timeout=15,
                                             expect_result=True)
    my_write_log(f"adb root 首次返回码: {root_code}, 输出: {root_output}")

    # 3. adb root 后 adbd 重启，需等待设备真正重新上线
    #    注意: "already connected" 只代表连接记录存在，不代表设备在线，
    #    必须用 get-state 确认状态为 device，否则 pull 会报 "device offline"
    time.sleep(5)
    if not wait_for_device_online(device, timeout=60):
        my_write_log(f"adb root 后设备未上线，无法 pull: {device}")
        return False

    root_code2, root_output2 = run_adb_command(["adb", "-s", device, "root"], timeout=15,
                                               expect_result=True)
    my_write_log(f"adb root 等待上线后获取返回码: {root_code2}, 输出: {root_output2}")
    # 若本次 root 又触发 adbd 重启(restarting adbd as root)，需再等一次上线
    if "restarting" in (root_output2 or "").lower():
        if not wait_for_device_online(device, timeout=60):
            my_write_log(f"adb root 重启 adbd 后设备未上线: {device}")
            return False

    # 4. pull 到根目录 machine 下按时间戳命名的文件夹
    machine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "machine",datetime.now().strftime('%Y%m%d_%H%M%S'))
    os.makedirs(machine_dir, exist_ok=True)

    # pull 失败(设备短暂离线等)时重试，最多3次，每次先等设备重新上线
    max_pull_attempts = 3
    for attempt in range(1, max_pull_attempts + 1):
        pull_cmd = ["adb", "-s", device, "pull", tcl_logo_path_1, machine_dir]
        my_write_log(f"执行 adb pull 命令: {' '.join(pull_cmd)}")
        pull_code, pull_output = run_adb_command(pull_cmd, timeout=600, expect_result=True)
        my_write_log(f"adb pull 返回码: {pull_code}, 输出: {pull_output}")
        if pull_code == 0:
            my_write_log(f"{tcl_logo_path_1} 数据已成功 pull 到: {machine_dir}")
            return True
        my_write_log(f"第{attempt}/{max_pull_attempts}次 adb pull 失败: {pull_output}")
        if attempt < max_pull_attempts:
            wait_for_device_online(device, timeout=60)

    my_write_log(f"adb pull 重试{max_pull_attempts}次后仍失败")
    return False

def enter_uboot_model():
    """
    进入uboot模式
    :return:
    :rtype:
    """
    platform = config_get("platform")
    my_write_log(f"platform: {platform}")
    for i in range(4):
        Relay_cmd("power off\r")
        time.sleep(3)
        Relay_cmd("power on\r")
        start_time = time.time()
        while time.time() - start_time < 10:    
            TCL_machine_serial.send_hex_data("0D")
            time.sleep(0.005)

            TCL_machine_serial.send_hex_data("20")
            time.sleep(0.005)

            TCL_machine_serial.send_hex_data("03")
            time.sleep(0.005)

        time.sleep(15)
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        for i in range(5):
            data = TCL_machine_serial.get_recent_lines(count=3)
            # data 是列表，先拼成文本再做子串判断
            data_text = "\n".join(str(line) for line in data)
            my_write_log(f"data: {data}")
            if "console:/ $" in data_text:
                my_write_log("进入uboot模式失败")
                time.sleep(2)
            else:
                if platform == "RTK":
                    if "Realtek>" in data_text:
                        my_write_log("进入uboot模式成功")
                        return True
                if platform == "MTK":
                    if "=>" in data_text:
                        my_write_log("进入uboot模式成功")
                        return True
                if platform == "AML":
                    if "t5m_ay30a" in data_text or "t6d_br30ab" in data_text or "t6x_bu30a6" in data_text:
                        my_write_log("进入uboot模式成功")
                        return True
    else:
        my_write_log("进入uboot模式失败")
        return False

def enter_Pmode():
    """
    进入enter_Pmode模式
    :return:
    :rtype:
    """
    platform = config_get("platform")
    if platform == "RTK":
        TCL_machine_serial.send_data(f"pmode enable;re\r")
    if platform == "MTK":
        TCL_machine_serial.send_data(f"uuenv_init;uuenv_set Pmode 1;uuenv_save;res\r")
    if platform == "AML":
        TCL_machine_serial.send_data(f"setenv Pmode 1;saveenv;reboot\r")
    time.sleep(25)
    data = TCL_machine_serial.get_recent_lines(count=2)
    data_text = "\n".join(str(line) for line in data)
    my_write_log(f"data: {data_text}")
    # 串口出现 factory auto test loop start 说明 Pmode=1 已生效，工厂自动测试循环已启动
    if "factory auto test loop start" in data_text:
        my_write_log("进入Pmode模式成功")
        return True
    else:
        my_write_log("进入Pmode模式失败")
        return False

def exit_Pmode():
    """
    退出Pmode模式
    :return:
    :rtype:
    """
    platform = config_get("platform")
    if platform == "RTK":
        TCL_machine_serial.send_data(f"pmode disable;re\r")
    if platform == "MTK":
        TCL_machine_serial.send_data(f"uuenv_init;uuenv_set Pmode 0;uuenv_save;res\r")
    if platform == "AML":
        TCL_machine_serial.send_data(f"setenv Pmode 0;saveenv;reboot\r")
    time.sleep(25)
    TCL_machine_serial.send_hex_data("0D")
    TCL_machine_serial.send_hex_data("0D")
    TCL_machine_serial.send_hex_data("0D")
    data = TCL_machine_serial.get_recent_lines(count=2)
    data_text = "\n".join(str(line) for line in data)
    my_write_log(f"data_text: {data_text}")
    if "console:/ $" in data_text:
        my_write_log("退出Pmode模式成功")
        return True
    else:
        my_write_log("退出Pmode模式失败")
        return False

def brush_key_crc(cmd, timeout=5, expect_hex_len=0):
    """
    刷入key值(发送一条hex协议帧，等待设备应答)
    注意：设备应答是二进制帧，内容为 hex "AB050ADF4E"；其中含 0x0A，
    会被按行监听线程当成换行劈开并按文本解码成乱码，所以必须在字节流里累积匹配。
    :param cmd: 十六进制指令字符串，如 "AA 06 10 01 A7 EF"
    :type cmd: str
    :param timeout: 等待设备应答的超时时间
    :type timeout: int
    :param expect_hex_len: 期望收到的完整应答hex字符数；0=匹配到应答立即返回。
                           查询PID的应答是两帧共24个字符，只收到第一帧会缺少SID数据
    :type expect_hex_len: int
    :return: 成功返回应答帧hex字符串，超时/失败返回 False
    :rtype: str / bool
    """
    if cmd is None:
        my_write_log("刷入key值失败: cmd 为空")
        return False
    data = bytes.fromhex(cmd.replace(" ", ""))
    my_write_log(f"发送key指令(hex): {data.hex()}")

    # 设备应答(hex去空格) "AB050ADF4E"
    ack_hex = "ab050adf4e"
    # 判断当前是否已有按行监听线程在运行
    had_listener = (TCL_machine_serial._listener_thread is not None
                    and TCL_machine_serial._listener_thread.is_alive())
    if had_listener:
        TCL_machine_serial.stop_listening()  # 暂停按行监听，避免它抢收/劈开应答帧

    try:
        # 先清空发送前可能残留的串口数据，避免把旧数据当成本次应答
        while TCL_machine_serial.check_receive_queue() is not None:
            pass

        # send_hex_data 只入队(无返回值/无timeout参数)，真正的串口写入由后台日志进程完成，
        # 它每次把读到的原始字节原样放入 receive_queue，不会按行切分。
        TCL_machine_serial.send_hex_data(data)

        # 字节流累积匹配(可跨多次read分包、可含0x0A)
        got_hex = ""
        matched = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            resp = TCL_machine_serial.receive_data(timeout=remaining, as_hex=True)
            if resp is None:  # 等待超时
                break
            got_hex += resp.lower()
            if ack_hex in got_hex:
                matched = True
                # 已匹配到应答；若调用方要求更完整的应答(如查询PID的第二帧)，继续等剩余数据
                if len(got_hex) >= expect_hex_len:
                    break
        if matched:
            my_write_log(f"刷入key值成功(应答hex): {got_hex}")
            return got_hex
        my_write_log("刷入key值失败(超时未收到设备应答)")
        return False
    finally:
        # 恢复按行监听，供后续 get_recent_lines 使用
        if had_listener:
            TCL_machine_serial.start_listening()

def pid_to_sid(pid_hex):
    """
    公式：PID = (SID//256)*2^24 + (HID//256)*2^16 + (SID%256)*256 + (HID%256)
    从 PID 十六进制字符串反推 SID
    pid_hex: 8个十六进制字符，如 "12345678"
    """
    pid = int(pid_hex, 16)

    # 提取各字节
    sid_high = (pid >> 24) & 0xFF   # 第1字节：SID 高8位
    hid_high = (pid >> 16) & 0xFF   # 第2字节：HID 高8位
    sid_low  = (pid >> 8)  & 0xFF   # 第3字节：SID 低8位
    hid_low  = pid & 0xFF           # 第4字节：HID 低8位

    # 还原 SID 和 HID
    sid = (sid_high << 8) | sid_low
    hid = (hid_high << 8) | hid_low

    return sid, hid

def parse_pid_ack(ack_hex):
    """
    从"查询PID"指令(AA 06 84 00 63 E1)的应答里解析设备当前 SID/HID。

    应答整体长度不固定(可能分多帧到达)，但数据帧固定以命令"85"开头，格式为:
        85 <数据(4个或8个hex字符)> <CRC1 CRC2(共4个hex字符)>
    例: ab050adf4eab 0785 4e00 78d3
                          │    └ 2字节CRC
                          └ 2字节数据(SID, 小端) 0x004e -> 78
    数据为8个hex字符(4字节)时: 前2字节是SID(小端)，后2字节是HID。
    只收到前面的通用应答帧(不含85数据帧)时返回 (None, None)，不能当0处理。

    :param ack_hex: brush_key_crc 返回的应答hex字符串(可含空格)
    :return: (sid, hid)，解析失败返回 (None, None)
    :rtype: tuple
    """
    if not ack_hex:
        my_write_log("解析PID应答失败: 应答为空")
        return None, None
    hex_str = str(ack_hex).replace(" ", "").lower()

    # 1. 定位命令"85"的数据帧(优先带长度字节的"0785"，避免误命中其它字节)
    start = hex_str.find("0785")
    if start >= 0:
        start += 4
    else:
        start = hex_str.find("85")
        if start < 0:
            my_write_log(f"解析PID应答失败: 未找到命令85数据帧, 应答hex: {hex_str}")
            return None, None
        start += 2

    # 2. 数据帧 = 数据(长度不固定) + 末尾2字节CRC
    tail = hex_str[start:]
    if len(tail) < 8:   # 最少: 2字节数据(4个hex字符) + 2字节CRC(4个hex字符)
        my_write_log(f"解析PID应答失败: 85后数据不足(需至少8个hex字符): {hex_str}")
        return None, None
    data_hex = tail[:-4][:8]        # 去掉末尾2字节CRC，数据最多取4字节
    if len(data_hex) not in (4, 8):
        my_write_log(f"解析PID应答失败: 85后数据长度异常({len(data_hex)}个hex字符): {hex_str}")
        return None, None

    # 3. 前2字节是SID(小端)；数据为4字节时后2字节是HID
    sid = (int(data_hex[2:4], 16) << 8) | int(data_hex[0:2], 16)
    hid = int(data_hex[4:8], 16) if len(data_hex) == 8 else None
    my_write_log(f"PID应答数据(hex): {data_hex} -> SID={sid}, HID={hid}")
    return sid, hid

if __name__ == "__main__":
    test_num = 0
    compare_bool = False
    date_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    date_time = str(date_time)
    ipAddress = config_get("ipAddress")
    try:
        TCL_machine_serial.start_logging()
        # 1. adb 连接设备
        adb_connect()
        # 关键：必须先等串口子进程真正打开串口再发命令，否则命令堆在队列里发不出去
        if not wait_serial_ready():
            my_write_log("串口未就绪，终止本次测试")
            raise RuntimeError("串口未就绪")
        sid_list = config_get("sid_list")
        sid_list = [int(item["sid"]) for item in sid_list] 
        my_write_log(f"SID的列表sid_list: {sid_list}")

        # 第一步：切换PID为0
        ok = enter_uboot_model()  # 进入uboot模式
        if ok:
            my_write_log("进入uboot模式成功")
        else:
            my_write_log("进入uboot模式失败，重试一次")
            enter_uboot_model()  # 进入uboot模式

        ok = enter_Pmode()
        if ok:
            my_write_log("进入Pmode模式成功")
        else:
            my_write_log("进入Pmode模式失败，重试一次")
            enter_Pmode()  # 进入Pmode模式

        ok = brush_key_crc("AA 06 10 01 A7 EF") # 进工厂模式
        if ok:
            my_write_log("进工厂模式成功")
        else:
            my_write_log("进工厂模式失败")

        time.sleep(1)

        ok = brush_key_crc("AA 09 70 00 00 00 00 92 EB") # 切换PID 0
        # ok = brush_key_crc("AA 09 70 00 00 00 01 82 CA") # 切换PID 1
        if ok:
            my_write_log("切换PID 0指令成功")
        else:
            my_write_log("切换PID 0指令失败")

        for sid in sid_list:
            my_write_log(f"当前测试SID: {sid}")

            # 第二步：断电上电后，切换SID为 当前测试的sid
            Relay_cmd("power off\r")
            time.sleep(2)
            Relay_cmd("power on\r")
            time.sleep(25)  # 等待25秒，确保设备启动完成
            ok = brush_key_crc("AA 06 10 01 A7 EF") # 进工厂模式
            if ok:
                my_write_log("进工厂模式成功")
            else:
                my_write_log("进工厂模式失败")

            time.sleep(1)

            ok = brush_key_crc("AA 06 11 01 94 DE") # 显示工厂模式
            if ok:
                my_write_log("显示工厂模式成功")
            else:
                my_write_log("显示工厂模式失败")
            time.sleep(1)

            for i in range(10): # 按下DPAD_DOWN键10次
                code, _ = run_adb_command(
                    ["adb", "-s", ipAddress, "shell", "input", "keyevent", "KEYCODE_DPAD_DOWN"],
                    timeout=10, expect_result=False,
                )
                if code == 0:
                    my_write_log("按键下已发送")
                time.sleep(0.5)
            code, _ = run_adb_command(
                ["adb", "-s", ipAddress, "shell", "input", "keyevent", "KEYCODE_DPAD_CENTER"],
                timeout=10, expect_result=False,
            )
            if code == 0:
                my_write_log("按键OK已发送")
            time.sleep(0.5)
            code, _ = run_adb_command(
                    ["adb", "-s", ipAddress, "shell", "input", "keyevent", "KEYCODE_DPAD_DOWN"],
                    timeout=10, expect_result=False,
                )
            if code == 0:
                my_write_log("按键下已发送")
            time.sleep(0.5)
            # 开始输入SID：把SID(int)拆成单个数字，按 KeyEvent 数值逐个用adb发送
            # 数字n的KeyCode = n + 7 (KEYCODE_0=7 ... KEYCODE_9=16)
            # 例：SID=15 -> 先发 keyevent 8('1')，再发 keyevent 12('5')
            for digit in str(sid):
                keycode = int(digit) + 7
                code, _ = run_adb_command(
                    ["adb", "-s", ipAddress, "shell", "input", "keyevent", str(keycode)],
                    timeout=10, expect_result=False,
                )
                if code == 0:
                    my_write_log(f"SID数字{digit}按键已发送(keycode={keycode})")
                else:
                    my_write_log(f"SID数字{digit}按键发送失败")
                time.sleep(0.05)
            time.sleep(10)

            # 加一个PID确认：应答长度不固定(85后的数据区可能4个或8个hex字符)，
            # 需要等含"85"数据帧的那部分收齐再解析，避免 int('',16) 报错
            ok = brush_key_crc("AA 06 84 00 63 E1", timeout=5, expect_hex_len=20)  # 查询PID指令
            sid_machine = None
            if ok:
                my_write_log("查询PID指令成功")
                sid_machine, hid_machine = parse_pid_ack(ok)
                if sid_machine is not None:
                    my_write_log(f"当前机器的SID: {sid_machine} (HID: {hid_machine})")
                else:
                    my_write_log("查询PID指令成功，但应答中未解析到SID")
            else:
                my_write_log("查询PID指令失败")
            if sid_machine is not None and int(sid_machine) == int(sid):
                my_write_log(f"切换SID为 {sid} 成功")
            else:
                my_write_log(f"切换SID为 {sid} 失败(设备回读SID: {sid_machine})")
            
            Relay_cmd("power off\r")
            time.sleep(2)
            Relay_cmd("power on\r")
            time.sleep(25)  # 等待25秒，确保设备启动完成并且生效SID
            my_write_log(f"切换SID为 {sid} 完成，等待设备启动完成")


            # 第三步：开机logo：Uboot输入printenv tcl_logo_path，并且adb pull /tclconfig 到本地 machine 文件夹中
            my_write_log(f"uboot下获取开机logo的地址")
            tcl_logo_path = get_bootanimation_path()
            my_write_log(f"tcl_logo_path: {tcl_logo_path}")   # tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
            Relay_cmd("power off\r")
            time.sleep(2)
            Relay_cmd("power on\r")
            time.sleep(35)  # 等待35秒，确保设备启动完成
            tcl_logo_path = os.path.dirname(tcl_logo_path)
            ok = adb_connect_and_pull_tclconfig(tcl_logo_path)
            if ok:
                my_write_log(f"拉取 {tcl_logo_path} 完成")
            else:
                my_write_log(f"拉取 {tcl_logo_path} 失败，再次尝试拉取 {tcl_logo_path}")
                adb_connect_and_pull_tclconfig(tcl_logo_path)

            # # 第四步：解压tcl_logo_path的压缩包的customized_bootanimation.zip，并且拿到最后一张开机动画的图片
            
            # my_write_log(f"解压tcl_logo_path的压缩包的customized_bootanimation.zip，并且拿到最后一张开机动画的图片")
            # last_boot_image_path = get_last_boot_image_path(tcl_logo_path)
            # my_write_log(f"last_boot_image_path: {last_boot_image_path}")

            # # 第五步：对比最后一张开机动画的图片和本地的template文件夹中的图片进行图像识别

            # my_write_log(f"对比最后一张开机动画的图片和本地的template文件夹中的图片进行图像识别")
            # compare_bool_1 = Torchstart(last_boot_image_path, "template")
            # if compare_bool_1:
            #     my_write_log(f"对比成功")
            # else:
            #     my_write_log(f"对比失败")

            # # 第六步：拆分tcl_logo_path路径"./common_TCL/" + logo的路径下的静态logo path路径，和tclconfig文件夹下的logo路径进行对比
            
            # my_write_log(f"拆分tcl_logo_path路径./common_TCL/ + logo的路径下的静态logo path路径，和tclconfig文件夹下的logo路径进行对比")
            # compare_bool_2 = Torchstart(last_boot_image_path, "template")
            # if compare_bool_2:
            #     my_write_log(f"对比成功")
            # else:
            #     my_write_log(f"对比失败")

            # # 第七步：如果静态logo和开机动画的图片都对比成功，继续下一个SID的测试，如果对比失败，记录日志并且存在fail_list列表中后面做结果输出
            # if compare_bool_1 and compare_bool_2:
            #     my_write_log(f"对比成功")
            # else:
            #     my_write_log(f"对比失败")
            #     continue  # 跳过当前 SID，继续下一个 SID
            

            my_write_log(f"拉取{sid}tclconfig完成")
        

        response1 = TCL_machine_serial.receive_data(timeout=0.5)

    except Exception as e:
        my_write_log(f"发生错误: {str(e)}")
    finally:
        # 停止日志进程
        TCL_machine_serial.stop_logging()

