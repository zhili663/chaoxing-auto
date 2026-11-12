"""考试端点（默认只作答不交卷，交卷由配置显式开启）。"""

from __future__ import annotations

from chaoxing.client.constants import DOMAIN_MOOC1
from chaoxing.client.endpoints.base import Calibration, Endpoint

EXAM_START = Endpoint(
    name="exam_start",
    url=f"{DOMAIN_MOOC1}/exam-ans/exam/test/reVersionTestStartNew",
    calibration=Calibration.TEMPLATE,
    required_params=("examId",),
    note="开始考试：换取试卷 ID 与试题列表",
)

EXAM_SUBMIT = Endpoint(
    name="exam_submit",
    url=f"{DOMAIN_MOOC1}/exam-ans/exam/test/submit",
    method="POST",
    calibration=Calibration.VOLATILE,
    note="交卷。高频改版接口，默认关闭，仅当配置显式开启时才调用",
)
