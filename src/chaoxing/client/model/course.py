"""课程与班级模型。

字段别名用 :class:`~pydantic.AliasChoices` 表达"候选字段名"—— 这是 legacy
``ATTR_CANDIDATES`` 那张表的类型安全版本：**同一语义在不同子系统/版本里叫法不同**，
把候选写进类型定义，业务代码就不必再写 ``dict.get()`` 兜底链。
"""

from __future__ import annotations

from pydantic import AliasChoices, Field

from chaoxing.client.model.base import ChaoxingModel


class Course(ChaoxingModel):
    """课程列表里的一门课（学习通按"课程 + 班级"维度给出，二者共同定位）。"""

    course_id: str = Field(
        default="", validation_alias=AliasChoices("courseId", "courseid", "course_id")
    )
    clazz_id: str = Field(
        default="", validation_alias=AliasChoices("clazzId", "clazzid", "clazz_id")
    )
    #: 实测：课程卡片的 personId 就是后续请求要的 cpi
    cpi: str = Field(default="", validation_alias=AliasChoices("personId", "cpi", "personid"))
    title: str = Field(
        default="", validation_alias=AliasChoices("courseName", "course_name", "title")
    )
    teacher: str = ""
    cover: str = ""

    @property
    def key(self) -> str:
        """课程唯一键 ``courseId:clazzId``（与 legacy 的 ``course_key`` 语义一致）。"""
        return f"{self.course_id}:{self.clazz_id}"

    @property
    def is_complete(self) -> bool:
        """是否具备发起后续请求的最小字段集。"""
        return bool(self.course_id and self.clazz_id)


class Chapter(ChaoxingModel):
    """一个章节（学习通叫"知识点"）。

    :cvar chapter_id: 学习通的章节 ID —— 取卡片、算播放顺序都要它
    :cvar index: 章节在页面里的出现顺序（用于推导任务点的全局顺序）
    """

    chapter_id: str = Field(
        default="", validation_alias=AliasChoices("chapterId", "knowledgeId", "id", "chapterid")
    )
    name: str = Field(
        default="", validation_alias=AliasChoices("chapterName", "knowledgeName", "name", "title")
    )
    index: int = 0

    @property
    def usable(self) -> bool:
        """是否可用（ID 是后续所有请求的前提）。"""
        return bool(self.chapter_id)
