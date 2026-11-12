"""领域服务层：账号、课程、计划、刷课。

服务之间不互相调用实现细节，编排靠**领域事件**（:mod:`chaoxing.obs.events`）解耦。
"""

from __future__ import annotations

from chaoxing.core.services.accounts import AccountNotFound, AccountService
from chaoxing.core.services.answers import AnswerEngine
from chaoxing.core.services.courses import CourseService, NotLoggedIn, ScanReport
from chaoxing.core.services.jobs import (
    CANCELLABLE,
    JobNotCancellable,
    JobNotFound,
    JobService,
    JobView,
)
from chaoxing.core.services.plans import DEFAULT_STRATEGY, PlanNotFound, PlanService
from chaoxing.core.services.prompts import PROMPT_KEY_SYSTEM, PromptService
from chaoxing.core.services.stats import DailyStat, StatsService, local_day
from chaoxing.core.services.study import (
    PointView,
    StudyReport,
    StudyService,
    StudyUnit,
    plan_units,
)
from chaoxing.core.services.users import BUILTIN_ROLES, UserExists, UserService

__all__ = [
    "BUILTIN_ROLES",
    "CANCELLABLE",
    "DEFAULT_STRATEGY",
    "PROMPT_KEY_SYSTEM",
    "AccountNotFound",
    "AccountService",
    "AnswerEngine",
    "CourseService",
    "DailyStat",
    "JobNotCancellable",
    "JobNotFound",
    "JobService",
    "JobView",
    "NotLoggedIn",
    "PlanNotFound",
    "PlanService",
    "PointView",
    "PromptService",
    "ScanReport",
    "StatsService",
    "StudyReport",
    "StudyService",
    "StudyUnit",
    "UserExists",
    "UserService",
    "local_day",
    "plan_units",
]
