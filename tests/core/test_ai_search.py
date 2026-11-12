"""检索层：DDG 解析、降级路径（超时 / 空结果 / 检索挂了）、提示词渲染。

全程离线 —— respx 拦下 httpx，不需要真的联网，也不消耗真实配额。
重点验收两条不变式：
① 检索命中时片段要进提示词；
② 检索**无论怎么挂**都不能影响作答（降级为纯模型）。
"""

from __future__ import annotations

import httpx
import respx

from chaoxing.ai.prompts import MAX_SNIPPETS, render_question
from chaoxing.ai.search import DDG_ENDPOINT, DuckDuckGoSearch, FakeSearch, Snippet
from chaoxing.client.model.quiz import Answer, Question, QuestionOption, QuestionType
from chaoxing.config import Settings
from chaoxing.core.services.answers import AnswerEngine
from chaoxing.storage.uow import UnitOfWork

#: 两个结果 + 一个没有链接的干扰块（应被忽略）
DDG_HTML = """
<html><body>
  <div class="result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">
      危化品事故特性 - 示例
    </a>
    <a class="result__snippet">易燃性、扩散性、毒害性是危化品事故的主要特性。</a>
  </div>
  <div class="result">
    <a class="result__a" href="https://wiki.example.org/b">百科 - 危化品</a>
    <div class="result__snippet">危化品指具有毒害、腐蚀、爆炸等性质的化学品。</div>
  </div>
  <div class="result"><span>没有链接的干扰块</span></div>
</body></html>
"""

EMPTY_HTML = "<html><body><div class='no-results'>没有找到结果</div></body></html>"


class TrackingProvider:
    """作答替身：记录调用次数与收到的片段。"""

    def __init__(self, value: str = "AB") -> None:
        self.value = value
        self.calls = 0
        self.snippets: list[Snippet] = []

    async def answer(self, question: Question, *, snippets: list[Snippet] | None = None) -> Answer:
        """记一笔并返回一个可用答案。"""
        self.calls += 1
        self.snippets = list(snippets or [])
        return Answer(
            fingerprint=question.fingerprint,
            value=self.value,
            source="model",
            confidence=0.9,
        )


def question(stem: str = "下列属于危化品造成事故的主要特性是（）") -> Question:
    """造一道多选题。"""
    return Question(
        stem=stem,
        qtype=QuestionType.MULTIPLE,
        options=[
            QuestionOption(label="A", text="易燃性"),
            QuestionOption(label="B", text="扩散性"),
        ],
    )


def settings(*, websearch_enabled: bool = True) -> Settings:
    """造一份配置（不读仓库里的 .env）。"""
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="A" * 43 + "=",
        ai={"websearch_enabled": websearch_enabled},
    )


def snippet(text: str = "危化品的主要特性包括易燃性、扩散性与毒害性。") -> Snippet:
    """造一条片段。"""
    return Snippet(title="危化品 - 百科", url="https://baike.example.org/w", text=text)


# ---------------------------------------------------------------------- #
# DDG 解析
# ---------------------------------------------------------------------- #
@respx.mock
async def test_ddg_parses_results_and_unwraps_redirect() -> None:
    """命中解析正确：标题/摘要取出，跳转壳还原成真实 URL。"""
    respx.get(url__startswith="https://html.duckduckgo.com/").mock(
        return_value=httpx.Response(200, text=DDG_HTML)
    )

    results = await DuckDuckGoSearch().search("危化品事故特性", limit=3)

    assert len(results) == 2, "干扰块不该被算成结果"
    assert results[0].title == "危化品事故特性 - 示例"
    assert results[0].url == "https://example.com/a", "uddg 跳转壳没还原"
    assert results[0].domain == "example.com"
    assert "易燃性" in results[0].text
    assert results[1].url == "https://wiki.example.org/b"


@respx.mock
async def test_ddg_honours_limit() -> None:
    """`limit` 是上限：要 1 条就只解析 1 条。"""
    respx.get(url__startswith="https://html.duckduckgo.com/").mock(
        return_value=httpx.Response(200, text=DDG_HTML)
    )

    results = await DuckDuckGoSearch().search("危化品", limit=1)

    assert len(results) == 1


def test_ddg_endpoint_needs_no_key() -> None:
    """选择器/端点写死在这里（唯一实现点），不需要 Key。"""
    assert DDG_ENDPOINT == "https://html.duckduckgo.com/html/"


# ---------------------------------------------------------------------- #
# 提示词渲染
# ---------------------------------------------------------------------- #
def test_render_question_without_snippets_is_plain() -> None:
    """没有片段时渲染结果与原来一致（不多出任何东西）。"""
    rendered = render_question(question())

    assert "网络检索片段" not in rendered
    assert rendered.startswith("【multiple】")


