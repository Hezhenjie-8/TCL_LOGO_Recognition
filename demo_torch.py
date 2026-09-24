import torch
import torchvision.transforms as transforms
from torchvision import models
from datetime import datetime
import serial
import time
import os
import cv2
import random
from PIL import Image
import socket
import requests
from PythonPackage import Relay_library
import detect_vertical_lines
Relay_open = [0,Relay_library.K1_open_cmd, Relay_library.K2_open_cmd, Relay_library.K3_open_cmd,
                  Relay_library.K4_open_cmd, Relay_library.K5_open_cmd, Relay_library.K6_open_cmd,
                  Relay_library.K7_open_cmd, Relay_library.K8_open_cmd, Relay_library.K9_open_cmd,
                  Relay_library.K10_open_cmd, Relay_library.K11_open_cmd, Relay_library.K12_open_cmd,
                  Relay_library.K13_open_cmd, Relay_library.K14_open_cmd, Relay_library.K15_open_cmd,
                  Relay_library.K16_open_cmd]
Relay_close = [0,Relay_library.K1_close_cmd, Relay_library.K2_close_cmd, Relay_library.K3_close_cmd,
                   Relay_library.K4_close_cmd, Relay_library.K5_close_cmd, Relay_library.K6_close_cmd,
                   Relay_library.K7_close_cmd, Relay_library.K8_close_cmd, Relay_library.K9_close_cmd,
                   Relay_library.K10_close_cmd, Relay_library.K11_close_cmd, Relay_library.K12_close_cmd,
                   Relay_library.K13_close_cmd, Relay_library.K14_close_cmd, Relay_library.K15_close_cmd,
                   Relay_library.K16_close_cmd]

log_date_time = datetime.now().strftime('%Y%m%d_%H%M%S_')
pc_name = socket.gethostname()
# url = 'https://open.feishu.cn/open-apis/bot/v2/hook/03fff426-b5ca-4a98-99b6-1318739b46fc'   # OA上机器人的群

# 继电器的函数
def Relay_cmd(open_or_close_cmd):
    retries = 5
    try:
        ser_Relay = serial.Serial("COM10", 9600, timeout=5)  # 继电器的COM
        ser_Relay.write(open_or_close_cmd)
        time.sleep(0.2)
        len_return_data = ser_Relay.inWaiting()
        return_data = ser_Relay.read(len_return_data)
        my_write_log(str(return_data))
        ser_Relay.close()
        return return_data
    except Exception as e:
        if retries <= 0:
            raise e  # 重试次数用完，抛出异常
        retries -= 1
        print(f"Error: {e}. Retrying in 3 seconds...")
        time.sleep(3)  # 等待3秒后重试
        ser_Relay = serial.Serial("COM6", 9600, timeout=5)  # 继电器的COM
        ser_Relay.write(open_or_close_cmd)
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

def take_fixed_resolution_photos(save_path, num_photos=10, resolution=(1920, 1080), delay_sec=1,
                                 max_retries=3, brightness=None):
    """
    改进版拍摄固定分辨率照片函数，增加亮度调节功能
    :param save_path: 保存路径
    :param num_photos: 拍摄数量
    :param resolution: 照片分辨率 (width, height)
    :param delay_sec: 拍摄间隔(秒)
    :param max_retries: 最大重试次数
    :param brightness: 亮度值 (None表示自动，范围通常为0-100或0-255)
    """
    # 确保保存路径存在
    os.makedirs(save_path, exist_ok=True)

    cap = None
    retries = 0

    while retries < max_retries:
        try:
            cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)

            # 设置分辨率
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])

            # 设置亮度（如果参数不为None）
            if brightness is not None:
                cap.set(cv2.CAP_PROP_BRIGHTNESS, brightness)

            if not cap.isOpened():
                raise RuntimeError(f"第 {retries + 1} 次尝试打开摄像头失败")

            # 获取实际设置
            actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_brightness = cap.get(cv2.CAP_PROP_BRIGHTNESS)

            my_write_log(f"摄像头实际分辨率: {actual_width}x{actual_height}")
            if brightness is not None:
                my_write_log(f"尝试设置亮度: {brightness}, 实际亮度值: {actual_brightness}")

            break

        except Exception as e:
            retries += 1
            my_write_log(f"摄像头初始化失败，尝试 {retries}/{max_retries}: {str(e)}")
            if cap is not None:
                cap.release()
            time.sleep(2)

    if retries == max_retries:
        raise RuntimeError(f"无法打开摄像头，已尝试 {max_retries} 次")

    try:
        for i in range(num_photos):
            ret, frame = cap.read()
            if not ret:
                raise RuntimeError(f"无法获取第 {i + 1} 张照片的画面")

            if (frame.shape[1], frame.shape[0]) != resolution:
                frame = cv2.resize(frame, resolution)

            filename = f"photo_{i + 1}.jpg"
            filepath = os.path.join(save_path, filename)

            if not cv2.imwrite(filepath, frame):
                raise RuntimeError(f"无法保存图片: {filepath}")

            my_write_log(f"拍照图片已保存: {filepath}")

            if i < num_photos - 1:
                time.sleep(delay_sec)

    finally:
        if cap is not None:
            cap.release()
        print("摄像头资源已释放")

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

