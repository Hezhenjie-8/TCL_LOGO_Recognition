# -*- coding: utf-8 -*-
"""
**************** 脚本文件说明 ****************
脚本功能：生成html测试报告
denghui@yftech.com
本地化修改：移除对scriptapi的依赖，支持纯本地调用
"""

import re
import base64
import os, json, shutil
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import zipfile, smtplib, requests, time, datetime, html
from jinja2 import Environment, FileSystemLoader, select_autoescape

# ==================== 模拟 scriptapi（本地调用时自动启用） ====================
try:
    import scriptapi
except ImportError:
    class MockScriptApi:
        @staticmethod
        def cur_script_info():
            return {
                "ptId": "local",
                "taskId": "local",
                "projectCode": "LOCAL_PROJECT",
                "benchName": "local_tester",
                "scriptName": "local_script",
                "clientId": "local_client",
                "clientVersion": "1.0",
                "triggerType": -1,
                "triggerUser": "local",
                "firmwareInfo": {"firmwarePath": "unknown"}
            }
        @staticmethod
        def info(msg):
            print(f"[INFO] {msg}")
        @staticmethod
        def error(msg):
            print(f"[ERROR] {msg}")
        @staticmethod
        def around_script(info):
            print(f"[AROUND] {info}")
        @staticmethod
        def before_script(info):
            print(f"[BEFORE] {info}")
        @staticmethod
        def after_script(info):
            print(f"[AFTER] {info}")
    scriptapi = MockScriptApi()
# =============================================================================

start_ts = 0
ok, ng = 0, 0
rep_data = ''
printhinfo = ""
report_path = ""
image_path2 = ""
report_path2 = ""
start_ts_name = ""
printh_records = []
csv_data = '自动化用例编号, 用例类型, 用例名称, 断言依据, 用例执行结果, 用例执行时间\n'

test_result = {
    "all": 0,
    "ptId": "",
    "title": "",
    "tester": "",
    "desc": "",
    "cases": {},
    "case": {},
    'rerun': 0,
    "failed": 0,
    "passed": 0,
    "warning": 0,
    "error": 0,
    "start_time": 0,
    "end_time": 0,
    "run_time": 0,
    "begin_time": "",
    "pass_rate": 0,
    "proj_name": "",
    "client_name": "",
    "client_version": "",
    "trigger_type": "",
    "trigger_user": "",
    "scriptVerId": "",
    "scriptId": "",
    "script_name": "冒烟测试脚本",
    "upgrade_package_path": "",
    "version_info_path": "",
    "task_details_url": "",
    "testModules": []
}
reportInfo = {
    "name": "DK033_report_",
    "title": "DK033-A1 冒烟测试报告",
    "tester": "FT07"
}
result = {
    "id": "用例ID",  # 用例ID
    "caseNum":"用例编号", # 用例编号
    'outcome': 'passed',  # 测试结果
    'testType': '用例类型',  # 用例类型
    'testName': '用例名称',  # 用例名称
    'desc': '断言依据',  # 断言依据
    'duration': '执行时间',  # 执行时间
    'capstdout': '测试用例输出'  # 测试用例输出
}
triggerType = {
    "-1": "客户端本地触发",
    "0": "Jenkins或Web触发",
    "1": "Jenkins触发",
    "2": "Web触发",
    "None": "未知"
}
after_script_info = {
    "zip_report_path": ""
}


