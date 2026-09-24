import random
import time
from datetime import datetime
import os
import subprocess
from PythonPackage2 import Report_Handler as Report_Handler
from PythonPackage2 import Relay_library
from PythonPackage2.Serial_Thread import dut_buffer, start_monitoring, stop_monitoring, get_buffer
from PythonPackage2.ThreadManager import thread_manager

# 测试结果通知配置
isDebug = False
# email_user = 'yfautotest@yftech.com'
# email_pwd = '54ikrLp6LEbjTo7j'
# email_to = ['AJ9YNDmoO6Vgp@yftech.com'] if (isDebug == False) else ['848MyDe8eJx1B@yftech.com']  # CMS067-功能自动化测试邮箱
debug_api = "https://open.feishu.cn/open-apis/bot/v2/hook/d410bfb0-df24-45b4-b70f-c136e3e9438f"
api = "https://open.feishu.cn/open-apis/bot/v2/hook/eac8299d-66b9-484e-914b-5ad47573fe83" if (isDebug == False) \
    else debug_api  # CMS067-功能自动化测试群 

email_to = ['chengao@yftech.com']

# 测试报告回填配置
# 根据需要单独开启本地 Excel 或飞书电子表格回填
ENABLE_LOCAL_RESULT_UPDATE = False
ENABLE_FEISHU_RESULT_UPDATE = True
FEISHU_RESULT_DOCUMENT_URL = "*"  
FEISHU_RESULT_SHEET_NAME = "*"
FEISHU_RESULT_APP_ID = "cli_a96b11d4f538dcb1"
FEISHU_RESULT_APP_SECRET = "ucvuE1GYGxEIFzwF9uKEjdBS1iucjru2"
# 回填前将 Version.txt 中的版本号写入飞书首页
ENABLE_FEISHU_VERSION_FILL = True
# VERSION_TXT_PATH = r"D:\CMS067-A1\config\Version.txt"
# 无法从 Version.txt 自动读取的字段在此手动配置（会覆盖自动提取值）
VERSION_FIELD_OVERRIDES = {
    "硬件版本": "V1.1",
}
# 回填后将飞书云文档下载到本地（留空则保存到测试报告同目录）
ENABLE_FEISHU_SPREADSHEET_DOWNLOAD = True
FEISHU_DOWNLOAD_DIR = r"D:\CMS067-A1\Test_Reports"  # 留空时保存至测试报告同目录

triggerType = {
    "-1": "客户端本地触发",
    "0": "Jenkins或Web触发",
    "1": "Jenkins触发",
    "2": "Web触发",
    "None": "未知"
}

# 启动服务器程序时保存进程对象
Server_process = None


# 测试前步骤
def pre_function():
    Report_Handler.printh("======================开始执行测试======================")


# 测试后步骤
def after_function():
    Report_Handler.printh("======================结束执行测试======================")
    pass

# 实例化的运行函数
def with_before_after(func):
    def wrapper(*args, **kwargs):
        pre_function()
        result = func(*args, **kwargs)
        after_function()
        return result
    return wrapper

