"""独立的 Logo 图像比对模块（单文件，对外只暴露一个函数）。

融合了原项目的两层判定：
    1. 本地算法：图像归一化 + 像素相似度 + 结构相似度(SSIM) + 感知哈希
    2. 多模态大模型复核：Qwen3-VL-Flash（DashScope OpenAI 兼容接口）

判定策略（本地直判相同 0.88，其余交模型终裁，降级阈值 0.88）：
    - score >= direct_match_threshold(默认 0.88) -> 本地直接判相同，不调用大模型
    - score < direct_match_threshold -> 调用 Qwen 复核并由其终裁，same_logo 与
      appearance_consistent 同时为真才算匹配
    - 未配置 Key 或调用失败 -> 按降级阈值 fallback_threshold(默认 0.88) 判定
      （进入该分支时 score 必然小于 0.88，因此降级结论为不相同）

依赖：仅 Pillow（HTTP 走标准库 urllib）
    pip install pillow

用法（只调用一个函数）：
    from logo_similarity import compare_logo_images

    result = compare_logo_images("reference.png", "candidate.png")
    print(result["matched"], result["decision_source"], result["score"])

配置来源（每个参数都按「函数入参 > 环境变量 > config.json > 代码默认值」取值）：
    参数                     环境变量                       config.json 键名                 默认值
    API Key                  LOGO_QWEN_API_KEY              qwen_api_key                    无
                             QWEN_API_KEY / DASHSCOPE_API_KEY
    接口地址                 LOGO_QWEN_API_BASE_URL         qwen_base_url                   DashScope 兼容地址
    模型名                   LOGO_QWEN_MODEL                qwen_model                      qwen3-vl-flash
    送模型前图片长边上限     LOGO_QWEN_MAX_IMAGE_SIDE       qwen_max_image_side             768
    请求超时(秒)             LOGO_QWEN_TIMEOUT_SECONDS      qwen_timeout_seconds            60
    本地直接判定阈值         LOGO_DIRECT_MATCH_THRESHOLD    logo_direct_match_threshold     0.88
    降级判定阈值             LOGO_FALLBACK_THRESHOLD        logo_fallback_threshold         0.88
    本地归一化边长           LOGO_TARGET_SIZE               logo_target_size                256

    config.json 的查找顺序：
        环境变量 LOGO_QWEN_CONFIG 指定的路径
        -> 当前工作目录/config.json
        -> 本文件所在目录/config.json
        -> 本文件所在目录的上一级/config.json
    键留空/删除时使用下一优先级；填入非法值（非数字）自动回退到默认值，不会报错。

命令行自测：
    python logo_similarity.py reference.png candidate.png
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

# ----------------------------- 本地算法默认参数 ----------------------------- #
DEFAULT_TARGET_SIZE = 256
DEFAULT_DIRECT_MATCH_THRESHOLD = 0.88
DEFAULT_FALLBACK_THRESHOLD = 0.88

_WEIGHT_PIXEL = 0.45
_WEIGHT_STRUCTURAL = 0.30
_WEIGHT_PERCEPTUAL = 0.25

# ----------------------------- Qwen 默认参数 ------------------------------- #
DEFAULT_QWEN_BASE_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEFAULT_QWEN_MODEL = "qwen3-vl-flash"
DEFAULT_QWEN_MAX_IMAGE_SIDE = 768
DEFAULT_QWEN_TIMEOUT_SECONDS = 60.0

# 按顺序读取任意一个存在的环境变量
_QWEN_API_KEY_ENV_NAMES = ("LOGO_QWEN_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY")

# config.json 回退：环境变量都没命中时，从与项目其它配置同一个 config.json 里读
_QWEN_CONFIG_FILE_ENV = "LOGO_QWEN_CONFIG"
_QWEN_CONFIG_FILENAME = "config.json"
_QWEN_API_KEY_CONFIG_KEYS = (
    "qwen_api_key",
    "qwenApiKey",
    "QWEN_API_KEY",
    "dashscope_api_key",
    "dashscopeApiKey",
    "DASHSCOPE_API_KEY",
)
_QWEN_BASE_URL_CONFIG_KEYS = ("qwen_base_url", "qwenBaseUrl", "QWEN_API_BASE_URL")
_QWEN_MODEL_CONFIG_KEYS = ("qwen_model", "qwenModel", "QWEN_MODEL")
_QWEN_MAX_IMAGE_SIDE_CONFIG_KEYS = (
    "qwen_max_image_side",
    "qwenMaxImageSide",
    "LOGO_QWEN_MAX_IMAGE_SIDE",
)
_QWEN_TIMEOUT_CONFIG_KEYS = (
    "qwen_timeout_seconds",
    "qwenTimeoutSeconds",
    "LOGO_QWEN_TIMEOUT_SECONDS",
)
_LOGO_DIRECT_MATCH_THRESHOLD_CONFIG_KEYS = (
    "logo_direct_match_threshold",
    "logoDirectMatchThreshold",
    "direct_match_threshold",
    "LOGO_DIRECT_MATCH_THRESHOLD",
)
_LOGO_FALLBACK_THRESHOLD_CONFIG_KEYS = (
    "logo_fallback_threshold",
    "logoFallbackThreshold",
    "fallback_threshold",
    "LOGO_FALLBACK_THRESHOLD",
)
_LOGO_TARGET_SIZE_CONFIG_KEYS = (
    "logo_target_size",
    "logoTargetSize",
    "target_size",
    "LOGO_TARGET_SIZE",
)

# 本地算法参数对应的环境变量名
_LOGO_DIRECT_MATCH_THRESHOLD_ENV = "LOGO_DIRECT_MATCH_THRESHOLD"
_LOGO_FALLBACK_THRESHOLD_ENV = "LOGO_FALLBACK_THRESHOLD"
_LOGO_TARGET_SIZE_ENV = "LOGO_TARGET_SIZE"

_QWEN_CONFIG_CACHE: dict[str, Any] | None = None

_QWEN_SYSTEM_PROMPT = (
    "你是一个严谨的 Logo 视觉一致性检测专家。请同时分析用户提供的两张图片，"
    "并只依据图片内容作出判断。\n\n"
    "这里的“是否是同一个 Logo”采用严格的视觉一致标准，而不是“是否属于"
    "同一个品牌”。只有下面所有组成部分都一致时，"
    "same_logo 和 appearance_consistent 才能为 true：\n"
    "- Logo 中显示的品牌名称和文字内容；\n"
    "- 核心图形、字母结构、字体、字形比例、笔画和字母间距；\n"
    "- Logo 的容器形状和几何结构，例如正方形、矩形、椭圆、圆形、缺角、"
    "圆角半径；\n"
    "- Logo 的背景、底色、填充方式、边框和颜色；\n"
    "- 图形与文字的排列、相对比例以及 Logo 自身的阴影、描边、渐变和纹理。\n\n"
    "特别规则：截图水印、平台角标、时间戳、压缩噪声和图片边缘的外部叠加"
    "不属于 Logo 本体，应忽略，不影响 same_logo 或 appearance_consistent。"
    "只有当水印或其他标识本身属于 Logo 设计的一部分时，才需要参与比较。"
)


class LogoComparisonError(ValueError):
    """输入图片无法被安全地归一化用于比对时抛出。"""


class QwenReviewError(RuntimeError):
    """大模型复核无法返回有效结果时抛出（内部使用，会被降级处理）。"""


@dataclass(frozen=True)
class _QwenReviewResult:
    """大模型返回的、与厂商无关的标准化结果。"""

    same_logo: bool
    appearance_consistent: bool
    confidence: float
    reason: str

    @property
    def matched(self) -> bool:
        return self.same_logo and self.appearance_consistent


# --------------------------------------------------------------------------- #
# 对外唯一入口
# --------------------------------------------------------------------------- #
def compare_logo_images(
    reference: str | Path | bytes,
    candidate: str | Path | bytes,
    *,
    direct_match_threshold: float | None = None,
    fallback_threshold: float | None = None,
    target_size: int | None = None,
) -> dict[str, Any]:
    """比较两张 Logo 图片：本地算法预筛 + 大模型复核。

    Args:
        reference: 基准图片（文件路径 / Path / 原始 bytes）。
        candidate: 待比对图片（文件路径 / Path / 原始 bytes）。
        direct_match_threshold: 本地得分达到该值即直接判相同，不再调用大模型。
            None 表示依次读取环境变量 LOGO_DIRECT_MATCH_THRESHOLD、
            config.json 的 logo_direct_match_threshold、默认 0.88。
        fallback_threshold: 未配置 Key 或大模型调用失败时，本地得分达到该值才判相同。
            None 表示依次读取环境变量 LOGO_FALLBACK_THRESHOLD、
            config.json 的 logo_fallback_threshold、默认 0.88。
        target_size: 归一化后的边长（像素），不小于 64。None 表示依次读取
            环境变量 LOGO_TARGET_SIZE、config.json 的 logo_target_size、默认 256。

    Returns:
        结果字典，包含：
            - matched: 最终是否判定为同一 Logo
            - decision_source: direct（本地直判相同）/ llm / fallback，表示最终判定来源
            - llm_status: not_needed / completed / not_configured / failed
            - score: 0~1 的本地综合相似度
            - pixel_similarity / structural_similarity / perceptual_hash_similarity
            - llm_match / llm_same_logo / llm_appearance_consistent /
              llm_confidence / llm_reason（仅在调用大模型时出现）
            - direct_match_threshold（本地直判相同阈值）/ fallback_threshold（降级判定
              阈值）/ decision_reason
            - reference / candidate: 原图元数据（filename/width/height/mode）

    Raises:
        FileNotFoundError: 传入的是路径但文件不存在。
        LogoComparisonError: 图片为空、损坏或格式不被支持。
        ValueError: 参数非法。
    """
    if direct_match_threshold is None:
        direct_match_threshold = _setting_float(
            _LOGO_DIRECT_MATCH_THRESHOLD_ENV,
            _LOGO_DIRECT_MATCH_THRESHOLD_CONFIG_KEYS,
            DEFAULT_DIRECT_MATCH_THRESHOLD,
        )
    if fallback_threshold is None:
        fallback_threshold = _setting_float(
            _LOGO_FALLBACK_THRESHOLD_ENV,
            _LOGO_FALLBACK_THRESHOLD_CONFIG_KEYS,
            DEFAULT_FALLBACK_THRESHOLD,
        )
    if target_size is None:
        target_size = _setting_int(
            _LOGO_TARGET_SIZE_ENV, _LOGO_TARGET_SIZE_CONFIG_KEYS, DEFAULT_TARGET_SIZE
        )

    if not 0.0 <= direct_match_threshold <= 1.0:
        raise ValueError("direct_match_threshold 必须在 0 到 1 之间")
    if not 0.0 <= fallback_threshold <= 1.0:
        raise ValueError("fallback_threshold 必须在 0 到 1 之间")
    if target_size < 64:
        raise ValueError("target_size 必须不小于 64")

    reference_bytes, reference_name = _read_input(reference)
    candidate_bytes, candidate_name = _read_input(candidate)

    reference_image, reference_meta = _prepare_image(
        reference_bytes, reference_name, target_size
    )
    candidate_image, candidate_meta = _prepare_image(
        candidate_bytes, candidate_name, target_size
    )

    reference_gray = _grayscale_matrix(reference_image)
    candidate_gray = _grayscale_matrix(candidate_image)

    pixel_similarity = _pixel_similarity(reference_image, candidate_image)
    structural_similarity = _structural_similarity(reference_gray, candidate_gray)
    perceptual_hash_similarity = _phash_similarity(reference_gray, candidate_gray)
    score = _clamp(
        _WEIGHT_PIXEL * pixel_similarity
        + _WEIGHT_STRUCTURAL * structural_similarity
        + _WEIGHT_PERCEPTUAL * perceptual_hash_similarity
    )

    result: dict[str, Any] = {
        "matched": False,
        "decision_source": "fallback",
        "llm_status": "not_configured",
        "score": score,
        "pixel_similarity": pixel_similarity,
        "structural_similarity": structural_similarity,
        "perceptual_hash_similarity": perceptual_hash_similarity,
        "direct_match_threshold": direct_match_threshold,
        "fallback_threshold": fallback_threshold,
        "decision_reason": "",
        "reference": reference_meta,
        "candidate": candidate_meta,
    }

    # 1) 本地高置信度：直接判定相同，不调用大模型
    if score >= direct_match_threshold:
        result.update(
            matched=True,
            decision_source="direct",
            llm_status="not_needed",
            decision_reason="综合相似度达到直接判定相同阈值。",
        )
        return result

    # 2) 未达到本地直判阈值：交给 Qwen 终裁（无 Key 或调用失败时按降级阈值判定）
    api_key = _resolve_api_key()
    if not api_key:
        result.update(
            matched=score >= fallback_threshold,
            decision_source="fallback",
            llm_status="not_configured",
            decision_reason=(
                f"未达到本地直判阈值{direct_match_threshold:.4f}，但未配置 Qwen API Key，"
                f"已按降级阈值{fallback_threshold:.4f}判定。"
            ),
        )
        return result

    try:
        review = _request_qwen_review(
            reference_bytes=reference_bytes,
            candidate_bytes=candidate_bytes,
            api_key=api_key,
        )
    except QwenReviewError:
        result.update(
            matched=score >= fallback_threshold,
            decision_source="fallback",
            llm_status="failed",
            decision_reason=(
                f"Qwen 复核失败，已按降级阈值{fallback_threshold:.4f}判定。"
            ),
        )
        return result

    result.update(
        matched=review.matched,
        decision_source="llm",
        llm_status="completed",
        llm_match=review.matched,
        llm_same_logo=review.same_logo,
        llm_appearance_consistent=review.appearance_consistent,
        llm_confidence=review.confidence,
        llm_reason=review.reason,
        decision_reason="综合相似度处于大模型介入区间，采用 Qwen 视觉复核结果。",
    )
    return result


# --------------------------------------------------------------------------- #
# 输入读取与图像归一化
# --------------------------------------------------------------------------- #
def _read_input(source: str | Path | bytes) -> tuple[bytes, str | None]:
    """把路径或 bytes 统一读成 (bytes, 文件名)。"""

    if isinstance(source, (bytes, bytearray)):
        return bytes(source), None

    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在：{path}")
    return path.read_bytes(), path.name


def _prepare_image(
    content: bytes, filename: str | None, target_size: int
) -> tuple[Image.Image, dict[str, Any]]:
    """归一化图片：修正方向 -> 裁剪空白 -> 白底合成 -> 等比缩放居中。"""

    if not content:
        raise LogoComparisonError("图片内容为空")

    try:
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            original_width, original_height = source.size
            original_mode = source.mode
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise LogoComparisonError("无法识别的图片或图片已损坏") from exc

    metadata: dict[str, Any] = {
        "filename": filename,
        "width": original_width,
        "height": original_height,
        "mode": original_mode,
    }

    image = _trim_background(image)
    image = _composite_on_white(image)
    image = ImageOps.contain(
        image,
        (target_size, target_size),
        method=Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (target_size, target_size), "white")
    left = (target_size - image.width) // 2
    top = (target_size - image.height) // 2
    canvas.paste(image.convert("RGB"), (left, top))
    return canvas, metadata


def _composite_on_white(image: Image.Image) -> Image.Image:
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, image)


def _trim_uniform_border(image: Image.Image, tolerance: int = 12) -> Image.Image:
    """
    按四角颜色识别背景色，裁掉纯色边框(黑底、白底都支持)，只保留 logo 本体。

    机器开机logo图常是"整屏黑底 + 中间logo"，标准库图常是"白底 + logo"，
    两者构图语义不同: 一个是完整画面、一个是 logo 特写。
    裁掉各自背景后，两张图都只剩 logo 本体，再统一尺寸才真正可比。
    四角颜色不一致(渐变/水印等)时不做裁剪，避免裁坏内容。

    :param image: 输入图片
    :param tolerance: 与背景色的容差
    :return: 裁剪后的图片(未裁剪时返回原图)
    :rtype: Image.Image
    """
    rgba = image.convert("RGBA")
    rgb = rgba.convert("RGB")
    width, height = rgb.size
    if width < 3 or height < 3:
        return rgba

    corners = [rgb.getpixel((0, 0)), rgb.getpixel((width - 1, 0)),
               rgb.getpixel((0, height - 1)), rgb.getpixel((width - 1, height - 1))]
    # 四角必须基本一致才认为是纯色背景，否则不裁(渐变/水印背景可能裁坏内容)
    for channel in range(3):
        values = [c[channel] for c in corners]
        if max(values) - min(values) > tolerance * 2:
            return rgba

    background = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, background))
    difference = difference.point(lambda value: 255 if value > tolerance else 0)
    if rgba.getchannel("A").getextrema()[0] < 255:
        alpha = rgba.getchannel("A").point(lambda value: 255 if value > tolerance else 0)
        mask = ImageChops.lighter(difference.convert("L"), alpha)
    else:
        mask = difference

    bbox = mask.getbbox()
    if bbox is None:
        return rgba

    left, top, right, bottom = bbox
    content_w, content_h = right - left, bottom - top
    padding = max(2, int(max(content_w, content_h) * 0.08))
    return rgba.crop((
        max(0, left - padding),
        max(0, top - padding),
        min(width, right + padding),
        min(height, bottom + padding),
    ))


def _trim_background(image: Image.Image) -> Image.Image:
    """裁剪近白边距，同时保留少量内容留白。"""

    rgba = image.convert("RGBA")
    rgb = rgba.convert("RGB")
    white = Image.new("RGB", rgb.size, (255, 255, 255))
    difference = ImageChops.difference(rgb, white)
    difference = difference.point(lambda value: 255 if value > 12 else 0)
    if rgba.getchannel("A").getextrema()[0] < 255:
        alpha = rgba.getchannel("A").point(lambda value: 255 if value > 12 else 0)
        mask = ImageChops.lighter(difference.convert("L"), alpha)
    else:
        mask = difference
    bbox = mask.getbbox()
    if bbox is None:
        return rgba

    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    padding = max(2, int(max(width, height) * 0.08))
    crop_box = (
        max(0, left - padding),
        max(0, top - padding),
        min(rgb.width, right + padding),
        min(rgb.height, bottom + padding),
    )
    return rgba.crop(crop_box)


# --------------------------------------------------------------------------- #
# 本地相似度算法
# --------------------------------------------------------------------------- #
def _grayscale_matrix(image: Image.Image) -> list[list[float]]:
    grayscale = ImageOps.grayscale(image)
    pixels = _flattened_pixels(grayscale)
    width, height = grayscale.size
    return [
        [pixels[row * width + column] / 255.0 for column in range(width)]
        for row in range(height)
    ]


def _pixel_similarity(first: Image.Image, second: Image.Image) -> float:
    """比较 RGB 通道，保证「换色」不会被判成一致。"""

    first_pixels = _flattened_pixels(first.convert("RGB"))
    second_pixels = _flattened_pixels(second.convert("RGB"))
    values = [
        abs(channel_a - channel_b) / 255.0
        for pixel_a, pixel_b in zip(first_pixels, second_pixels, strict=True)
        for channel_a, channel_b in zip(pixel_a, pixel_b, strict=True)
    ]
    return _clamp(1.0 - (sum(values) / len(values) if values else 1.0))


def _structural_similarity(first: list[list[float]], second: list[list[float]]) -> float:
    """标准 SSIM（c1=0.01^2, c2=0.03^2）。"""

    values_a = [value for row in first for value in row]
    values_b = [value for row in second for value in row]
    if not values_a or not values_b:
        return 0.0

    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    variance_a = sum((value - mean_a) ** 2 for value in values_a) / len(values_a)
    variance_b = sum((value - mean_b) ** 2 for value in values_b) / len(values_b)
    covariance = (
        sum(
            (value_a - mean_a) * (value_b - mean_b)
            for value_a, value_b in zip(values_a, values_b, strict=True)
        )
        / len(values_a)
    )

    c1 = 0.01**2
    c2 = 0.03**2
    numerator = (2 * mean_a * mean_b + c1) * (2 * covariance + c2)
    denominator = (mean_a**2 + mean_b**2 + c1) * (variance_a + variance_b + c2)
    if denominator == 0:
        return 1.0 if values_a == values_b else 0.0
    return _clamp(numerator / denominator)


def _phash_similarity(first: list[list[float]], second: list[list[float]]) -> float:
    hash_a = _perceptual_hash(first)
    hash_b = _perceptual_hash(second)
    distance = sum(
        bit_a != bit_b for bit_a, bit_b in zip(hash_a, hash_b, strict=True)
    )
    return _clamp(1.0 - (distance / len(hash_a)))


def _perceptual_hash(pixels: list[list[float]]) -> list[bool]:
    """不依赖 OpenCV / SciPy 的快速均值哈希。"""

    source_size = len(pixels)
    if source_size == 0:
        return []
    sample = _resize_matrix(pixels, 8, 8)
    values = [value for row in sample for value in row]
    threshold = sum(values) / len(values)
    return [value >= threshold for value in values]


def _resize_matrix(
    matrix: list[list[float]], target_width: int, target_height: int
) -> list[list[float]]:
    """用于感知哈希的双线性缩放。"""

    source_height = len(matrix)
    source_width = len(matrix[0]) if source_height else 0
    if not source_width or not source_height:
        return [[0.0] * target_width for _ in range(target_height)]

    result: list[list[float]] = []
    for target_y in range(target_height):
        source_y = target_y * (source_height - 1) / max(1, target_height - 1)
        y0, y1 = int(source_y), min(source_height - 1, int(source_y) + 1)
        y_weight = source_y - y0
        row: list[float] = []
        for target_x in range(target_width):
            source_x = target_x * (source_width - 1) / max(1, target_width - 1)
            x0, x1 = int(source_x), min(source_width - 1, int(source_x) + 1)
            x_weight = source_x - x0
            top = matrix[y0][x0] * (1 - x_weight) + matrix[y0][x1] * x_weight
            bottom = matrix[y1][x0] * (1 - x_weight) + matrix[y1][x1] * x_weight
            row.append(top * (1 - y_weight) + bottom * y_weight)
        result.append(row)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _flattened_pixels(image: Image.Image) -> list:
    """兼容不同 Pillow 版本地读取像素，避免弃用告警。"""

    get_flattened_data = getattr(image, "get_flattened_data", None)
    if get_flattened_data is not None:
        return list(get_flattened_data())
    return list(image.getdata())


# --------------------------------------------------------------------------- #
# Qwen3-VL 复核（DashScope OpenAI 兼容接口）
# --------------------------------------------------------------------------- #
def _config_file_candidates() -> list[Path]:
    """按优先级返回可能的 config.json 路径（去重）。"""

    candidates: list[Path] = []
    override = os.getenv(_QWEN_CONFIG_FILE_ENV)
    if override and override.strip():
        candidates.append(Path(override.strip()))
    try:
        here = Path(__file__).resolve().parent
    except (NameError, OSError):  # 极端情况下取不到文件位置
        here = Path.cwd()
    candidates.extend(
        (
            Path.cwd() / _QWEN_CONFIG_FILENAME,
            here / _QWEN_CONFIG_FILENAME,
            here.parent / _QWEN_CONFIG_FILENAME,
        )
    )

    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _load_qwen_config() -> dict[str, Any]:
    """读取 config.json（带缓存）。读不到或格式非法时返回空字典，不影响本地算法。"""

    global _QWEN_CONFIG_CACHE
    if _QWEN_CONFIG_CACHE is not None:
        return _QWEN_CONFIG_CACHE

    _QWEN_CONFIG_CACHE = {}
    for path in _config_file_candidates():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            _QWEN_CONFIG_CACHE = data
            break
    return _QWEN_CONFIG_CACHE


def _config_text(*names: str) -> str | None:
    """从 config.json 按别名顺序取第一个有效值（数字会被转成字符串），取不到返回 None。"""

    config = _load_qwen_config()
    for name in names:
        value = config.get(name)
        if isinstance(value, str):
            text = value.strip()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            text = str(value)
        else:
            continue
        if text:
            return text
    return None


def _setting_text(env_name: str, config_keys: tuple[str, ...], default: str) -> str:
    """读字符串配置：环境变量优先，其次 config.json，最后默认值。"""

    value = (os.getenv(env_name) or "").strip()
    if value:
        return value
    return _config_text(*config_keys) or default


def _resolve_api_key() -> str | None:
    """按顺序读取可用的 API Key：环境变量 -> config.json 的 qwen_api_key。"""

    for name in _QWEN_API_KEY_ENV_NAMES:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return _config_text(*_QWEN_API_KEY_CONFIG_KEYS)


def _request_qwen_review(
    *,
    reference_bytes: bytes,
    candidate_bytes: bytes,
    api_key: str,
) -> _QwenReviewResult:
    """调用 Qwen3-VL-Flash 判断两张图是否为同一个 Logo。"""

    base_url = _setting_text(
        "LOGO_QWEN_API_BASE_URL", _QWEN_BASE_URL_CONFIG_KEYS, DEFAULT_QWEN_BASE_URL
    )
    model = _setting_text("LOGO_QWEN_MODEL", _QWEN_MODEL_CONFIG_KEYS, DEFAULT_QWEN_MODEL)
    max_image_side = _setting_int(
        "LOGO_QWEN_MAX_IMAGE_SIDE",
        _QWEN_MAX_IMAGE_SIDE_CONFIG_KEYS,
        DEFAULT_QWEN_MAX_IMAGE_SIDE,
    )
    timeout = _setting_float(
        "LOGO_QWEN_TIMEOUT_SECONDS",
        _QWEN_TIMEOUT_CONFIG_KEYS,
        DEFAULT_QWEN_TIMEOUT_SECONDS,
    )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _QWEN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "图1（reference，基准图片）："},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _to_image_data_url(
                                reference_bytes, max_image_side
                            )
                        },
                    },
                    {"type": "text", "text": "图2（candidate，待比对图片）："},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _to_image_data_url(
                                candidate_bytes, max_image_side
                            )
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "请严格判断两张图片是否为同一个 Logo，并只输出 JSON，"
                            "不要 Markdown："
                            '{"same_logo":true或false,'
                            '"appearance_consistent":true或false,'
                            '"confidence":0到1之间的数字,'
                            '"reason":"不超过80字的中文理由"}'
                        ),
                    },
                ],
            },
        ],
        "temperature": 0,
        "max_tokens": 128,
    }

    request = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = _extract_message_content(body)
        return _parse_review(content)
    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise QwenReviewError("Qwen 复核请求失败") from exc


def _to_image_data_url(content: bytes, max_image_side: int) -> str:
    """
    发送前把两张图统一成"相同画布尺寸、相同构图"的 JPEG data URL:
    修正方向 -> 白底合成 -> 等比缩放(不裁剪)居中到
    max_image_side × max_image_side 的白色画布。

    统一构图的必要性:
        机器图(如 1920x1080 整屏画面)与标准库图(如 384x228)尺寸、宽高比往往不同。
        若各自按原图大小送模型，模型会把"留白多少、logo 在画面里的大小和位置"
        当成 logo 本身的差异(圆角/直角、文字比例)，把一致的两个 logo 判成不一致。

    注意: 这里先按四角背景色裁掉纯色边框(黑底/白底都行)，再统一尺寸居中。
        机器图是"整屏黑底 + 中间logo"、标准库图是"白底 + logo"，构图语义不同;
        若只统一尺寸不裁背景，一个仍是完整画面、一个仍是 logo 特写，
        模型会据此判"候选图整体尺寸更大/圆角更大"。
        裁掉各自背景后两张图都只剩 logo 本体，构图才真正可比。
        这里的处理与本地打分的 _prepare_image(只裁白边)不同，是专门为模型准备的。
    """

    try:
        side = max(64, int(max_image_side))
        with Image.open(io.BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGBA")
        image = _trim_uniform_border(image)
        image = _composite_on_white(image).convert("RGB")
        image = ImageOps.contain(image, (side, side), method=Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (side, side), "white")
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=85, optimize=True)
    except (OSError, ValueError) as exc:
        raise QwenReviewError("无法为 Qwen 编码图片") from exc

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_message_content(body: dict[str, Any]) -> str:
    content = body["choices"][0]["message"]["content"]
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [
            item.get("text", "") for item in content if isinstance(item, dict)
        ]
        return "".join(text_parts)
    raise QwenReviewError("Qwen 返回了不支持的消息格式")


def _parse_review(content: str) -> _QwenReviewResult:
    """解析严格 JSON，并容忍被代码围栏包裹的返回。"""

    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").removeprefix("json").strip()

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            raise QwenReviewError("Qwen 返回了非法 JSON") from None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise QwenReviewError("Qwen 返回了非法 JSON") from exc

    if not isinstance(data, dict):
        raise QwenReviewError("Qwen JSON 结果不是对象")
    same_logo = data.get("same_logo", data.get("same", data.get("matched")))
    appearance_consistent = data.get("appearance_consistent", same_logo)
    confidence = data.get("confidence")
    reason = data.get("reason", "")
    if (
        not isinstance(same_logo, bool)
        or not isinstance(appearance_consistent, bool)
        or not isinstance(confidence, (int, float))
    ):
        raise QwenReviewError("Qwen JSON 结果字段非法")
    if not 0 <= float(confidence) <= 1:
        raise QwenReviewError("Qwen confidence 超出 0 到 1 范围")
    return _QwenReviewResult(
        same_logo=same_logo,
        appearance_consistent=appearance_consistent,
        confidence=float(confidence),
        reason=str(reason)[:200],
    )


def _setting_int(env_name: str, config_keys: tuple[str, ...], default: int) -> int:
    """读整数配置：环境变量优先，其次 config.json，最后默认值。"""

    raw = (os.getenv(env_name) or "").strip() or (_config_text(*config_keys) or "")
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _setting_float(
    env_name: str, config_keys: tuple[str, ...], default: float
) -> float:
    """读浮点配置：环境变量优先，其次 config.json，最后默认值。"""

    raw = (os.getenv(env_name) or "").strip() or (_config_text(*config_keys) or "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# 可选命令行入口（方便单文件自测，不影响函数调用）
# --------------------------------------------------------------------------- #
def _main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="比较两张 Logo 图片：本地算法 + 可选的大模型复核。"
    )
    parser.add_argument("reference", help="基准图片路径")
    parser.add_argument("candidate", help="待比对图片路径")
    parser.add_argument(
        "--direct-match-threshold",
        type=float,
        default=None,
        help=(
            "直接判相同的本地得分阈值；默认取环境变量 LOGO_DIRECT_MATCH_THRESHOLD / "
            "config.json 的 logo_direct_match_threshold，未配置为 0.88"
        ),
    )
    parser.add_argument(
        "--fallback-threshold",
        type=float,
        default=None,
        help=(
            "无 Key 或调用失败时的降级判定阈值；默认取环境变量 LOGO_FALLBACK_THRESHOLD / "
            "config.json 的 logo_fallback_threshold，未配置为 0.88"
        ),
    )
    parser.add_argument(
        "--target-size",
        type=int,
        default=None,
        help="本地归一化边长；默认取环境变量 LOGO_TARGET_SIZE / config.json 的 logo_target_size，未配置为 256",
    )
    args = parser.parse_args(argv)

    try:
        result = compare_logo_images(
            args.reference,
            args.candidate,
            direct_match_threshold=args.direct_match_threshold,
            fallback_threshold=args.fallback_threshold,
            target_size=args.target_size,
        )
    except Exception as exc:  # CLI 只输出简洁错误
        print(f"执行失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
