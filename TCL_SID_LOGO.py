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
import zipfile
from PythonPackage.logo_similarity import compare_logo_images
from PythonPackage import Report_Handler

log_date_time = datetime.now().strftime('%Y%m%d_%H%M%S_')
log_file_path = os.path.dirname(os.path.abspath(__file__)) + "\\logging"
if not os.path.exists(log_file_path):
    os.makedirs(log_file_path)
save_path = log_file_path + "\\" + str(log_date_time)

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

platform = config_get("platform")
TCL_machine_port = config_get("TCL_machine_serial")
TCL_machine_serial = SerialManager(port=TCL_machine_port, baudrate=115200, log_file=f"{save_path}TCL_machine_serial.log", receive_as_hex=False)

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
    Relay_serial = config_get("Relay_serial")
    try:
        ser_Relay = serial.Serial(Relay_serial, 115200, timeout=2)  # 继电器的COM
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

def to_brand_dir(path):
    """
    把设备返回的 logo / zip 路径统一成机型(品牌)目录，即最终要 pull 的目录。

    三个机芯返回的内容层级不一致，用本函数抹平差异:
        MTK: /tclconfig/.../common_TCL/logo              -> /tclconfig/.../common_TCL
        AML: /tclconfig/.../common_TCL/logo/logo.jpg     -> /tclconfig/.../common_TCL
        RTK: /tclconfig/.../common_TCL/bootanim/xxx.zip  -> /tclconfig/.../common_TCL

    pull 后的目录结构要求是:
        <机型目录>/logo/logo.jpg
        <机型目录>/bootanim/customized_bootanimation.zip

    :param path: 设备返回的原始路径
    :return: 机型目录；空输入返回 ""
    :rtype: str
    """
    if not path:
        return ""
    path = str(path).replace("\\", "/").rstrip("/")
    resource_names = {"logo", "bootanim", "bootanimation", "animation"}
    while True:
        parent, name = os.path.split(path)
        if not name or not parent or parent == path:
            break
        # 具体文件名(带扩展名)和资源子目录都继续向上一级剥
        if "." in name or name.lower() in resource_names:
            path = parent
            continue
        break
    return path


def extract_logo_dir(text):
    """
    从设备返回/日志文本里提取机型(品牌)目录，即最终要 pull 的目录。

    支持两种返回格式:
        1) RTK 开机动画日志(BootAnimation 打印的 zip 路径):
           "... D BootAnimation: cxtest mZipFileName:/tvcust/aio_resources/resource/
            extension/common_TCL_QDMINI_2K/bootanim/customized_bootanimation.zip"
           -> 补 /tclconfig 前缀并去掉 zip 文件名:
              /tclconfig/tvcust/aio_resources/resource/extension/common_TCL_QDMINI_2K/bootanim
              (主流程再 dirname 一次 -> .../common_TCL_QDMINI_2K，即要 pull 的目录)
        2) 直接的 tcl_logo_path(uboot / config_logo_path.bin):
           "tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo"
           -> 原样返回(去掉末尾 "/")

    :param text: 设备返回或日志文本
    :return: 提取到的路径；提取不到返回 ""
    :rtype: str
    """
    if not text:
        return ""

    # 1. mZipFileName 优先：RTK 的开机动画 zip 路径
    zip_match = re.search(r"mZipFileName:\s*(/[^\s\"'\r\n]+)", text)
    if zip_match:
        zip_path = zip_match.group(1)
        if not zip_path.startswith("/tclconfig"):
            zip_path = "/tclconfig" + zip_path
        # 统一剥到机型目录(去掉 zip 文件名与 bootanim 目录)
        return to_brand_dir(zip_path)

    # 2. 直接的 /tclconfig 路径
    path_match = re.search(r"/tclconfig/[A-Za-z0-9_.\-/]+", text)
    if path_match:
        return to_brand_dir(path_match.group(0))
    return ""


def _wait_logo_dir_from_serial(timeout=30):
    """
    轮询串口最近日志，等待出现开机动画 zip 路径(mZipFileName)并提取 logo 目录。

    :param timeout: 最长等待秒数
    :return: 提取到的路径；超时返回 ""
    :rtype: str
    """
    deadline = time.time() + timeout
    seen_lines = set()      # 只处理本次新出现的行，避免误用缓存中的旧数据
    while time.time() < deadline:
        time.sleep(1)
        for line in TCL_machine_serial.get_recent_lines(count=20):
            if line in seen_lines:
                continue
            seen_lines.add(line)
            logo_dir = extract_logo_dir(line)
            if logo_dir:
                my_write_log(f"从串口日志抓取到开机动画路径: {line}")
                return logo_dir
    return ""


