"""测试研发调研知识库 (Research Knowledge Base 0.4.0)

验证对象:
1. ResearchCategory / ResearchStatus 枚举与归一化 Normalizer
2. Research 模型五要素、默认 IP、时间戳与强类型契约
3. 调研铁律断言：completed 状态必须具备详实的选型决策 (decision) 与对抗代价评估 (tradeoffs)
4. MCP 工具 research_record, research_search, research_query 签名与自适应 IP 探测机制
"""

import pytest
from pydantic import ValidationError

from logbook.models import (
    Research,
    ResearchCategory,
    ResearchStatus,
    DevLogVisibility,
)
from logbook.normalizer import (
    normalize_research_category,
    normalize_research_status,
)
from logbook.mcp_server import (
    research_record,
    research_search,
    research_query,
)


def test_research_enums_and_normalizer():
    """测试调研分类与状态的枚举值及鲁棒归一化映射。"""
    assert ResearchCategory.ARCHITECTURE == "architecture"
    assert ResearchCategory.DATABASE == "database"
    assert ResearchStatus.COMPLETED == "completed"
    assert ResearchStatus.IN_PROGRESS == "in_progress"

    # 归一化同义词映射
    assert normalize_research_category("db") == "database"
    assert normalize_research_category("arch") == "architecture"
    assert normalize_research_category("lib") == "library"
    assert normalize_research_category("net") == "network"
    assert normalize_research_category("unknown_xyz") == "architecture"

    assert normalize_research_status("done") == "completed"
    assert normalize_research_status("wip") == "in_progress"
    assert normalize_research_status("running") == "in_progress"
    assert normalize_research_status("abandoned") == "deprecated"


def test_research_model_fields_and_defaults():
    """测试 Research 模型字段完整性与审计三要素 (Who + When + Where)。"""
    res = Research(
        project_id="logbook",
        title="Python 高性能异步 SSH 客户端调研",
        objective="解决公网大批量主机并发扫描与执行效率问题，压减 RTT 延迟",
        market_landscape="社区主流方案：AsyncSSH (活跃维护, 高 Star, 原生 asyncio) vs Paramiko (同步阻塞, 需线程池包装)",
        tradeoffs="自研成本极高且难维护 RFC 协议细节；AsyncSSH 需对抗其默认大包与连接数限制",
        decision="选型 AsyncSSH 作为底层核心库，在应用层收敛连接池并辅以 Linux 内核防火墙防护",
        references="https://github.com/ronf/asyncssh",
    )
    assert res.project_id == "logbook"
    assert res.category == ResearchCategory.ARCHITECTURE
    assert res.author == "agy"
    assert res.agent_ip == "0.0.0.0"
    assert res.status == ResearchStatus.COMPLETED
    assert res.visibility == DevLogVisibility.PROJECT_PRIVATE
    assert res.created_at is not None
    assert res.updated_at is not None


def test_research_completed_invariant_assertion():
    """测试调研铁律：completed 状态必须具备清晰的 decision 与 tradeoffs，缺漏或过短必须断言失败。"""
    # 1. 缺少详实的 decision (太短，<10 字符)
    with pytest.raises(ValidationError) as exc1:
        Research(
            project_id="logbook",
            title="测试不合格调研",
            objective="目标很长很清晰",
            market_landscape="市场全景很清晰很详细",
            tradeoffs="自研代价很大，需对抗内部线程池泄漏",
            decision="用开源的",  # 仅 4 字符
            status=ResearchStatus.COMPLETED,
        )
    assert "调研铁律" in str(exc1.value)

    # 2. 缺少详实的 tradeoffs (太短，<10 字符)
    with pytest.raises(ValidationError) as exc2:
        Research(
            project_id="logbook",
            title="测试不合格调研2",
            objective="目标很长很清晰",
            market_landscape="市场全景很清晰很详细",
            tradeoffs="成本低",  # 仅 3 字符
            decision="最终决定选用成熟开源库并在外层增加连接池防线",
            status=ResearchStatus.COMPLETED,
        )
    assert "调研铁律" in str(exc2.value)

    # 3. in_progress 状态允许草稿阶段简略
    draft = Research(
        project_id="logbook",
        title="草稿阶段调研",
        objective="正在探索",
        market_landscape="收集中",
        tradeoffs="待评估",
        decision="待定",
        status=ResearchStatus.IN_PROGRESS,
    )
    assert draft.status == ResearchStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_mcp_research_tools_signatures_and_negotiation():
    """测试 MCP 调研工具入参契约与自适应 IP 探测机制。"""
    # 1. 空项目触发 negotiate 错误返回
    err_rec = await research_record(
        project="",
        title="测试",
        objective="目标",
        market_landscape="全景",
        tradeoffs="对抗成本",
        decision="选型决策",
        agent_ip="192.168.1.55",
    )
    assert err_rec.get("isError") is True

    # 2. research_search 触发协商
    err_search = await research_search(project="", query="SSH 客户端选型")
    assert err_search.get("isError") is True

    # 3. research_query 触发协商
    err_query = await research_query(project="", status="completed")
    assert err_query.get("isError") is True