def fixed_crop(image_path, output_path, left, top, right, bottom, output_size=None):
    """
    固定位置裁剪图片并统一输出尺寸
    :param image_path: 输入图片路径
    :param output_path: 输出裁剪图片路径
    :param left: 裁剪区域左坐标
    :param top: 裁剪区域上坐标
    :param right: 裁剪区域右坐标
    :param bottom: 裁剪区域下坐标
    :param output_size: 可选参数，指定输出尺寸 (width, height)
    """

    with Image.open(image_path) as img:
        # 固定位置裁剪
        cropped = img.crop((left, top, right, bottom))

        # 调整到统一尺寸（如果需要）
        if output_size:
            cropped = cropped.resize(output_size, Image.Resampling.LANCZOS)

        cropped.save(output_path)

def photos_similarity(image_path, output_path, template_path, left, top, right, bottom, output_size=None,
                      SSIM_template=100):
    """
        固定位置裁剪图片并统一输出尺寸，并且完成图像识别
        :param image_path: 输入图片路径
        :param output_path: 输出裁剪图片路径
        :param template_path: 模版识别图片路径
        :param left: 裁剪区域左坐标
        :param top: 裁剪区域上坐标
        :param right: 裁剪区域右坐标
        :param bottom: 裁剪区域下坐标
        :param output_size: 可选参数，指定输出尺寸 (width, height)
        :param SSIM_template: 模版识别图片相似度的阈值
        """
    fixed_crop(
        image_path=image_path,
        output_path=output_path,
        left=left,
        top=top,
        right=right,
        bottom=bottom  # 示例：从图片的(100,50)到(400,300)区域裁剪
    )
    similarity = Torchstart(output_path, template_path)
    my_write_log(f"图片的SSIM相似度: {similarity:.2f}")
    result = detect_vertical_lines.compare_two_images(output_path, template_path)
    if similarity > SSIM_template and result:
        my_write_log(f"图像配对识别成功")
        return True
    else:
        my_write_log(f"图像配对识别失败")
        return False

def sendRebotMsg(api, text):
    msg = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }
    rsp = requests.post(api, json=msg)
    if rsp.json()["code"] == 0:
        print("飞书机器人消息发送成功")
    else:
        print("飞书机器人消息发送失败")