def test_report_init(out_path):
    global test_result, printhinfo, report_path, report_path2, csv_data, rep_data, ok, ng
    test_result["cases"] = {}
    test_result['passed'] = 0
    test_result['failed'] = 0
    test_result['warning'] = 0
    test_result['error'] = 0
    ok, ng = 0, 0
    printhinfo = ""
    csv_data = '自动化用例编号, 用例类型, 用例名称, 断言依据, 用例执行结果, 用例执行时间\n'
    rep_data = ''
    session_start()
    report_path = out_path + "\\Report\\" + start_ts_name + "\\"
    if not os.path.exists(report_path):
        os.makedirs(report_path)
    tmp_path = report_path + "tmp\\"
    if not os.path.exists(tmp_path):
        tmp_src = os.path.join(os.path.dirname(__file__), "tmp")
        if os.path.isdir(tmp_src):
            shutil.copytree(tmp_src, tmp_path)
        else:
            os.makedirs(tmp_path, exist_ok=True)
            scriptapi.error(f"静态资源目录不存在：{tmp_src}，报告页将缺少样式脚本")
    report_path2 = report_path + "case\\"
    if not os.path.exists(report_path2):
        os.makedirs(report_path2)
    res = scriptapi.cur_script_info()
    if res.get('firmwareInfo'):
        test_result['upgrade_package_path'] = res.get('firmwareInfo')['firmwarePath']
        test_result['version_info_path'] = os.path.dirname(test_result['upgrade_package_path']) + "/Version.txt"
        test_result['task_details_url'] = "http://192.168.29.240/test-platform-web/script-task/script-task-detail?" \
                                          f"ptId={str(res.get('ptId'))}&taskId={str(res.get('taskId'))}"
    else:
        test_result['upgrade_package_path'] = "未知"
        test_result['version_info_path'] = "未知"
        test_result['task_details_url'] = "未知"
    test_result['scriptId'] = res.get('scriptId')
    test_result['scriptVerId'] = res.get('scriptVersionId')
    test_result['proj_name'] = res.get('projectCode')
    test_result['tester'] = res.get("benchName")
    test_result['script_name'] = res.get('scriptName')
    test_result['client_name'] = res.get('clientId')
    test_result['client_version'] = res.get('clientVersion')
    test_result['trigger_type'] = triggerType[str(res.get('triggerType'))]
    test_result['trigger_user'] = res.get('triggerUser')
    test_result['ptId'] = res.get('ptId')

def generate_printh_report(test_case_title, filename="printh_report.html"):
    global printh_records
    processed_records = []
    
    for item in printh_records:
        processed_item = {}
        for k, v in item.items():
            if k in ['result_color', 'can_expand']:
                processed_item[k] = v
            elif k in ['description', 'expected', 'actual']:
                processed_content = process_image_content(str(v), report_path)
                if is_html_content(processed_content):
                    processed_item[k] = processed_content
                else:
                    processed_item[k] = html.escape(processed_content)
            else:
                processed_item[k] = html.escape(str(v))
        processed_records.append(processed_item)

    has_error = any(i['result'] == "ERROR" for i in processed_records)
    has_fail = any(i['result'] in ["NG", "FAIL"] for i in processed_records)
    has_warning = any(i['result'] == "WARNING" for i in processed_records)

    if has_error:
        suffix, bg, fg = "：错误", "#FF8000", "#FFFFFF"
    elif has_fail:
        suffix, bg, fg = "：不通过", "#F44336", "#FFFFFF"
    elif has_warning:
        suffix, bg, fg = "：警告", "#FFFF00", "#FFFFFF"
    else:
        suffix, bg, fg = "：通过", "#4CAF50", "#FFFFFF"
    
    full_title = test_case_title + suffix
    # 模板目录：优先本文件所在目录，其次上一级目录、当前工作目录
    template_name = "testcase.html"
    search_paths = [
        os.path.dirname(__file__),
        os.path.dirname(os.path.dirname(__file__)),
        os.getcwd(),
    ]
    template_path = None
    for candidate in search_paths:
        if os.path.isfile(os.path.join(candidate, template_name)):
            template_path = candidate
            break
    if template_path is None:
        raise FileNotFoundError(
            f"未找到报告模板 {template_name}，已查找目录：" + "；".join(search_paths)
        )

    env = Environment(
        loader=FileSystemLoader(template_path),
        autoescape=select_autoescape(['html', 'xml'])
    )
    tpl = env.get_template(template_name)
    rendered = tpl.render(
        test_case_title=test_case_title,
        full_title=full_title,
        title_suffix=suffix,
        title_bg_color=bg,
        title_text_color=fg,
        data_list=processed_records
    )
    out_dir = os.path.dirname(os.path.abspath(filename))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(rendered)
    print(f"测试详情已生成：{filename}，共{len(processed_records)}条记录")

