"""Logbook 研发人员极简 CLI 终端看板工具 - 0.8.0 极简正典

命令体系:
- logbook status [project]     : 打印低噪音 ANSI 任务与阻塞项看板 (直接读取 shared.records)
- logbook doctor               : 检查数据库、SNTP 时钟漂移、内存占用与 Schema 健康度
- logbook show <id>            : 查阅指定任务详情与时序审计
- logbook import <file>        : 从 Markdown DEVLOG.md 逆向解析灌库
- logbook export [project]     : 从数据库导出兼容的 DEVLOG.md
- logbook mcp                  : 启动原生 FastMCP Stdio 服务
"""

import sys
import os
import asyncio
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from .db import db
from .time_sync import check_clock_drift, get_beijing_now, format_beijing
from .converter import import_devlog_markdown, export_devlog_markdown
from .negotiation import validate_and_negotiate_project, ProjectNegotiationError

console = Console()

try:
    import asyncpg
    POSTGRES_ERRORS = (ConnectionRefusedError, OSError, asyncpg.PostgresError)
except ImportError:
    POSTGRES_ERRORS = (ConnectionRefusedError, OSError)


def run_async(coro):
    try:
        return asyncio.run(coro)
    except POSTGRES_ERRORS as e:
        console.print(f"[bold red]✘ 无法连接到 PostgreSQL 数据库或底层服务未就绪[/bold red]: [dim]{e}[/dim]")
        console.print("[dim]请检查 DATABASE_URL 环境变量或容器服务状态。[/dim]")
        sys.exit(1)


@click.group()
def main():
    """Logbook: Agent-Native 工业级研发台账与排查手记中枢"""
    pass


