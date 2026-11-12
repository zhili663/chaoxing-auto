"""文本归一化与指纹。

题干指纹的唯一用途是**题库缓存命中**：同一道题在不同测验里出现时（同一门课反复
练、不同账号刷同一套题），题干文字可能有细微差异（空白、全角标点），
因此先归一化再哈希 —— 否则缓存形同虚设。

来源：legacy 的 ``QUESTION_HASH_ALGO = "sha1"``（沿用同一算法，旧题库才能直接复用）。
"""

from __future__ import annotations

import hashlib
import re

#: 需要从题干里抹掉的字符（空白与常见全角/半角标点）
_NOISE = re.compile(r"[\s\u3000_（）()【】\[\]{}<>《》,，.。;；:：!！?？'\"“”‘’\-—－~～]+")


def normalize_question(text: str) -> str:
    """归一化题干：去掉空白与标点、统一小写。

    :param text: 原始题干
    :returns: 归一化文本
    """
    return _NOISE.sub("", (text or "").strip().lower())


def question_hash(text: str) -> str:
    """题干指纹（sha1 hex）。

    :param text: 原始题干
    :returns: 40 位十六进制摘要
    """
    return hashlib.sha1(normalize_question(text).encode("utf-8")).hexdigest()


def squish(text: str) -> str:
    """压掉多余空白（用于展示与日志）。"""
    return " ".join((text or "").split())