def test_out(case_num="test", case_type="test", case_name="test", ok_ng="OK", desc="无"):
    global ok, ng, csv_data, rep_data, printhinfo, printh_records

    start_ts = datetime.datetime.now()
    if ok_ng == "OK":
        result['outcome'] = "passed"
        ok += 1
        st = 0
    elif ok_ng == "NG":
        result['outcome'] = "failed"
        ng += 1
        st = 1
    elif ok_ng == "WARNING":
        result['outcome'] = "warning"
        ok += 1
        st = 0
    else:
        result['outcome'] = "error"
        ng += 1
        st = 1
    case_time = start_ts.strftime("%H:%M:%S")
    desc = desc.replace(",", "，")

    printh("================================================")
    printh("用例编号：" + case_num)
    printh("用例类型：" + case_type)
    printh("用例名称：" + case_name)
    printh("测试结果：" + desc, result=ok_ng)
    printh("结果判定：" + ok_ng, result=ok_ng)
    printh("测试OK数：" + str(ok) + "，测试NG数：" + str(ng) + "，脚本执行时间：" + case_time)
    printh("================================================\n")

    if len(desc) > 50:
        desc = "内容过长，详见打印"
    result['id'] = str(ok + ng)
    result['caseNum'] = case_num
    result['testType'] = case_type
    result['testName'] = case_name
    result['desc'] = desc
    result['capstdout'] = printhinfo
    result['duration'] = case_time
    result['url'] = 'case/' + result['id'] + '.html'

    around_script_info = {
        "cur_task_index": ok + ng,
        "error_code": st,
        "error_msg": "【用例】" + case_name + "\n【断言】" + desc,
        "description": desc,
    }
    scriptapi.around_script(around_script_info)
    csv_data += f'{case_num}, {case_type}, {case_name}, {desc}, {ok_ng}, {case_time}\n'
    rep_data += f'{case_name}, {desc}, {ok_ng}, {case_time}\n'
    report_refresh()
    file_name = report_path2 + result['id'] + '.html'
    try:
        generate_printh_report(
            f"测试用例 #{result['id']}: {case_name}",
            file_name
        )
    except Exception as e:
        scriptapi.error(f"生成printh报告失败: {str(e)}")
    # 保存CSV文件
    try:
        with open(report_path2 + 'report.csv', mode='a', encoding='utf-8') as text:
            text.write(csv_data)
    except Exception as e:
        scriptapi.error(f"写CSV失败: {str(e)}")
    printhinfo = ''
    csv_data = ''
    printh_records = []

    return ok, ng

def report_refresh():
    global test_result
    if result['outcome'] == 'passed':
        test_result['passed'] += 1
    elif result['outcome'] == 'failed':
        test_result['failed'] += 1
    elif result['outcome'] == 'warning':
        test_result['warning'] += 1
    else:
        test_result['error'] += 1
    test_result["cases"][result['id']] = result.copy()
    test_result["case"][0] = result.copy()
    test_result['testModules'].append(result['testType'])

def session_start():
    global test_result, start_ts, start_ts_name
    start_ts = datetime.datetime.now()
    start_ts_name = time.strftime("%Y-%m-%d_%H_%M_%S")
    test_result["start_time"] = start_ts.timestamp()
    test_result["begin_time"] = start_ts.strftime("%Y-%m-%d %H:%M:%S")

def printh(info, *args, result="", expected="", actual="", image_path="", image_alt="截图"):
    """
    参数:
        info: 要打印的信息
        *args: 格式化参数
        result: 结果判定，默认为""，可选（OK、NG、WARNING、ERROR）
        expected: 预期结果，用于展开详情
        actual: 实测结果，用于展开详情
        image_path: 图片路径（可选）
        image_alt: 图片描述（可选）
    """
    global printhinfo, printh_records
    info = str(info)
    if args:
        info = info.format(*args)
    if image_path:
        info = add_image_to_content(info, image_path, image_alt)
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f] ")
    full_info = timestamp + info
    if result == "" or result.upper() == "OK":
        scriptapi.info(full_info)
    else:
        scriptapi.error(full_info)
    printhinfo += full_info + '\n'
    record_type = "步骤" if result != "" else "打印"
    
    # 确定结果显示和颜色
    if result == "":
        display_result = "-"
        result_color = "#FFFFFF"  # 白色
    elif result.upper() == "OK":
        display_result = "OK"
        result_color = "#4CAF50"  # 绿色
    elif result.upper() in ["NG", "FAIL", "FAILED"]:
        display_result = "NG"
        result_color = "#F44336"  # 红色
    elif result.upper() == "WARNING":
        display_result = "WARNING"
        result_color = "#F6C73B"  # 黄色
    else:
        display_result = result
        result_color = "#800000"  # 暗红色
    
    can_expand = bool(expected.strip() or actual.strip())
    record = {
        "time_str": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "description": info,
        "result": display_result,
        "result_color": result_color,
        "expected": expected,
        "actual": actual,
        "can_expand": can_expand,
        "record_type": record_type
    }
    printh_records.append(record)
    
