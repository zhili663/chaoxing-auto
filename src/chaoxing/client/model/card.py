"""任务点卡片模型（章节页里内嵌的 job / attachments 结构）。

来源：``tests/fixtures/job_card_video.json``（真实抓包 cards_1192366344.html）。

两个必须处理的真实结构特征：

1. **``_jobid`` 藏在 ``property`` 里**（嵌套一层），顶层没有 —— legacy 因此丢过
   整个视频任务点的 jobid，导致心跳永远不被服务端确认；
2. **同一份课件会在 attachments 里出现两次**（job 卡片带令牌、附件卡片带文件名与
   页数），二者 ``jobid`` 相同，必须归并，否则同一任务被处理两次。
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, Field, field_validator

from chaoxing.client.model.base import ChaoxingModel

#: 卡片 ``type`` → 任务点种类（后续学习通新增类型只需扩展这张表）
KIND_ALIASES: dict[str, str] = {
    "video": "video",
    "audio": "video",
    "mov": "video",
    "mp4": "video",
    "document": "document",
    "pdf": "document",
    "ppt": "document",
    "pptx": "document",
    "doc": "document",
    "docx": "document",
    "work": "quiz",
    "quiz": "quiz",
    "homework": "quiz",
    "exam": "exam",
    "test": "exam",
    "read": "read",
    "discussion": "discussion",
    "live": "live",
}


class Attachment(ChaoxingModel):
    """任务点附件（视频 / 文档 / 测验都挂在 ``attachments`` 下）。"""

    type: str = ""
    #: 视频心跳与视频元信息接口都靠它
    object_id: str = Field(default="", validation_alias=AliasChoices("objectId", "objectid"))
    #: 任务点 ID（可能来自顶层，也可能来自摊平后的 ``property._jobid``）
    job_id: str = Field(
        default="", validation_alias=AliasChoices("jobid", "jobId", "_jobid", "job_id")
    )
    #: 任务点显示名。真机实测（2026-10-12）：「阅读/文档」类卡片把标签放在
    #: ``property.title``（如「文学与人生」），只认 ``name``/``filename`` 会一路落成空串 ——
    #: 而落库取的就是这个字段（``_store_points`` 的 ``title=attachment.name``）。
    name: str = Field(default="", validation_alias=AliasChoices("name", "filename", "title"))
    #: 「阅读 / 专题阅读」卡片的完成令牌（真机实测 2026-10-12）：阅读器按 ``property.mid``
    #: 从附件里取它，再调 ``/mooc-ans/coursedata/readjobv2/show`` 上报完成。
    #: 它和 ``jtoken`` 是两条并行的令牌线（文档卡用 jtoken、阅读卡用 enc）——
    #: 模型里没有这个字段时会被 pydantic 丢掉，落库就永远是空。
    enc: str = Field(default="", validation_alias=AliasChoices("enc", "utEnc"))
    #: 实测：视频真实时长在 ``attDuration``（秒）；老版用 ``duration``
    att_duration: float = Field(
        default=0.0,
        validation_alias=AliasChoices("attDuration", "attduration", "duration", "videoduration"),
    )
    att_duration_enc: str = Field(
        default="", validation_alias=AliasChoices("attDurationEnc", "attdurationenc")
    )
    #: ★ 心跳的核心参数：``nodeId_xx-cpi_xx-rt_d-...-enc_<hash>``
    #: **不带**页面里多挂的 ``&courseId=xx``（见下面的校验器）。
    other_info: str = Field(default="", validation_alias=AliasChoices("otherInfo", "otherinfo"))

    @field_validator("other_info", mode="after")
    @classmethod
    def _strip_page_suffix(cls, value: str) -> str:
        """截掉 ``&courseId=<id>`` 尾巴。

        页面 JSON 里的 otherInfo 形如 ``…-enc_<hash>&courseId=<id>``，而浏览器真正
        发出的心跳里**到 ``-enc_<hash>`` 为止**（``courseId`` 是独立查询参数）。
        实测（2026-10-15）：照页面原样带上它必定 403，同一条请求去掉它立刻 200。
        """
        return value.split("&", 1)[0].strip()

    #: ★ 测验 / 作业的唯一标识（**十六进制串，不是数字**）。
    #: 别拿 ``aid`` / ``mid`` 顶替：``mid`` 是"模块 ID"，长得像数字，
    #: 用它去换票只会拿到一张 HTML 错误页。
    work_id: str = Field(default="", validation_alias=AliasChoices("workid", "workId"))
    #: 作业类型（``workA`` 之类），排障时有用
    work_type: str = Field(default="", validation_alias=AliasChoices("worktype", "workType"))
    #: 有人脸抓拍校验时原样透传
    face_capture_enc: str = Field(
        default="",
        validation_alias=AliasChoices(
            "videoFaceCaptureEnc",
        ),
    )
    #: 文档 / PPT 类任务点独有令牌（相当于视频的 dtoken）
    jtoken: str = ""
    #: 服务端是否已认这个任务点完成
    is_passed: bool = Field(default=False, validation_alias=AliasChoices("isPassed", "ispassed"))
    #: 已上报的播放位置（毫秒；卡片里是毫秒，接口要秒 —— 换算在 core 层做）
    play_time: float = Field(default=0.0, validation_alias=AliasChoices("playTime", "playtime"))
    mid: str = ""
    aid: str = ""
    #: 原始嵌套属性（摊平后仍保留，便于排障与二次取字段）
    property_: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "property",
        ),
    )

    @property
    def identity(self) -> str:
        """归并用的身份键：优先 jobid，退化到 objectId。"""
        return self.job_id or self.object_id

    @property
    def kind(self) -> str:
        """任务点种类。

        先按 ``type`` 查表（实测取值形如 ``video``、``.mov``、``work``），
        查不到时用"有没有令牌"兜底：有 objectId 就是视频，有 jtoken 就是文档 ——
        宁可归到一个可执行的分支，也不要让任务点停在 ``unknown`` 上被跳过。

        :returns: ``video`` / ``document`` / ``quiz`` / … / ``unknown``
        """
        key = (self.type or "").strip().lower().lstrip(".")
        if key in KIND_ALIASES:
            return KIND_ALIASES[key]
        if self.object_id:
            return "video"
        if self.jtoken:
            return "document"
        return "unknown"


class CardDefaults(ChaoxingModel):
    """卡片页 ``defaults`` 里的会话级参数（心跳与提交都要用到）。"""

    fid: str = ""
    ktoken: str = ""
    userid: str = Field(default="", validation_alias=AliasChoices("userid", "userId"))
    #: 实测：卡片里的 cpi 是数字，接口要字符串，统一按字符串承载
    cpi: str = Field(default="", validation_alias=AliasChoices("cpi", "personId"))
    clazz_id: str = Field(default="", validation_alias=AliasChoices("clazzId", "clazzid"))
    knowledge_id: str = Field(
        default="", validation_alias=AliasChoices("knowledgeid", "knowledgeId")
    )
    #: 卡片签发的签名材料（心跳/提交都要原样回传）
    defenc: str = ""
    card_id: str = Field(default="", validation_alias=AliasChoices("cardid", "cardId"))
    #: 上报地址模板（形如 ``.../multimedia/log/a/{cpi}``）
    report_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "reportUrl",
        ),
    )
    initdata_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "initdataUrl",
        ),
    )


class JobCard(ChaoxingModel):
    """章节卡片页里内嵌的一个任务点单元。"""

    course_name: str = Field(default="", validation_alias=AliasChoices("coursename", "courseName"))
    knowledge_name: str = Field(
        default="", validation_alias=AliasChoices("knowledgename", "knowledgeName")
    )
    attachments: list[Attachment] = Field(default_factory=list)
    defaults: CardDefaults = Field(default_factory=CardDefaults)
    ut: str = ""

    @property
    def primary(self) -> Attachment | None:
        """取第一个带可识别身份的任务点附件（多附件时按归并结果看）。"""
        for item in self.attachments:
            if item.identity:
                return item
        return self.attachments[0] if self.attachments else None