def machine_1_6_compare():
    machine_bool_1 = True
    machine_bool_2 = True
    machine_bool_3 = True
    machine_bool_4 = True
    machine_bool_5 = True
    machine_bool_6 = True
    # # 设置保存路径（修改为你需要的绝对路径）
    # base_save_path = os.path.dirname(os.path.abspath(__file__)) + "\\picture"  # 获取当前文件的绝对路径
    # # 1. 创建文件夹，用于保存拍照的图片
    # folder_path = create_timestamped_folder(base_save_path)
    # my_write_log(f"已创建文件夹: {folder_path}")

    # # 2. 拍摄固定分辨率的照片
    # take_fixed_resolution_photos(
    #     folder_path,
    #     num_photos=10,  # 拍多少张照片
    #     resolution=(1920, 1080),  # 设置为你需要的分辨率
    #     delay_sec=0.5,  # 拍摄间隔(秒)
    #     max_retries=5,  # 最大重试次数
    #     brightness=None  # 亮度值
    # )
    # my_write_log("所有照片拍摄完成！")

    # 创建一个文件夹保存裁剪后的图片
    # compare_photos_output_path_one = folder_path + "\\CMS003_1#"
    # os.makedirs(compare_photos_output_path_one, exist_ok=True)
    # 进行图像裁剪和识别
    for i in range(1, 9):
        compare_photos_input_path = os.path.dirname(os.path.abspath(__file__)) + "\\photo_" + str(i) + ".jpg"
        compare_photos_output_one_photos = os.path.dirname(os.path.abspath(__file__)) + "\\compare" + str(i) + ".jpg"
        template_photo_path_one = os.path.dirname(os.path.abspath(__file__)) + "\\template.jpg"
        similarity_bool = photos_similarity(
            image_path=compare_photos_input_path,  # 输入图片路径
            output_path=compare_photos_output_one_photos,  # 输出裁剪图片路径
            template_path=template_photo_path_one,  # 模版识别图片路径
            left=1205,  # 裁剪区域左坐标
            top=635,  # 裁剪区域上坐标
            right=1275,  # 裁剪区域右坐标
            bottom=715,  # 裁剪区域下坐标
            SSIM_template=90  # 模版识别图片相似度的阈值
        )
        if similarity_bool == True:
            my_write_log(f"识别成功，CMS003_3#第{i}张图片识别成功")
        else:
            my_write_log(f"识别失败，CMS003_3#第{i}张图片识别失败")
            machine_bool_1 = False
    return machine_bool_1


def fast_capture_batch(cap, num_photos, save_path, resolution, delay_sec=0):
    """
    批量处理版本 - 最高速度，但占用更多内存
    """
    frames = []
    my_write_log(f"开始缓存帧")
    # 第一阶段：快速读取所有帧
    for i in range(num_photos):
        ret, frame = cap.read()
        if not ret:
            raise RuntimeError(f"无法获取第 {i + 1} 张照片的画面")

        # 快速resize
        if (frame.shape[1], frame.shape[0]) != resolution:
            frame = cv2.resize(frame, resolution, interpolation=cv2.INTER_NEAREST)

        frames.append(frame)

        if i < num_photos - 1 and delay_sec > 0:
            time.sleep(delay_sec)
    my_write_log(f"缓存结束")

    # 第二阶段：批量保存（利用多线程）
    def save_frame(i, frame):
        filename = os.path.join(save_path, f"photo_{i + 1}.jpg")
        cv2.imwrite(filename, frame)
        my_write_log(f"拍照图片已保存: {filename}")

    # 使用线程池并行保存
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(save_frame, i, frame) for i, frame in enumerate(frames)]
        for future in concurrent.futures.as_completed(futures):
            future.result()  # 检查是否有异常


if __name__ == "__main__":
    test_num = 0
    compare_bool = False
    date_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    date_time = str(date_time)
    tcl_machine_ser = serial.Serial("COM5", 115200, timeout=5)  # TCL机器的COM

    
    # output_path = os.path.dirname(os.path.abspath(__file__)) + "\\compare1.jpg"
    # template_path = os.path.dirname(os.path.abspath(__file__)) + "\\image2.jpg"
    # similarity = Torchstart(output_path, template_path)
    # my_write_log(f"similarity：{str(similarity)}")

    compare_photos_input_path = os.path.dirname(os.path.abspath(__file__)) + "\\222.jpg"
    compare_photos_output_one_photos = os.path.dirname(os.path.abspath(__file__)) + "\\compare.jpg"
    template_photo_path_one = os.path.dirname(os.path.abspath(__file__)) + "\\777_1.jpg"
    similarity_bool = photos_similarity(
        image_path=compare_photos_input_path,  # 输入图片路径
        output_path=compare_photos_output_one_photos,  # 输出裁剪图片路径
        template_path=template_photo_path_one,  # 模版识别图片路径
        left=0,  # 裁剪区域左坐标
        top=408,  # 裁剪区域上坐标
        right=3009,  # 裁剪区域右坐标
        bottom=1140,  # 裁剪区域下坐标
        SSIM_template=90  # 模版识别图片相似度的阈值
    )