def process_image_content(content, base_path=None):
    if not content or not isinstance(content, str):
        return content
    img_pattern = r'<img[^>]*src=["\']([^"\']*)["\'][^>]*>' 
    def replace_img_src(match):
        full_tag = match.group(0)
        src = match.group(1)
        alt_match = re.search(r'alt=["\']([^"\']*)["\']', full_tag)
        alt_text = alt_match.group(1) if alt_match else "图片"
        if src.startswith('data:image/') or src.startswith('http'):
            return add_click_functionality(full_tag, src, alt_text)
            
        if os.path.isfile(src):
            try:
                if base_path and report_path:
                    report_case_dir = os.path.join(report_path, "case")
                    dst_path = copy_report_image_and_original(src, report_case_dir)
                    new_filename = os.path.basename(dst_path)
                    new_src = f"images/{new_filename}"
                    new_tag = full_tag.replace(f'src="{src}"', f'src="{new_src}"').replace(f"src='{src}'", f"src='{new_src}'")
                    return add_click_functionality(new_tag, new_src, alt_text)
                    
                with open(src, 'rb') as f:
                    img_data = f.read()
                    img_ext = os.path.splitext(src)[1].lower().lstrip('.')
                    if img_ext in ['jpg', 'jpeg']:
                        mime_type = 'image/jpeg'
                    elif img_ext == 'png':
                        mime_type = 'image/png'
                    elif img_ext == 'gif':
                        mime_type = 'image/gif'
                    elif img_ext in ['bmp', 'webp']:
                        mime_type = f'image/{img_ext}'
                    else:
                        mime_type = 'image/jpeg'  # 默认
                    base64_data = base64.b64encode(img_data).decode('utf-8')
                    new_src = f"data:{mime_type};base64,{base64_data}"
                    new_tag = full_tag.replace(f'src="{src}"', f'src="{new_src}"').replace(f"src='{src}'", f"src='{new_src}'")
                    
                    return add_click_functionality(new_tag, new_src, alt_text)
            except Exception as e:
                scriptapi.error(f"处理图片失败 {src}: {str(e)}")
                return full_tag
        return full_tag
    return re.sub(img_pattern, replace_img_src, content)


from PIL import Image