def test_render_question_caps_and_truncates_snippets() -> None:
    """片段最多 3 条、每条截断，并标出来源域名（不整段搬运）。"""
    long_text = "细" * 1000
    snippets = [
        Snippet(title=f"标题{index}", url=f"https://site{index}.example.org/x", text=long_text)
        for index in range(4)
    ]

    rendered = render_question(question(), snippets)

    assert "site0.example.org" in rendered
    assert "site3.example.org" not in rendered, "超过 3 条了"
    assert "细" * 1000 not in rendered, "没有截断原文"
    # 行数 = 题干 + 2 选项 + 说明行 + 3 片段
    assert len(rendered.splitlines()) == 1 + 2 + 1 + MAX_SNIPPETS


# ---------------------------------------------------------------------- #
# 降级路径
# ---------------------------------------------------------------------- #
async def test_search_timeout_degrades_to_plain_model(uow: UnitOfWork) -> None:
    """超时（2s）→ 记日志、继续纯模型作答，答案照常可用。"""
    provider = TrackingProvider()
    with respx.mock:
        respx.get(url__startswith="https://html.duckduckgo.com/").mock(
            side_effect=httpx.ConnectTimeout("检索超时")
        )
        engine = AnswerEngine(
            uow=uow,
            provider=provider,
            search=DuckDuckGoSearch(),
            settings=settings(websearch_enabled=True),
        )
        answer = await engine.answer(question())

    assert provider.calls == 1, "检索超时不能影响模型作答"
    assert provider.snippets == []
    assert answer.usable is True
    assert answer.source == "model"


async def test_empty_results_degrade_to_plain_model(uow: UnitOfWork) -> None:
    """空结果 → 不拼任何片段，模型照常作答。"""
    provider = TrackingProvider()
    with respx.mock:
        respx.get(url__startswith="https://html.duckduckgo.com/").mock(
            return_value=httpx.Response(200, text=EMPTY_HTML)
        )
        engine = AnswerEngine(
            uow=uow,
            provider=provider,
            search=DuckDuckGoSearch(),
            settings=settings(websearch_enabled=True),
        )
        answer = await engine.answer(question())

    assert provider.calls == 1
    assert provider.snippets == []
    assert answer.source == "model"


async def test_search_error_still_answers(uow: UnitOfWork) -> None:
    """检索整个挂了（抛异常）→ 仍然能作答，且 `source` 保持 model。"""
    search = FakeSearch(error=RuntimeError("检索服务不可用"))
    provider = TrackingProvider()
    engine = AnswerEngine(
        uow=uow, provider=provider, search=search, settings=settings(websearch_enabled=True)
    )

    answer = await engine.answer(question())

    assert search.calls, "应该尝试过检索"
    assert provider.calls == 1, "检索挂了模型仍要作答"
    assert provider.snippets == []
    assert answer.source == "model", "检索只辅助，不冒充直接答案"


# ---------------------------------------------------------------------- #
# 开关与命中
# ---------------------------------------------------------------------- #
async def test_snippets_reach_the_model_when_enabled(uow: UnitOfWork) -> None:
    """开启检索且命中 → 片段被拼进提示（模型确实收到了）。"""
    search = FakeSearch([snippet(), snippet("第二条"), snippet("第三条"), snippet("第四条")])
    provider = TrackingProvider()
    engine = AnswerEngine(
        uow=uow, provider=provider, search=search, settings=settings(websearch_enabled=True)
    )

    await engine.answer(question())

    assert search.calls == [("下列属于危化品造成事故的主要特性是（）", 3)]
    assert len(provider.snippets) == 3, "最多只带 3 条"
    assert provider.snippets[0].domain == "baike.example.org"


async def test_websearch_disabled_never_searches(uow: UnitOfWork) -> None:
    """`websearch_enabled=false` → 一次检索都不发，纯模型作答。"""
    search = FakeSearch([snippet()])
    provider = TrackingProvider()
    engine = AnswerEngine(
        uow=uow, provider=provider, search=search, settings=settings(websearch_enabled=False)
    )

    answer = await engine.answer(question())

    assert search.calls == [], "关掉了还去检索了"
    assert provider.calls == 1
    assert provider.snippets == []
    assert answer.source == "model"


async def test_cache_hit_skips_search(uow: UnitOfWork) -> None:
    """缓存命中时根本不检索（钱和延迟都省在这里）。"""
    search = FakeSearch([snippet()])
    provider = TrackingProvider()
    engine = AnswerEngine(
        uow=uow, provider=provider, search=search, settings=settings(websearch_enabled=True)
    )

    await engine.answer(question())
    second = await engine.answer(question())

    assert second.source == "cache"
    assert search.calls == [("下列属于危化品造成事故的主要特性是（）", 3)], "第二次不该再检索"


async def test_fake_search_is_deterministic() -> None:
    """假实现确定性：同样的入参永远同样的出参。"""
    search = FakeSearch([snippet("a"), snippet("b")])

    assert await search.search("q", limit=5) == await search.search("q", limit=5)