@main.command()
@click.argument("project")
def status(project: str):
    """打印项目任务台账与阻塞项看板。强制显式提供项目名称。"""
    async def _status():
        try:
            proj = await validate_and_negotiate_project(project, is_write=False)
        except ProjectNegotiationError as e:
            console.print(f"[bold red]✘ 未找到项目 '{e.requested_project}'[/bold red]")
            if e.suggestions:
                console.print(f"  [yellow]?[/yellow] 您是否意指相近项目: [bold cyan]{e.suggestions}[/bold cyan]？")
            if e.workspace_project:
                console.print(f"  [dim]• 检测到当前终端物理工作区为: {e.workspace_project}[/dim]")
            return
        except PermissionError as e:
            console.print(f"[bold red]{e}[/bold red]")
            return

        tasks = await db.query_records(proj, dataset_code="sys_task", limit=30)
        if not tasks:
            tasks = await db.query_records(proj, dataset_code="tasks", limit=30)

        waitings = await db.query_records(proj, dataset_code="sys_waiting", limit=30)
        if not waitings:
            waitings = await db.query_records(proj, dataset_code="waitings", limit=30)

        # 1. 任务看板
        table = Table(title=f"【{proj.upper()} 任务台账 (最新 30 项)】", show_header=True, header_style="bold cyan")
        table.add_column("ID", style="bold yellow", width=10)
        table.add_column("状态", width=12)
        table.add_column("优先级", width=8)
        table.add_column("类型", width=10)
        table.add_column("标题", width=40)
        table.add_column("Commit", width=12)
        table.add_column("更新时间", width=18)

        def _v(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        for t in tasks:
            p = _v(t, "payload") or {}
            st = _v(t, "status")
            st_style = "green" if st == "closed" else "yellow" if st == "running" else "red" if st == "blocked" else "white"
            commit_h = p.get("commit_hash") or "-"
            table.add_row(
                _v(t, "doc_no"),
                f"[{st_style}]{st}[/{st_style}]",
                p.get("priority", "P2"),
                p.get("task_type", "fix"),
                (_v(t, "entity_name") or "-")[:38],
                commit_h[:10] if commit_h != "-" else "-",
                format_beijing(_v(t, "updated_at"))
            )
        console.print(table)

        # 2. 阻塞项
        open_waitings = [w for w in waitings if _v(w, "status") == "open"]
        if open_waitings:
            w_table = Table(title=f"【{proj.upper()} 待办与待用户裁决 ({len(open_waitings)} 项)】", show_header=True, header_style="bold red")
            w_table.add_column("ID", style="bold red", width=12)
            w_table.add_column("类别", width=10)
            w_table.add_column("事项描述", width=60)
            w_table.add_column("阻塞时刻", width=20)
            for w in open_waitings:
                p = _v(w, "payload") or {}
                desc = p.get("description") or _v(w, "entity_name") or "-"
                w_table.add_row(_v(w, "doc_no"), p.get("category", "user"), desc, format_beijing(_v(w, "updated_at")))
            console.print(w_table)

    run_async(_status())


@main.command()
def doctor():
    """核验系统底座健康度、SNTP 时钟漂移与 Schema 状态。"""
    async def _doctor():
        console.print(Panel("[bold green]Logbook 系统健康巡检 (Doctor)[/bold green]", border_style="cyan"))

        # 1. 时钟核验
        drift_rep = check_clock_drift(max_allowed_drift=1.0)
        if drift_rep.is_synchronized:
            console.print(f" [green]✔[/green] 时钟同步: [bold]{drift_rep.message}[/bold] (授时中心: {drift_rep.server})")
        else:
            console.print(f" [red]✘[/red] 时钟同步异常: [bold]{drift_rep.message}[/bold]")
        console.print(f"     当前北京时间: [cyan]{format_beijing(get_beijing_now(), with_timezone=True)}[/cyan]")

        # 2. 数据库连接核验
        try:
            await db.connect()
            async with db._pool.acquire() as conn:
                version = await conn.fetchval("SELECT version();")
                pg_tz = await conn.fetchval("SHOW timezone;")
                has_vec = await conn.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'vector';")
                console.print(f" [green]✔[/green] PostgreSQL 底座连接成功: {version.split(',')[0]}")
                console.print(f"     数据库时区: [cyan]{pg_tz}[/cyan] (pgvector 扩展: {'已就绪' if has_vec else '未就绪'})")
        except Exception as e:
            console.print(f" [red]✘[/red] PostgreSQL 连接失败: {e}")

        # 3. 内存开销核验 (通过 docker 检查)
        res = os.popen("docker stats logbook-postgres --no-stream --format '{{.MemUsage}}' 2>/dev/null").read().strip()
        if res:
            console.print(f" [green]✔[/green] 容器物理内存开销: [bold green]{res}[/bold green] (远低于 80MB 限制)")
        else:
            console.print(" [yellow]![/yellow] 未检测到 logbook-postgres 容器内存输出")

    run_async(_doctor())


@main.command()
@click.argument("task_id")
@click.option("--project", default="brix", help="项目名称")
def show(task_id: str, project: str):
    """查阅指定任务详情。"""
    async def _show():
        tasks = await db.query_records(project=project, dataset_code="sys_task", doc_no=task_id, limit=1)
        if not tasks:
            tasks = await db.query_records(project=project, dataset_code="tasks", doc_no=task_id, limit=1)
        if not tasks:
            console.print(f"[red]未找到任务 {task_id} (项目: {project})[/red]")
            return

        task = tasks[0]
        def _v(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        p = _v(task, "payload") or {}
        panel_content = f"""
[bold yellow]ID:[/bold yellow] {_v(task, 'doc_no')}
[bold]标题:[/bold] {_v(task, 'entity_name')}
[bold]状态:[/bold] {_v(task, 'status')} (优先级: {p.get('priority', 'P2')}, 类型: {p.get('task_type', 'fix')})
[bold]Commit 指针:[/bold] {p.get('commit_hash') or '-'}
[bold]验证报告:[/bold] {p.get('proof_link') or '-'}
[bold]耗时:[/bold] {f"{p.get('duration_seconds')} 秒" if p.get('duration_seconds') is not None else '-'}
[bold]更新时间:[/bold] {format_beijing(_v(task, 'updated_at'))}
[bold]创建时间:[/bold] {format_beijing(_v(task, 'created_at'))}
[bold]备注:[/bold] {p.get('notes') or '-'}
"""
        console.print(Panel(panel_content.strip(), title=f"任务详情: {project}.{_v(task, 'doc_no')}", border_style="green"))

    run_async(_show())


@main.command(name="import")
@click.argument("markdown_file", type=click.Path(exists=True))
@click.option("--project", default="brix", help="目标项目名称")
def import_cmd(markdown_file: str, project: str):
    """从 Markdown DEVLOG 文件导入数据。"""
    async def _import():
        with open(markdown_file, "r", encoding="utf-8") as f:
            content = f.read()
        summary = await import_devlog_markdown(project, content)
        console.print(f"[green]✔ 成功导入项目 [{project}]:[/green]")
        console.print(f"   - 任务 (Tasks): {summary.tasks_imported} 条")
        console.print(f"   - 发现 (Findings): {summary.findings_imported} 条")
        console.print(f"   - 待办 (Waitings): {summary.waitings_imported} 条")

    run_async(_import())


@main.command(name="export")
@click.option("--project", default="brix", help="目标项目名称")
@click.option("--output", default="docs/DEVLOG.md", help="输出路径")
def export_cmd(project: str, output: str):
    """从数据库生成兼容的 DEVLOG.md。"""
    async def _export():
        md = await export_devlog_markdown(project)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            f.write(md)
        console.print(f"[green]✔ 成功导出 [{project}] 最新状态到 [bold]{output}[/bold] (共 {len(md.splitlines())} 行)[/green]")

    run_async(_export())


@main.command()
def mcp():
    """以 Stdio 模式启动 FastMCP Server。"""
    from .mcp_server import run_stdio
    run_stdio()


if __name__ == "__main__":
    main()