def get_unique_file_path(output_dir, filename):
    """Return a non-conflicting file path by appending _N before the suffix."""
    name, ext = os.path.splitext(filename)
    candidate = os.path.join(output_dir, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{name}_{counter}{ext}")
        counter += 1
    return candidate


def find_original_image_for_webp(image_path):
    """Find the raw source image paired with an auto-generated WEBP preview."""
    if os.path.splitext(image_path)[1].lower() != '.webp':
        return None

    image_dir = os.path.dirname(image_path)
    if os.path.basename(image_dir).lower() != 'images':
        return None

    raw_dir = os.path.join(os.path.dirname(image_dir), 'images_raw')
    if not os.path.isdir(raw_dir):
        return None

    stem = os.path.splitext(os.path.basename(image_path))[0]
    for entry in os.listdir(raw_dir):
        raw_stem, _ = os.path.splitext(entry)
        if raw_stem == stem:
            raw_path = os.path.join(raw_dir, entry)
            if os.path.isfile(raw_path):
                return raw_path
    return None


def copy_report_image_and_original(src, report_case_dir):
    """Copy the report image and its raw original using aligned unique naming."""
    image_dir = os.path.join(report_case_dir, 'images')
    raw_dir = os.path.join(report_case_dir, 'images_raw')
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    dst_path = get_unique_file_path(image_dir, os.path.basename(src))
    shutil.copy2(src, dst_path)

    raw_src = find_original_image_for_webp(src)
    if raw_src:
        raw_ext = os.path.splitext(raw_src)[1]
        raw_filename = f"{os.path.splitext(os.path.basename(dst_path))[0]}{raw_ext}"
        raw_dst_path = os.path.join(raw_dir, raw_filename)
        shutil.copy2(raw_src, raw_dst_path)

    return dst_path


def save_webp_image(image_path, output_dir):
    """
    将图片保存为WEBP格式，同时调整分辨率
    :param image_path: 原始图片路径
    :param output_dir: 保存WEBP图片的目录
    :return: WEBP文件路径
    """
    name, _ = os.path.splitext(os.path.basename(image_path))
    webp_path = get_unique_file_path(output_dir, f"{name}.webp")

    with Image.open(image_path) as img:
        original_width, original_height = img.size
        target_width = 1442 # 1920
        if original_width > target_width:
            scale_ratio = target_width / original_width
            target_height = int(original_height * scale_ratio)
            img = img.resize((target_width, target_height), Image.LANCZOS)

        img.save(webp_path, "WEBP", quality=70)

    return webp_path


def backup_original_image(image_path, output_dir, target_stem=None):
    """在压缩前备份原图到同级原图目录，并与WEBP文件名对齐。"""
    original_dir = os.path.join(output_dir, "images_raw")
    os.makedirs(original_dir, exist_ok=True)

    _, ext = os.path.splitext(image_path)
    filename = os.path.basename(image_path)
    if target_stem:
        filename = f"{target_stem}{ext}"

    if target_stem:
        backup_path = os.path.join(original_dir, filename)
    else:
        backup_path = get_unique_file_path(original_dir, filename)
    if os.path.abspath(image_path) != os.path.abspath(backup_path):
        shutil.copy2(image_path, backup_path)

    return backup_path

def add_image_to_content(content, image_path, alt_text="图片", insert_position="new_line"):
    if not image_path:
        return content
    
    # 生成目标路径
    base_path = os.path.dirname(image_path)
    img_dir = os.path.join(base_path, "images")
    if not os.path.exists(img_dir):
        os.makedirs(img_dir)

    # 将图片转换为WEBP格式并缩小分辨率
    webp_path = save_webp_image(image_path, img_dir)

    # 同步保留原始格式文件，文件名与生成的WEBP保持一致
    try:
        backup_original_image(
            image_path,
            base_path,
            target_stem=os.path.splitext(os.path.basename(webp_path))[0]
        )
    except Exception as e:
        printh(f"保留原图出错：{e}")
    
    title_attr = 'title="点击查看大图"'
    img_tag = f'<img src="{webp_path}" alt="{alt_text}" {title_attr}>'

    if insert_position == "start":
        return img_tag + " " + content
    elif insert_position == "new_line":
        return content + "<br>" + img_tag
    else:  # end
        return content + " " + img_tag
    
# def add_image_to_content(content, image_path, alt_text="图片", insert_position="new_line"):
#     if not image_path:
#         return content
    
#     title_attr = 'title="点击查看大图"'
#     img_tag = f'<img src="{image_path}" alt="{alt_text}" {title_attr}>'
    
#     if insert_position == "start":
#         return img_tag + " " + content
#     elif insert_position == "new_line":
#         return content + "<br>" + img_tag
#     else:  # end
#         return content + " " + img_tag

def add_click_functionality(img_tag, src, alt_text):
    if 'alt=' not in img_tag:
        img_tag = img_tag.replace('<img ', f'<img alt="{alt_text}" ')
    if 'title=' not in img_tag:
        img_tag = img_tag.replace('<img ', f'<img title="点击查看大图" ')
    
    return img_tag
    
def is_html_content(content):
    if not content or not isinstance(content, str):
        return False
    return bool(re.search(r'<[^>]+>', content))

def screenshot_and_printh(info, screenshot_func=None, *args, result="", expected="", actual=""):
    image_path = ""
    if screenshot_func and callable(screenshot_func):
        try:
            image_path = screenshot_func()
        except Exception as e:
            scriptapi.error(f"截图失败: {str(e)}")
    
    return printh(info, *args, result=result, expected=expected, actual=actual, 
                  image_path=image_path, image_alt="截图")

def handle_history_data(test_result):
    try:
        with open('history.json', 'r', encoding='utf-8') as f:
            history = json.load(f)
    except:
        history = []
    history.append({'success': test_result['passed'],
                    'all': test_result['all'],
                    'fail': test_result['failed'],
                    'warning': test_result['warning'],
                    'error': test_result['error'],
                    'runtime': test_result['run_time'],
                    'begin_time': test_result['begin_time'],
                    'pass_rate': test_result['pass_rate'],
                    })
    with open('history.json', 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=True)
    return history

def report_output(reportInfo):
    global test_result
    report2 = reportInfo["name"]
    if report_path and report2:
        test_result['title'] = reportInfo["title"]
        test_result['testModules'] = list(set(test_result['testModules']))
        file_name = report_path + 'index.html'
        test_result["end_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        test_result["run_time"] = '{:.6f} 秒'.format(time.time() - test_result["start_time"])
        test_result['all'] = len(test_result['cases'])
        if test_result['all'] != 0:
            test_result['pass_rate'] = '{:.2f}'.format(test_result['passed'] / test_result['all'] * 100)
        else:
            test_result['pass_rate'] = 0
        test_result['history'] = handle_history_data(test_result)
        template_path = os.path.join(os.path.dirname(__file__), './')
        env = Environment(loader=FileSystemLoader(template_path))
        template = env.get_template('report')
        report = template.render(test_result)
        with open(file_name, 'wb') as f:
            f.write(report.encode('utf8'))
        before_script_info = {
            "target_test_count": ok+ng,
        }
        scriptapi.before_script(before_script_info)
        printh(f'总次数：{str(before_script_info)}上报完成！')
    else:
        return
        
def zip_out(srcPath=None, zipFilePath=None, includeDirInZip=True):
    if not zipFilePath:
        zipFilePath = srcPath + ".zip"
    parentDir, dirToZip = os.path.split(srcPath)
    excluded_dirs = {"images_raw"}

    def trimPath(path):
        archivePath = path.replace(parentDir, "", 1)
        if parentDir:
            archivePath = archivePath.replace(os.path.sep, "", 1)
        if not includeDirInZip:
            archivePath = archivePath.replace(dirToZip + os.path.sep, "", 1)
        return archivePath
    with zipfile.ZipFile(zipFilePath, "w", compression=zipfile.ZIP_DEFLATED) as outFile:
        if os.path.isdir(srcPath):
            for (archiveDirPath, dirNames, fileNames) in os.walk(srcPath):
                dirNames[:] = [dir_name for dir_name in dirNames if dir_name not in excluded_dirs]
                for fileName in fileNames:
                    filePath = os.path.join(archiveDirPath, fileName)
                    outFile.write(filePath, trimPath(filePath))
                if not fileNames and not dirNames:
                    zipInfo = zipfile.ZipInfo(trimPath(archiveDirPath) + "/")
                    outFile.writestr(zipInfo, "")
        else:
            outFile.write(srcPath, trimPath(srcPath))
            
def sendEmail(from_addr, password, to_addrs, title, content, zip_name):
    global rep_data
    for n in range(3):
        try:
            addr = from_addr.split('@')[-1]
            if addr != 'yftech.com':
                smtp_server = 'smtp.' + addr  # 发信服务器
            else:
                smtp_server = 'smtp.feishu.cn'
            msg = MIMEMultipart()  # 创建一封空邮件
            msg['From'] = Header(from_addr)  # 添加邮件头信息
            msg['Subject'] = Header(title)  # 添加邮件标题
            zip_file_new = zip_name + '.zip'
            zip_out(report_path, zip_file_new)
            case_info_list = rep_data.split('\n')
            data = []
            for item in case_info_list:
                item_list = item.split(', ')
                if len(item_list) > 3:
                    data.append(item_list)
            # 构建邮件表格HTML代码
            info_str = content.replace('\n', '<br>') + '<p>\n测试结果如下表:\n</p>'
            table_header = '<tr><th>序号</th><th>用例名称</th><th>测试结果</th><th>结果判定</th><th>执行时间</th></tr>'
            table_rows = ''
            for i, item in enumerate(data, 1):
                if item[2] == 'NG' or item[2] == 'ERROR':
                    result = f'<td style="color: red; font-weight: bold;">{item[2]}</td>'
                elif item[2] == 'WARNING':
                    result = f'<td style="color: blue; font-weight: bold;">{item[2]}</td>'
                else:
                    result = f'<td style="color: green">{item[2]}</td>'
                    # result = f'<td>{item[2]}</td>'
                row = f'<tr><td>{i}</td><td>{item[0]}</td><td>{item[1]}</td>{result}<td>{item[3]}</td></tr>'
                table_rows += row
            table_html = f'<table border="1" cellpadding="5">{info_str}{table_header}{table_rows}</table>'
            # 将HTML内容添加到邮件中
            html_part = MIMEText(table_html, 'html')
            msg.attach(html_part)
            rep_data = ""
            part = MIMEBase('application', "octet-stream")
            after_script_info["zip_report_path"] = zip_file_new
            part.set_payload(open(zip_file_new, "rb").read())  # 读取附件
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', 'attachment', filename=zip_file_new)
            msg.attach(part)  # 把附件添加到邮件中
            server = smtplib.SMTP_SSL(smtp_server)  # 开启发信服务，这里使用的是加密传输
            server.connect(smtp_server, 465)  # 登录发信邮箱
            scriptapi.after_script(after_script_info)
            # 以下注释掉实际发送，避免本地误发
            # server.login(from_addr, password)
            # for to_addr in to_addrs:
            #     msg['To'] = Header(to_addr)
            #     server.sendmail(from_addr, to_addr, msg.as_string())
            server.quit()
        except Exception as e:
            scriptapi.after_script(after_script_info)
            printh('邮件发送失败: '+str(e))
            time.sleep(5)
        else:
            printh('邮件发送成功')
            break
        
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    fixture_extras = getattr(item.config, "extras", [])
    plugin_extras = getattr(report, "extra", [])
    report.extra = fixture_extras + plugin_extras
    report.testType = item.location[0]
    if hasattr(item, 'callspec'):
        report.desc = item.callspec.id or item._obj.__doc__
    else:
        report.desc = item._obj.__doc__
    report.testName = item.location[2].split('[')[0]

def pytest_make_parametrize_id(config, val, argname):
    if isinstance(val, dict):
        return val.get('title') or val.get('desc')
        
def sendRebotMsg(api, text):
    msg = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }
    rsp = requests.post(api, json=msg)
    if rsp.json()["code"] == 0:
        printh("飞书后视镜人消息发送成功")
    else:
        printh("飞书后视镜人消息发送失败")
        
def script_main(*args, **kwargs):
    # 本地调用示例
    reportInfo = {
        "name": "my_report_",  # 报告名称前缀
        "title": "我的测试报告",  # 报告标题
        "tester": "本地测试",  # 测试人员
        "desc": "基本功能测试报告"  # 报告描述
    }
    test_report_init("D:")  # 初始化测试报告，报告生成在 D:\Report\时间戳\ 下
    
    # 打印一些信息
    printh('纯文本打印')
    printh("样机出现花屏现象 <img src='fail_123.png' alt='样机花屏图像'>", result="NG")
    printh("样机出现花屏现象", image_path="fail_123.png", image_alt="样机花屏图像", result="NG")
    printh("UI对比测试", result="NG", 
           expected="按钮应该是蓝色 <img src='expected_button.png' alt='预期按钮'>",
           actual="按钮实际是红色 <img src='actual_button.png' alt='实际按钮'>")
           
    for i in range(1, 10):
        for j in range(10):
            printh('hello test report ' + str(j))  # 脚本打印
        test_out(case_num=str(i),  # 用例编号
                case_name="XXX模块XXX测试_" + str(i),  # 用例名称
                ok_ng="OK",  # 测试结果
                case_type="XXX_" + str(i),  # 用例类型
                desc="XXX"  # 断言依据
                )

    report_output(reportInfo)  # 生成测试报告

if __name__ == '__main__':
    printh("testreport.py")
    script_main()