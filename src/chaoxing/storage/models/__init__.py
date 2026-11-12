"""ORM 模型汇总。

**这里必须 import 全部模型模块**：Alembic 的 ``--autogenerate`` 只认识已经导入、
注册进 ``Base.metadata`` 的表 —— 漏掉一个模块，迁移里就会少一张表，
而且不会报错（这才是最坑的地方）。
"""

from __future__ import annotations

from chaoxing.storage.models.account import Account, AccountGroup, account_group_members
from chaoxing.storage.models.course import Chapter, Course, TaskPoint
from chaoxing.storage.models.identity import (
    Permission,
    Role,
    User,
    UserSession,
    role_permissions,
    user_roles,
)
from chaoxing.storage.models.obs import ActivityDaily, AuditLog, Job
from chaoxing.storage.models.quiz import AnswerCache, QuizAttempt
from chaoxing.storage.models.settings import Prompt, SettingsOverride
from chaoxing.storage.models.study import (
    RUN_PAUSE_REQUESTED,
    RUN_PAUSED,
    RUN_RESUME_REQUESTED,
    RUN_RUNNING,
    RUN_STATES,
    RUN_STOP_REQUESTED,
    StudyPlan,
    StudyPlanCourse,
    StudyRun,
    StudySession,
    TaskEvent,
)

__all__ = [
    "RUN_PAUSED",
    "RUN_PAUSE_REQUESTED",
    "RUN_RESUME_REQUESTED",
    "RUN_RUNNING",
    "RUN_STATES",
    "RUN_STOP_REQUESTED",
    "Account",
    "AccountGroup",
    "ActivityDaily",
    "AnswerCache",
    "AuditLog",
    "Chapter",
    "Course",
    "Job",
    "Permission",
    "Prompt",
    "QuizAttempt",
    "Role",
    "SettingsOverride",
    "StudyPlan",
    "StudyPlanCourse",
    "StudyRun",
    "StudySession",
    "TaskEvent",
    "TaskPoint",
    "User",
    "UserSession",
    "account_group_members",
    "role_permissions",
    "user_roles",
]