def get_bootanimation_path():
    # 断电上电 -> 35s后执行 进入uboot -> 发送 printenv tcl_logo_path抓取返回值
    # 抓取到 ZipFileName 后发送 ctrl+C 结束 logcat，只提取 bootanimation 的 zip 路径并返回
    # 获取不到类似数据则断电上电重试，最多重试3次，全部失败返回 False
    max_attempts = 3
    # 匹配 /tclconfig/ 开头的整段路径(字母/数字/下划线/点/横线/斜杠，1个或多个字符)
    # 例：printenv 返回 tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
    #     应提取整段 /tclconfig/.../logo，而不是只取 /tclconfig/ 后的单个字符
    zip_path_pattern = re.compile(r"/tclconfig/[A-Za-z0-9_.\-/]+")
    platform = config_get("platform")

    for attempt in range(1, max_attempts + 1):
        if platform == "RTK":  # RTK方案从串口冷开机启动获取 logcat |grep mZipFileName
            Relay_cmd("power off\r")
            time.sleep(2)
            Relay_cmd("power on\r")
            ok = enter_uboot_model()  # 进入uboot模式
            if ok:
                my_write_log("进入uboot模式成功")
            else:
                my_write_log("进入uboot模式失败")
            ok = exit_Pmode()
            if ok:
                my_write_log("退出Pmode模式成功")
            else:
                my_write_log("退出Pmode模式失败")
            # 发送 logcat 过滤命令(SerialManager 只有 send_data，没有 send / read_until)
            TCL_machine_serial.send_data("logcat |grep mZipFileName\r")
            my_write_log("已发送命令: logcat |grep mZipFileName，等待抓取 mZipFileName...")
            bootanim_zip = _wait_logo_dir_from_serial(timeout=10)

            # 发送 ctrl+C (hex 03) 结束 logcat
            TCL_machine_serial.send_hex_data("03")
            my_write_log("已发送 ctrl+C 结束 logcat")

            if bootanim_zip:
                # 例: mZipFileName:/tvcust/.../common_TCL_QDMINI_2K/bootanim/customized_bootanimation.zip
                #  -> /tclconfig/tvcust/.../common_TCL_QDMINI_2K(已统一为机型目录，可直接 pull)
                my_write_log(f"RTK 提取到的tcl_logo_path路径: {bootanim_zip}")
                Relay_cmd("power off\r")
                time.sleep(2)
                Relay_cmd("power on\r")
                ok = enter_uboot_model()  # 进入uboot模式
                if ok:
                    my_write_log("进入uboot模式成功")
                else:
                    my_write_log("进入uboot模式失败")
                ok = enter_Pmode()
                if ok:
                    my_write_log("进入Pmode模式成功")
                    return bootanim_zip
                else:
                    my_write_log("进入Pmode模式失败")
            my_write_log(f"第{attempt}/{max_attempts}次 RTK 未解析到开机动画路径或者未重新进入Pmode，2s后重试")
            time.sleep(2)
        else: # 非RTK的都从uboot获取 tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
            # 1. 断电上电
            ok = enter_uboot_model()  # 进入uboot模式
            if ok:
                my_write_log("进入uboot模式成功")
            else:
                my_write_log("进入uboot模式失败")
            my_write_log(f"第{attempt}/{max_attempts}次获取开机动画路径完成")
            if platform == "AML":
                # AML 用 logo_path_full，返回的是具体文件:
                #   logo_path_full=/tclconfig/.../common_TCL/logo/logo.jpg
                # 主流程再 dirname 一次即为 .../common_TCL/logo(要 pull 的目录)，
                # 因此这里必须保留文件名，不能提前去掉
                TCL_machine_serial.send_data("printenv logo_path_full\r")
                my_write_log("已发送命令: printenv logo_path_full，正在等待抓取返回值...")
                key_word = "logo_path_full="
            else:
                # MTK 及其它平台沿用 tcl_logo_path，返回的是目录:
                #   tcl_logo_path=/tclconfig/.../common_TCL/logo
                TCL_machine_serial.send_data("printenv tcl_logo_path\r")
                my_write_log("已发送命令: printenv tcl_logo_path，正在等待抓取返回值...")
                key_word = "tcl_logo_path="

            # 3. 轮询串口返回的新数据，按平台关键字提取 logo 路径
            bootanim_zip = ""
            seen_lines = set()  # 只处理本次命令之后新出现的行，避免误用缓存中的旧数据
            deadline = time.time() + 30
            while time.time() < deadline:
                time.sleep(1)
                for line in TCL_machine_serial.get_recent_lines(count=10):
                    if line in seen_lines:
                        continue
                    seen_lines.add(line)
                    if key_word in line:
                        match = zip_path_pattern.search(line)
                        if match:
                            # 统一剥到机型目录: MTK 的 .../logo 与 AML 的 .../logo/logo.jpg
                            # 都归一为 .../common_TCL
                            bootanim_zip = to_brand_dir(match.group(0))
                            my_write_log(f"抓取到返回值: {line}")
                            my_write_log(f"提取到的logo路径: {bootanim_zip}")
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
            if platform == "MTK":
                TCL_machine_serial.send_hex_data("0D")
                time.sleep(0.002)
            if platform == "RTK":
                TCL_machine_serial.send_hex_data("20")
                time.sleep(0.002)
            if platform == "AML":
                TCL_machine_serial.send_hex_data("03")
                time.sleep(0.002)

        time.sleep(10)
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        TCL_machine_serial.send_hex_data("0D")
        time.sleep(5)
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
    my_write_log("进入uboot模式失败")
    return False

def enter_Pmode():
    """
    进入enter_Pmode模式
    :return:
    :rtype:
    """
    for i in range(4):
        platform = config_get("platform")
        if platform == "RTK":
            TCL_machine_serial.send_data(f"pmode enable;re\r")
        if platform == "MTK":
            TCL_machine_serial.send_data(f"uuenv_init;uuenv_set Pmode 1;uuenv_save;res\r")
        if platform == "AML":
            TCL_machine_serial.send_data(f"setenv Pmode 1;saveenv;reboot\r")
        time.sleep(35)
        TCL_machine_serial.send_hex_data("0D")
        data = TCL_machine_serial.get_recent_lines(count=20)
        data_text = "\n".join(str(line) for line in data)
        my_write_log(f"data: {data_text}")
        # 串口出现 factory auto test loop start 说明 Pmode=1 已生效，工厂自动测试循环已启动
        if "factory auto test loop start" in data_text:
            my_write_log("进入Pmode模式成功")
            return True
        else:
            my_write_log("进入Pmode模式失败")
            enter_uboot_model()
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
    time.sleep(35)
    TCL_machine_serial.send_hex_data("0D")
    TCL_machine_serial.send_hex_data("0D")
    TCL_machine_serial.send_hex_data("0D")
    TCL_machine_serial.send_hex_data("0D")
    time.sleep(1)
    data = TCL_machine_serial.get_recent_lines(count=5)
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