def call_with_independent_window():
    """
    在新窗口中运行64位Python脚本（有控制台窗口）
    """
    global Server_process  # 声明使用全局变量
    python64_path = r"D:\Program Files\Python\python.exe"
    script_path = r"D:\CMS064_Server_main\CMS064_Photo_Recognition_Server_main_3.0_集成分离拍照和识别.py"

    # Windows系统 - 创建新的控制台窗口
    Server_process = subprocess.Popen(
        [python64_path, script_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE  # 关键参数：创建新窗口
    )
    
    print("64位脚本已在独立窗口中运行")

def close_process(Server_process):
    """关闭服务器进程"""
    if Server_process.poll() is None:  # 检查进程是否还在运行
        Server_process.terminate()  # 尝试友好终止
        time.sleep(2)  # 给一点时间清理
        
        if Server_process.poll() is None:  # 如果还没结束
            Server_process.kill()  # 强制终止
            print("进程已被强制终止")
        else:
            print("进程已正常终止")
    else:
        print("进程已经结束")

def script_main(*args, **kwargs):
    global Server_process
    call_with_independent_window() # 服务器启动
    scriptapi.info("等待服务器启动")
    time.sleep(10)

    DeviceConnect.init(True) # 初始化设备列表
    start_time = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    script_info = scriptapi.cur_script_info()
    scriptapi.info(script_info)
    if script_info['firmwareInfo']:
        sw_path = script_info['firmwareInfo']['firmwarePath']
        ver_info = os.path.dirname(sw_path) + "/Version.txt"
    else:
        Report_Handler.printh('无升级版本相关信息')
        sw_path = "null"
        ver_info = "null"
    reportInfo = {
        "name": "report",  # 报告名称前缀
        "title": "CMS067-A1项目测试报告",  # 报告标题
        "tester": "1966398461242314754",  # 台架编号
        "desc": "冒烟测试报告"  # 报告描述
    }
    Report_Handler.test_report_init("D:")

    if sw_path != "null":
        ver = sw_path.strip("/").split("/")[-3]
    else:
        ver = "null"
    Report_Handler.printh(f'ver：{ver}')
    
    triggerTypeInt = script_info['triggerType']
    pturl = f"http://192.168.29.240/test-platform-web/script-task/script-task-detail?ptId=" \
            f"{str(script_info['ptId'])}&taskId={str(script_info['taskId'])}"
    email_text = f"测试开始\n软件版本：{ver}\n" \
                 f"测试开始时间：{start_time}\n" \
                 f"项目任务ID: {script_info['ptId']}\n" \
                 f"任务触发类型: {triggerType[str(triggerTypeInt)]}\n" \
                 f"任务触发者：{script_info['triggerUser']}\n" \
                 f"客户端名称：{script_info['clientId']}\n" \
                 f"台架名称：{script_info['benchName']}\n" \
                 f"脚本任务ID: {script_info['taskId']}\n" \
                 f"脚本ID: {script_info['scriptVersionId']}\n" \
                 f"升级包文件路径：{sw_path}\n" \
                 f"版本信息文件路径：{ver_info}\n" \
                 f"任务详情链接：{pturl}"
    # Report_Handler.sendRebotMsg(api, email_text)

    # 启动串口监控器
    scriptapi.info("启动串口监控器...")
    if not start_monitoring(DeviceConnect.Machine_MCU_deviceId, poll_interval=0.1):  # 50ms轮询间隔
        scriptapi.error("启动串口监控器失败")
        return
    
    # 开启CAN的进程
    can_thread = ZCAN_Thread.ZLG_run(1, "Main_CAN_Monitor", 0, DeviceConnect.can_deviceId)
    can_thread.add_signal_monitor(0x570, "Main_Signal_0x570", enabled=True)
    
    # 将所有模块的__all__合并
    # all_modules = NG
    all_modules = A100 + A1 + A2 + A3 + A5 + A6 + A4 + A8 + A9 + NG + A7 
    Report_Handler.printh(f"用例all_modules = {all_modules}")
    for module_name in all_modules:
        module = globals().get(module_name)
        if module is None:
            Report_Handler.printh(f"用例{module_name}实例化失败")
            continue
        if hasattr(module, "run") and callable(module.run):
            try:
                Report_Handler.printh(f"用例{module_name}实例化成功，执行run函数")
                finalFunction = with_before_after(module.run)
                finalFunction()
                Report_Handler.printh(f"用例{module_name}执行完成")
            except Exception as e:
                Report_Handler.printh(f"用例{module_name}执行异常: {e}")
                Report_Handler.sendRebotMsg(debug_api, f"用例{module_name}执行异常: {e}")
        else:
            Report_Handler.printh(f"用例{module_name}不存在入口函数")

    # can_thread.stop()
    # scriptapi.info("停止CAN监控")
    can_config = {
        'threadID': 1,
        'name': 'Main_CAN_Monitor',
        'counter': 1,
        'zlg_id': DeviceConnect.can_deviceId,
        'auto_initialize': True
    }
    thread_manager.register_thread('Main_CAN_Monitor', can_config)
    scriptapi.info("停止CAN监控线程...")
    stop_can_thread('Main_CAN_Monitor')
    time.sleep(1)

    stop_monitoring()
    scriptapi.info("停止串口监控")
    close_process(Server_process) #关闭服务器进程
    scriptapi.info("关闭服务器进程")

    # 调用写入Excel测试报告的函数
    try:
        csv_report_path = os.path.join(Report_Handler.report_path2, "report.csv")
        update_test_results(
            csv_report_path,
            enable_local=ENABLE_LOCAL_RESULT_UPDATE,
            enable_feishu=ENABLE_FEISHU_RESULT_UPDATE,
            feishu_document_url=FEISHU_RESULT_DOCUMENT_URL,
            feishu_sheet_name=FEISHU_RESULT_SHEET_NAME,
            feishu_app_id=FEISHU_RESULT_APP_ID,
            feishu_app_secret=FEISHU_RESULT_APP_SECRET,
            enable_version_fill=ENABLE_FEISHU_VERSION_FILL,
            version_txt_path=VERSION_TXT_PATH,
            version_overrides=VERSION_FIELD_OVERRIDES,
            enable_download=ENABLE_FEISHU_SPREADSHEET_DOWNLOAD,
            feishu_download_dir=FEISHU_DOWNLOAD_DIR,
            logger=scriptapi,
        )
    except Exception as exc:
        scriptapi.error(f"测试结果回填失败: {exc}")
        Report_Handler.sendRebotMsg(debug_api, f"测试结果回填失败: {exc}")

    Report_Handler.report_output(reportInfo)  # 生成测试报告
    report_path = Report_Handler.report_path
    Report_Handler.printh(f'测试报告路径：{report_path}')
    Report_Handler.report_output(reportInfo)
    case_total = Report_Handler.ok + Report_Handler.ng
    pass_rate = round(Report_Handler.ok / case_total * 100, 2)
    end_time = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    report_name = reportInfo['name'] + ver + "_" + start_time.replace(":", "-")
    repurl = f"http://192.168.29.240/test-platform/script-task/reportFile/{str(script_info['taskId'])}/{report_name}/index.html"
    email_text = f"全功能测试结束\n软件版本：{ver}\n" \
                 f"测试开始时间：{start_time}\n" \
                 f"测试结束时间：{end_time}\n" \
                 f"项目任务ID: {script_info['ptId']}\n" \
                 f"任务触发类型: {triggerType[str(triggerTypeInt)]}\n" \
                 f"任务触发者：{script_info['triggerUser']}\n" \
                 f"客户端名称：{script_info['clientId']}\n" \
                 f"台架名称：{script_info['benchName']}\n" \
                 f"脚本任务ID: {script_info['taskId']}\n" \
                 f"脚本ID: {script_info['scriptVersionId']}\n" \
                 f"通过率：{pass_rate}%\n" \
                 f"case总数：{case_total}\n" \
                 f"通过case数：{Report_Handler.ok}\n" \
                 f"未通过case数：{Report_Handler.ng}\n" \
                 f"升级包文件路径：{sw_path}\n" \
                 f"版本信息文件路径：{ver_info}\n" \
                 f"任务详情链接：{pturl}\n" \
                 f"测试报告链接：{repurl}"
    Report_Handler.sendRebotMsg(api, email_text)
    if Report_Handler.ng:
        title = f'CMS067-A1_全功能测试不通过'
    else:
        title = f'CMS067-A1_全功能测试通过'
    Report_Handler.sendEmail(email_user, email_pwd, email_to, title, email_text, report_name)

if __name__ == "__main__":
    script_main()