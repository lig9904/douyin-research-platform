from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"


def read(relative: str) -> str:
    return (APP / relative).read_text(encoding="utf-8")


def test_platform_icons_are_local_branded_svg_assets() -> None:
    source = read("src/components/PlatformIcon.tsx")

    for platform in (
        "douyin",
        "kuaishou",
        "wechat_channels",
        "xiaohongshu",
        "bilibili",
        "weibo",
    ):
        assert f'"{platform}"' in source

    assert "Simple Icons 16.31.0" in source
    assert '<svg viewBox="0 0 24 24"' in source
    assert "<path d={icon.path}" in source
    assert "aria-label={label}" in source
    assert source.count('role="img"') >= 2
    assert 'aria-hidden="true"' in source
    assert "platform-icon-all" in source
    assert "<img" not in source
    assert "fetch(" not in source


def test_all_platform_surfaces_use_shared_icon_component() -> None:
    surfaces = (
        "App.tsx",
        "VideoLibrary.tsx",
        "AccountLibrary.tsx",
        "HotspotLibrary.tsx",
        "src/components/GlobalSearch.tsx",
        "src/components/OperationsOverview.tsx",
    )

    for surface in surfaces:
        source = read(surface)
        assert "PlatformIcon" in source, surface
        assert "platformGlyph" not in source, surface


def test_navigation_only_exposes_working_pages_and_survives_mobile() -> None:
    shell = read("AppShell.tsx")
    shell_css = read("shell.css")
    index_css = read("index.css")
    app = read("App.tsx")

    assert "navItems.filter((item) => item.enabled && (!allowedViews || allowedViews.has(item.view)))" in shell
    assert "new Set<ResearchView>(['videos', 'briefs', 'accounts'])" in shell
    assert 'aria-label="研究台主导航"' in shell
    assert "label: '运行与成本', view: 'cost', enabled: true" in shell
    assert "成本与预算" not in shell
    assert "运行状态与调用" in app
    assert "运行状态与预算" not in app
    assert "预算使用率" not in app
    assert "budget_usage_pct" not in app
    assert "Progress," not in app
    assert 'activeView="search"' in app
    assert "@media (max-width: 900px)" in shell_css
    assert ".nav { width: 100%" in shell_css
    assert ".sidebar { display: none; }" not in index_css


def test_home_does_not_convert_request_guards_into_budget_percentages() -> None:
    app = read("App.tsx")
    backend = read("backend/get_home_overview.py")

    assert "预算使用率" not in app
    assert "budget_usage_pct" not in app
    assert "Progress," not in app
    assert "budget_usage_pct" not in backend
    assert "from daily_budget" not in backend