# 查询PID应答中 85 数据帧的最小有效长度: 4字节 = 8个hex字符
# 例: 85 00 26 64 -> "85002664"(85 + 1字节数据"00" + 2字节CRC"2664")
# 比这个更短说明数据帧还没收齐，属于无效数据，不能参与解析
PID_ACK_MIN_HEX_LEN = 8


def parse_pid_ack(ack_hex):
    """
    从"查询PID"指令(AA 06 84 00 63 E1)的应答里解析设备当前 SID/HID。

    应答整体长度不固定(可能分多帧到达)，但数据帧固定以命令"85"开头，格式为:
        85 <数据(2个、4个或8个hex字符)> <CRC1 CRC2(共4个hex字符)>
    数据长度不固定，可能是1字节/2字节/4字节:
        - 2个hex字符(1字节): 即PID值本身，例 85002664 -> 数据"00"、CRC"2664"
        - 4个hex字符(2字节): 小端SID，例 0785 4e00 78d3 -> 数据"4e00" -> SID=78
        - 8个hex字符(4字节): 前2字节是SID(小端)，后2字节是HID

    最小有效数据长度: 从命令字节85开始(含85)至少4字节，即8个hex字符，
    例: 85 00 26 64 -> "85002664"(85 + 1字节数据 + 2字节CRC)；
    不足该长度视为数据未收齐，返回 (None, None)。
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

    # 2. 最小有效长度校验: 从命令字节85开始(含85)至少4字节 = 8个hex字符，
    #    例: "85002664"(85 + 1字节数据 + 2字节CRC)；更短说明数据帧还没收齐
    frame = hex_str[start - 2:]
    if len(frame) < PID_ACK_MIN_HEX_LEN:
        my_write_log(
            f"解析PID应答失败: 85数据帧长度不足(从85起至少需{PID_ACK_MIN_HEX_LEN}个hex字符"
            f"即4字节, 实际{len(frame)}个): {hex_str}"
        )
        return None, None

    # 3. 数据帧 = 数据(1/2/4字节) + 末尾2字节CRC
    tail = frame[2:]        # 去掉命令字节85
    data_hex = tail[:-4][:8]        # 去掉末尾2字节CRC，数据最多取4字节
    if len(data_hex) not in (2, 4, 8):
        my_write_log(f"解析PID应答失败: 85后数据长度异常({len(data_hex)}个hex字符): {hex_str}")
        return None, None

    # 4. 解析数据: 1字节即PID值本身；>=2字节时前2字节是小端SID，4字节时后2字节是HID
    if len(data_hex) == 2:
        sid = int(data_hex, 16)
        hid = None
    else:
        sid = (int(data_hex[2:4], 16) << 8) | int(data_hex[0:2], 16)
        hid = int(data_hex[4:8], 16) if len(data_hex) == 8 else None
    my_write_log(f"PID应答数据(hex): {data_hex} -> SID={sid}, HID={hid}")
    return sid, hid
	
def Step_1_set_0_PID():
    """
    当前在Pmdoe下，进行设置PID为0
    :return: 成功返回 True，失败返回 False
    :rtype: bool
    """
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

    time.sleep(10)

    # 加一个PID确认：应答长度不固定(85后的数据区可能4个或8个hex字符)，
    # 需要等含"85"数据帧的那部分收齐再解析，避免 int('',16) 报错
    ok = brush_key_crc("AA 06 84 00 63 E1", timeout=5, expect_hex_len=12)  # 查询PID指令
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
    if sid_machine == 0 and (hid_machine in (0, None)):
        my_write_log(f"切换PID为 0 成功")
        return True
    else:
        my_write_log(f"切换PID为 0 失败(设备回读SID: {sid_machine}，HID: {hid_machine})")
        return False

def Step_2_set_sid(sid):
    """
    当前在Pmdoe下，进行设置SID
    :param sid: SID
    :type sid: int
    :return: 成功返回 True，失败返回 False
    :rtype: bool
    """
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
    # 发生命令后等待10s
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
        return True
    else:
        my_write_log(f"切换SID为 {sid} 失败(设备回读SID: {sid_machine})")
        return False

def get_last_boot_image_path(tcl_logo_path):
    """
    获取开机动画最后一张照片、以及静态开机logo的本地路径。

    设备侧 tcl_logo_path 形如:
        /tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
    它对应的本地文件为:
        ./machine/<时间戳>/<机型目录>/bootanim/customized_bootanimation.zip  (开机动画压缩包)
        ./machine/<时间戳>/<机型目录>/logo/logo.jpg                         (静态开机logo)
    其中:
        - ./machine/<时间戳>/ 固定(machine 下每次 pull 生成的时间戳文件夹)
        - <机型目录> 从传入的 tcl_logo_path 中取(brand 后的第一段，如 common_TCL)
        - /bootanim/customized_bootanimation.zip 与 /logo/logo.jpg 固定

    解压 customized_bootanimation.zip 后，动画帧目录名可能变化
    (如 customized_bootanimation/part0/、boot/ 等)，所以取"图片数量最多"的目录作为帧目录，
    再按文件名中的数字排序取最后一张(如 000.png~086.png、loop_00001.png~loop_00111.png)。

    :param tcl_logo_path: 设备侧开机logo路径(可带 /logo 结尾，也可已是上一级目录)
    :return: (last_bootanimation_image_path, logo_image_path) 两个本地绝对路径，取不到的为 None
    :rtype: tuple
    """
    if not tcl_logo_path:
        my_write_log("获取开机动画图片失败: tcl_logo_path 为空")
        return None, None

    # 1. 从设备路径中解析出机型目录(如 common_TCL)
    parts = [p for p in str(tcl_logo_path).replace("\\", "/").split("/") if p]
    if not parts:
        my_write_log(f"获取开机动画图片失败: 无法从路径解析机型目录: {tcl_logo_path}")
        return None, None
    if "brand" in parts:
        # brand 后的一段就是机型目录(如 .../brand/common_TCL/logo -> common_TCL)
        brand_index = parts.index("brand")
        model_dir = parts[brand_index + 1] if brand_index + 1 < len(parts) else parts[-1]
    else:
        # 没有 brand 段时，末尾若是 logo 则取上一级
        model_dir = parts[-1]
        if model_dir.lower() == "logo" and len(parts) > 1:
            model_dir = parts[-2]
    my_write_log(f"从 tcl_logo_path 解析出的机型目录: {model_dir}")

    # 2. 定位本批次机型目录: machine/<时间戳>/<机型目录>/
    #    时间戳目录名格式固定(YYYYmmdd_HHMMSS)，按目录名倒序取最新的一批
    machine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "machine")
    model_path = None
    if os.path.isdir(machine_dir):
        time_dirs = sorted((d for d in os.listdir(machine_dir)
                            if os.path.isdir(os.path.join(machine_dir, d))), reverse=True)
        # 2.1 优先精确匹配机型目录
        for d in time_dirs:
            if os.path.isdir(os.path.join(machine_dir, d, model_dir)):
                model_path = os.path.join(machine_dir, d, model_dir)
                break
        # 2.2 兜底: 前缀匹配(如 common_TCL_LED_4K 用 common_TCL 也能命中)
        if not model_path:
            for d in time_dirs:
                sub_dirs = sorted(s for s in os.listdir(os.path.join(machine_dir, d))
                                  if os.path.isdir(os.path.join(machine_dir, d, s))
                                  and s.startswith(model_dir))
                if sub_dirs:
                    model_path = os.path.join(machine_dir, d, sub_dirs[0])
                    break
    if not model_path:
        my_write_log(f"未找到本批次机型目录: machine/<时间戳>/{model_dir}")
        return None, None
    my_write_log(f"本批次机型目录: {model_path}")

    # 3. 静态开机logo: <机型目录>/logo/logo.jpg
    logo_dir = os.path.join(model_path, "logo")
    logo_image_path = os.path.join(logo_dir, "logo.jpg")
    if os.path.isfile(logo_image_path):
        my_write_log(f"静态开机logo: {logo_image_path}")
    else:
        # 兜底: 取 logo 目录下第一张图片(排除 pmode 专用logo)
        logo_image_path = None
        if os.path.isdir(logo_dir):
            for f in sorted(os.listdir(logo_dir)):
                if (f.lower().endswith((".jpg", ".jpeg", ".png"))
                        and "pmode" not in f.lower()
                        and os.path.isfile(os.path.join(logo_dir, f))):
                    logo_image_path = os.path.join(logo_dir, f)
                    break
        if logo_image_path:
            my_write_log(f"未找到 logo.jpg，改用: {logo_image_path}")
        else:
            my_write_log(f"未找到静态开机logo: {logo_dir}")

    # 4. 解压开机动画压缩包(先清掉旧的解压结果，避免残留旧图片)
    zip_name = "customized_bootanimation.zip"
    zip_path = os.path.join(model_path, "bootanim", zip_name)
    if not os.path.isfile(zip_path):
        my_write_log(f"未找到开机动画压缩包: {zip_path}")
        return None, logo_image_path
    my_write_log(f"找到开机动画压缩包: {zip_path}")

    unzip_dir = os.path.join(os.path.dirname(zip_path), os.path.splitext(zip_name)[0])
    if os.path.exists(unzip_dir):
        shutil.rmtree(unzip_dir)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_file:
            zip_file.extractall(unzip_dir)
    except Exception as e:
        my_write_log(f"解压开机动画压缩包失败: {zip_path} -> {e}")
        return None, logo_image_path
    my_write_log(f"解压开机动画压缩包完成: {unzip_dir}")

    # 5. 帧目录名可能变化(part0 / boot / customized_bootanimation/xxx)，
    #    取"图片数量最多"的目录作为动画帧目录
    image_exts = (".png", ".jpg", ".jpeg", ".webp")
    dir_images = {}
    for root, dirs, files in os.walk(unzip_dir):
        imgs = [f for f in files if f.lower().endswith(image_exts)]
        if imgs:
            dir_images[root] = imgs
    if not dir_images:
        my_write_log(f"解压后未找到任何图片: {unzip_dir}")
        return None, logo_image_path
    frame_dir = max(dir_images, key=lambda d: len(dir_images[d]))
    my_write_log(f"开机动画帧目录: {frame_dir} (共{len(dir_images[frame_dir])}张)")

    # 6. 按文件名中的数字排序，取最后一张(数值最大)
    def _sort_key(name):
        """优先按文件名中的数字排序(000.png -> 0)，无数字时按名称兜底"""
        match = re.search(r"(\d+)", name)
        if match:
            return 0, int(match.group(1)), name
        return 1, 0, name

    image_names = sorted(dir_images[frame_dir], key=_sort_key)
    last_bootanimation_image_path = os.path.join(frame_dir, image_names[-1])
    my_write_log(f"开机动画最后一张图片: {last_bootanimation_image_path}")
    return logo_image_path, last_bootanimation_image_path


def template_SID_LOGO(sid):
    """
    根据当前测试的 SID，从 ./template/SID_<sid>_<机型名>/ 目录下获取模板图片的本地路径:
        ./template/SID_100_C7K/开机logo.png   (静态开机logo标准图)
        ./template/SID_100_C7K/开机动画.png   (开机动画末帧标准图)

    :param sid: 当前测试的 SID 值(可传 int 或 str，如 100 / "100")
    :return: (template_logo_image_path, template_bootanimation_image_path) 两个绝对路径，取不到的为 None
    :rtype: tuple
    """
    if sid is None or str(sid).strip() == "":
        my_write_log("获取 template 模板图片失败: SID 为空")
        return None, None
    sid = str(sid).strip()

    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
    if not os.path.isdir(template_dir):
        my_write_log(f"未找到 template 目录: {template_dir}")
        return None, None

    # 1. 匹配 SID_<sid>_<机型名> 目录(必须带下划线，避免 SID_10 误匹配到 SID_100)
    sid_dirs = sorted(d for d in os.listdir(template_dir)
                      if os.path.isdir(os.path.join(template_dir, d))
                      and (d.upper() == f"SID_{sid}" or d.upper().startswith(f"SID_{sid}_")))
    if not sid_dirs:
        my_write_log(f"未找到 SID={sid} 对应的 template 目录: {template_dir}\\SID_{sid}_<机型名>")
        return None, None
    if len(sid_dirs) > 1:
        my_write_log(f"SID={sid} 匹配到多个 template 目录: {sid_dirs}，取第一个")
    template_sid_dir = os.path.join(template_dir, sid_dirs[0])
    my_write_log(f"SID={sid} 对应的 template 目录: {template_sid_dir}")

    # 2. 在目录内取"开机logo.png"与"开机动画.png"(精确名优先，失败则按关键字兜底)
    def _find_template_image(keyword, default_name):
        exact_path = os.path.join(template_sid_dir, default_name)
        if os.path.isfile(exact_path):
            return exact_path
        for f in sorted(os.listdir(template_sid_dir)):
            if (keyword in f
                    and f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                    and os.path.isfile(os.path.join(template_sid_dir, f))):
                return os.path.join(template_sid_dir, f)
        return None

    template_logo_image_path = _find_template_image("开机logo", "开机logo.png")
    template_bootanimation_image_path = _find_template_image("开机动画", "开机动画.png")

    if template_logo_image_path:
        my_write_log(f"template 开机logo图片: {template_logo_image_path}")
    else:
        my_write_log(f"template 目录下未找到 开机logo.png: {template_sid_dir}")
    if template_bootanimation_image_path:
        my_write_log(f"template 开机动画图片: {template_bootanimation_image_path}")
    else:
        my_write_log(f"template 目录下未找到 开机动画.png: {template_sid_dir}")

    return template_logo_image_path, template_bootanimation_image_path


def _fmt_metric(value):
    """把相似度/阈值格式化成便于阅读的字符串，取不到时返回 N/A。"""
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return "N/A"


def compare_one_image(machine_image_path, template_image_path, item_name):
    """
    对比单张图片（机器图 vs 标准库图），异常或图片缺失时只记录失败原因，不影响后续 SID 测试。

    :param machine_image_path: 机器上拉取到的图片路径
    :param template_image_path: 标准库 template 目录下的图片路径
    :param item_name: 项目名称（如 "开机logo" / "开机动画"），用于拼接失败原因
    :return: (对比结果 dict 或 None, 失败原因 str，成功时为空字符串)
    :rtype: tuple
    """
    if not machine_image_path or not template_image_path:
        return None, f"{item_name}图片缺失(机器图片:{machine_image_path}, 标准库图片:{template_image_path})"
    try:
        return compare_logo_images(machine_image_path, template_image_path), ""
    except Exception as e:
        return None, f"{item_name}对比异常: {e}"


def build_sid_result(sid, logo_result, logo_error, logo_image_path, template_logo_image_path,
                     boot_result, boot_error, boot_image_path, template_boot_image_path):
    """
    汇总单个 SID 的对比结果，供 sid_result_list / fail_list 使用。

    :return: 结果字典，含 logo、bootanimation 明细以及 fail_reasons（失败原因列表）、matched（是否全部通过）
    :rtype: dict
    """
    def _item(result, error, machine_image, template_image):
        return {
            "matched": bool(result.get("matched")) if result else False,
            "score": result.get("score") if result else None,
            "direct_match_threshold": result.get("direct_match_threshold") if result else None,
            "fallback_threshold": result.get("fallback_threshold") if result else None,
            "decision_source": result.get("decision_source") if result else None,
            "llm_status": result.get("llm_status") if result else None,
            "machine_image": machine_image,
            "template_image": template_image,
            "error": error,
        }

    logo_item = _item(logo_result, logo_error, logo_image_path, template_logo_image_path)
    boot_item = _item(boot_result, boot_error, boot_image_path, template_boot_image_path)

    # 两项判定阈值（两张图用的是同一份配置，取任一可用的对比结果即可）
    threshold_source = logo_result or boot_result or {}
    thresholds = {
        "direct_match_threshold": threshold_source.get("direct_match_threshold"),
        "fallback_threshold": threshold_source.get("fallback_threshold"),
    }
    threshold_text = "直判{0}/降级{1}".format(
        _fmt_metric(thresholds["direct_match_threshold"]),
        _fmt_metric(thresholds["fallback_threshold"]),
    )

    fail_reasons = []
    for item_name, item in (("开机logo", logo_item), ("开机动画", boot_item)):
        if item["error"]:
            fail_reasons.append(item["error"])
        elif not item["matched"]:
            fail_reasons.append(
                "{0}对比失败(相似度{1}, 判定来源{2}, 阈值[{3}])".format(
                    item_name,
                    _fmt_metric(item["score"]),
                    item["decision_source"],
                    threshold_text,
                )
            )

    return {
        "sid": sid,
        "matched": logo_item["matched"] and boot_item["matched"],
        "logo": logo_item,
        "bootanimation": boot_item,
        "thresholds": thresholds,
        "threshold_text": threshold_text,
        "fail_reasons": fail_reasons,
    }


def _find_sid_model_name(sid):
    """
    按 template 目录下的文件夹名取 "SID_<sid>_<机型名>"，例如 SID_56_P8K。

    :param sid: SID 值
    :return: 目录名；找不到对应目录时返回 "SID_<sid>"
    :rtype: str
    """
    default_name = f"SID_{sid}"
    if sid is None or str(sid).strip() == "":
        return default_name

    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template")
    if not os.path.isdir(template_dir):
        return default_name

    sid_str = str(sid).strip()
    try:
        matched = sorted(
            d for d in os.listdir(template_dir)
            if os.path.isdir(os.path.join(template_dir, d))
            and (d.upper() == f"SID_{sid_str}" or d.upper().startswith(f"SID_{sid_str}_"))
        )
    except OSError:
        return default_name
    return matched[0] if matched else default_name


def export_sid_result_table(sid_result_list, output_dir=None, image_height=80):
    """
    把 SID 开机logo对比结果导出成 Excel 表格，并把对应图片插入单元格。

    表格列：
        SID_机型 | 标准库开机logo | 标准库开机动画 | 机器开机logo | 机器开机动画 |
        判断结果 | 判定结果源

    :param sid_result_list: build_sid_result 产出的结果列表（含图片路径与判定来源）
    :param output_dir: 输出目录，None 时写到脚本目录下的 report 目录
    :param image_height: 单元格内图片高度(像素)，宽度按原图比例自动缩放
    :return: 生成的 xlsx 绝对路径；导出失败返回 None
    :rtype: str | None
    """
    try:
        from openpyxl import Workbook
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        my_write_log(f"导出 Excel 表格失败，缺少依赖: {e}（可执行 pip install openpyxl）")
        return None

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report")

    headers = ["SID_机型", "标准库开机logo", "标准库开机动画", "机器开机logo",
               "机器开机动画", "判断结果", "判定结果源"]

    wb = Workbook()
    ws = wb.active
    ws.title = "SID对比结果"
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 24

    image_widths = [0] * len(headers)

    for row_index, item in enumerate(sid_result_list, start=2):
        logo_info = item.get("logo", {})
        boot_info = item.get("bootanimation", {})
        decision_source = "开机logo={0}；开机动画={1}".format(
            logo_info.get("decision_source"), boot_info.get("decision_source")
        )
        ws.append([
            _find_sid_model_name(item.get("sid")),
            None, None, None, None,
            "OK" if item.get("matched") else "NG",
            decision_source,
        ])

        # 四张图片：标准库开机logo / 标准库开机动画 / 机器开机logo / 机器开机动画
        image_columns = (
            (2, logo_info.get("template_image")),
            (3, boot_info.get("template_image")),
            (4, logo_info.get("machine_image")),
            (5, boot_info.get("machine_image")),
        )
        for column, image_path in image_columns:
            cell = ws.cell(row=row_index, column=column)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if image_path and os.path.isfile(image_path):
                try:
                    with Image.open(image_path) as img:
                        origin_width, origin_height = img.size
                    ratio = image_height / float(origin_height) if origin_height else 1.0
                    xl_image = XLImage(image_path)
                    xl_image.height = int(image_height)
                    xl_image.width = int(origin_width * ratio)
                    # 按单元格位置插入，图片按 image_height 等比缩放，
                    # 不随单元格拉伸(twoCell 锚定会把图片撑到超出单元格)
                    ws.add_image(xl_image, cell.coordinate)
                    image_widths[column - 1] = max(image_widths[column - 1], xl_image.width)
                    continue
                except Exception as e:  # 图片坏了不影响整张表
                    cell.value = f"图片插入失败: {e}"
            else:
                cell.value = "图片缺失" if not image_path else f"图片不存在: {image_path}"

        ws.row_dimensions[row_index].height = image_height * 0.8
        for column in (1, 6, 7):
            ws.cell(row=row_index, column=column).alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )

    # 列宽：图片列按图片像素宽换算，其余给固定宽度
    ws.column_dimensions[get_column_letter(1)].width = 22
    for column in range(2, 6):
        ws.column_dimensions[get_column_letter(column)].width = max(
            12, image_widths[column - 1] / 7.0
        )
    ws.column_dimensions[get_column_letter(6)].width = 10
    ws.column_dimensions[get_column_letter(7)].width = 34
    ws.freeze_panes = "A2"

    os.makedirs(output_dir, exist_ok=True)
    file_name = "SID开机logo对比结果_%s.xlsx" % datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = os.path.join(output_dir, file_name)
    try:
        wb.save(file_path)
    except Exception as e:
        my_write_log(f"保存 Excel 表格失败: {e}")
        return None

    my_write_log(f"SID对比结果表格已导出: {file_path}")
    return file_path

    
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
        Report_Handler.printh(f"SID的列表sid_list: {sid_list}")

        # 第一步：切换PID为0
        ok = enter_uboot_model()  # 进入uboot模式
        if ok:
            my_write_log("进入uboot模式成功")
            ok_pmode = enter_Pmode()
            if ok_pmode:
                my_write_log("进入Pmode模式成功")
            else:
                my_write_log("进入Pmode模式失败，重试一次")
        else:
            my_write_log("进入uboot模式失败，重试一次")
            enter_uboot_model()  # 进入uboot模式

        if Step_1_set_0_PID():
            my_write_log("设置PID为0成功")
        else:
            my_write_log("设置PID为0失败，重试一次")
            Step_1_set_0_PID()
		
        Relay_cmd("power off\r")
        time.sleep(2)
        Relay_cmd("power on\r")
        time.sleep(25)  # 等待25秒，确保设备启动完成
        # sid_result_list：保存每个 SID 的对比结果明细；fail_list：只保存对比失败的 SID
        sid_result_list = []
        fail_list = []
# 开始遍历所有SID列表
        for sid in sid_list:
            my_write_log(f"当前测试SID: {sid}")
            Report_Handler.printh(f"当前测试SID: {sid}")
            # 第二步：切换SID为 当前测试的sid，并且查询SID是否切换成功

            ok = Step_2_set_sid(sid)
            if ok:
                my_write_log(f"设置SID {sid} 成功")
            else:
                my_write_log(f"设置SID {sid} 失败，再次设置")
                Step_2_set_sid(sid)
            
            Relay_cmd("power off\r")
            time.sleep(2)
            Relay_cmd("power on\r")
            time.sleep(25)  # 等待25秒，确保设备启动完成并且生效SID
            my_write_log(f"切换SID为 {sid} 完成，等待设备启动完成")

            # 第三步：开机logo：Uboot输入printenv tcl_logo_path，并且adb pull /tclconfig 到本地 machine 文件夹中
            my_write_log(f"uboot下获取开机logo的地址")
            tcl_logo_path = get_bootanimation_path()
            my_write_log(f"tcl_logo_path: {tcl_logo_path}")   # tcl_logo_path=/tclconfig/tvcust/aio_resources/resource/brand/common_TCL/logo
            if platform != "RTK":  # RTK是从adb获取路径，所以不用重启
                Relay_cmd("power off\r")
                time.sleep(2)
                Relay_cmd("power on\r")
                time.sleep(35)  # 等待35秒，确保设备启动完成
            # get_bootanimation_path 已统一返回机型目录(如 /tclconfig/.../common_TCL)，
            # 不再需要 os.path.dirname(三个机芯的返回层级差异已在函数内抹平)
            ok = adb_connect_and_pull_tclconfig(tcl_logo_path)
            if ok:
                my_write_log(f"拉取 {tcl_logo_path} 完成")
            else:
                my_write_log(f"拉取 {tcl_logo_path} 失败，再次尝试拉取 {tcl_logo_path}")
                adb_connect_and_pull_tclconfig(tcl_logo_path)

            # 第四步：解压tcl_logo_path的压缩包的customized_bootanimation.zip，并且拿到最后一张开机动画的图片
            
            my_write_log(f"解压tcl_logo_path的压缩包的customized_bootanimation.zip，并且拿到最后一张开机动画的图片")
            logo_image_path, last_bootanimation_image_path = get_last_boot_image_path(tcl_logo_path)
            my_write_log(f"开机动画的图片last_bootanimation_image_path: {last_bootanimation_image_path}")
            my_write_log(f"静态logo图片logo_image_path: {logo_image_path}")

            # 第五步：根据当前测试的SID从 ./template/SID_X_机型名 的文件夹中获取  开机logo.png  和   开机动画.png  2个图片的地址
            template_logo_image_path, template_bootanimation_image_path = template_SID_LOGO(sid)
            my_write_log(f"标准库开机动画图片template_bootanimation_image_path: {template_bootanimation_image_path}")
            my_write_log(f"标准库静态logo图片template_logo_image_path: {template_logo_image_path}")

            # 第六步：对比静态logo和开机动画；每个 SID 的结果存入 sid_result_list，对比失败的 SID 存入 fail_list
            compare_bool_1, logo_error = compare_one_image(logo_image_path, template_logo_image_path, "开机logo")
            compare_bool_2, boot_error = compare_one_image(last_bootanimation_image_path, template_bootanimation_image_path, "开机动画")
            sid_result = build_sid_result(
                sid,
                compare_bool_1, logo_error, logo_image_path, template_logo_image_path,
                compare_bool_2, boot_error, last_bootanimation_image_path, template_bootanimation_image_path,
            )
            sid_result_list.append(sid_result)
            if not sid_result["matched"]:
                fail_list.append(sid_result)

            my_write_log("开机logo对比结果")
            my_write_log(str(compare_bool_1))
            my_write_log("开机动画对比结果")
            my_write_log(str(compare_bool_2))
            if sid_result["matched"]:
                my_write_log(f"{sid}对比成功")
                Report_Handler.printh(f"SID={sid}的开机logo对比成功",result="OK")
                Report_Handler.printh(f"标准库开机logo图片地址: {template_logo_image_path}", image_path=template_logo_image_path, image_alt="标准库开机logo图片")
                Report_Handler.printh(f"机器的开机logo图片地址: {logo_image_path}", image_path=logo_image_path, image_alt="机器的开机logo图片")
                Report_Handler.printh(f"SID={sid}的开机动画对比成功",result="OK")
                Report_Handler.printh(f"标准库开机动画图片地址: {template_bootanimation_image_path}", image_path=template_bootanimation_image_path, image_alt="标准库开机动画图片")
                Report_Handler.printh(f"机器的开机动画图片地址: {last_bootanimation_image_path}", image_path=last_bootanimation_image_path, image_alt="机器的开机动画图片")
            else:
                my_write_log(f"{sid}对比失败")
                # 每遍历完一个 SID，立即输出该 SID 的失败结果
                for reason in sid_result["fail_reasons"]:
                    my_write_log(f"【失败结果】SID={sid}：{reason}")
                    Report_Handler.printh(f"【失败结果】SID={sid}：{reason}", result="NG")
                Report_Handler.printh(f"SID={sid}的开机logo对比失败",result="NG")
                Report_Handler.printh(f"标准库开机logo图片地址: {template_logo_image_path}", image_path=template_logo_image_path, image_alt="标准库开机logo图片")
                Report_Handler.printh(f"机器的开机logo图片地址: {logo_image_path}", image_path=logo_image_path, image_alt="机器的开机logo图片")
                Report_Handler.printh(f"SID={sid}的开机动画对比失败",result="NG")
                Report_Handler.printh(f"标准库开机动画图片地址: {template_bootanimation_image_path}", image_path=template_bootanimation_image_path, image_alt="标准库开机动画图片")
                Report_Handler.printh(f"机器的开机动画图片地址: {last_bootanimation_image_path}", image_path=last_bootanimation_image_path, image_alt="机器的开机动画图片")

            # 每遍历完一个 SID 输出该 SID 的对比结果（失败时同时输出失败清单）
            my_write_log(
                f"SID={sid} 对比完成：{'通过' if sid_result['matched'] else '失败'}；"
                f"阈值[{sid_result['threshold_text']}]；"
                f"开机logo={sid_result['logo']['matched']}"
                f"(相似度{_fmt_metric(sid_result['logo']['score'])}，判定来源{sid_result['logo']['decision_source']})，"
                f"开机动画={sid_result['bootanimation']['matched']}"
                f"(相似度{_fmt_metric(sid_result['bootanimation']['score'])}，判定来源{sid_result['bootanimation']['decision_source']})"
            )
            if not sid_result["matched"]:
                sid_fail_text = "；".join(sid_result["fail_reasons"])
                my_write_log(f"【失败结果】SID={sid}：{sid_fail_text}")
                Report_Handler.printh(f"【失败结果】SID={sid}：{sid_fail_text}", result="NG")

            my_write_log(f"========================开始下一个SID测试========================")
            Report_Handler.printh(f"========================开始下一个SID测试========================")

        # 所有 SID 遍历完成：输出汇总与失败清单
        my_write_log(f"共测试 {len(sid_result_list)} 个SID，对比通过 {len(sid_result_list) - len(fail_list)} 个，失败 {len(fail_list)} 个")
        Report_Handler.printh(f"共测试 {len(sid_result_list)} 个SID，对比通过 {len(sid_result_list) - len(fail_list)} 个，失败 {len(fail_list)} 个")
        if fail_list:
            for item in fail_list:
                fail_text = f"SID={item['sid']}：" + "；".join(item["fail_reasons"])
                my_write_log(f"【失败结果】{fail_text}")
                Report_Handler.printh(f"【失败结果】{fail_text}", result="NG")
            desc_text = "失败SID：" + "、".join(str(item["sid"]) for item in fail_list)
        else:
            desc_text = f"共{len(sid_result_list)}个SID的开机logo与开机动画对比全部通过"

        # 明细结果（含相似度、阈值、判定来源）写入日志，便于事后分析
        my_write_log("所有SID对比明细: " + json.dumps(sid_result_list, ensure_ascii=False))

        # 导出 Excel 结果表格（每个 SID 一行，四张图片插入单元格）
        result_table_path = export_sid_result_table(sid_result_list)
        if result_table_path:
            Report_Handler.printh(f"SID对比结果表格已导出: {result_table_path}")
        else:
            my_write_log("SID对比结果表格导出失败")

        # 输出测试结果：只要有 SID 对比失败，用例结论即为 NG
        Report_Handler.test_out(
                case_num="TCL开机logo和开机动画对比",
                case_name="TCL开机logo和开机动画对比",
                ok_ng="NG" if fail_list else "OK",
                case_type="TCL开机logo和开机动画对比",
                desc=desc_text
            )
        response1 = TCL_machine_serial.receive_data(timeout=0.5)

    except Exception as e:
        my_write_log(f"发生错误: {str(e)}")
    finally:
        # 停止日志进程
        TCL_machine_serial.stop_logging()

