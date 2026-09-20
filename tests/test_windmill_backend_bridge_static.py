from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"


def test_full_code_app_has_one_virtual_windmill_backend_import() -> None:
    imports: list[tuple[Path, str]] = []
    pattern = re.compile(r"from\s+['\"]([^'\"]*wmill)['\"]")

    for source_path in sorted(APP.rglob("*.ts*")):
        source = source_path.read_text(encoding="utf-8")
        imports.extend((source_path, match.group(1)) for match in pattern.finditer(source))

    assert imports == [(APP / "backend.ts", "./wmill")]


def test_backend_consumers_use_the_shared_bridge() -> None:
    consumers = {
        "App.tsx": "./backend",
        "VideoLibrary.tsx": "./backend",
        "AccountLibrary.tsx": "./backend",
        "HotspotLibrary.tsx": "./backend",
        "src/components/GlobalSearch.tsx": "../../backend",
        "src/components/MetricTimeline.tsx": "../../backend",
        "src/components/L3ReviewPanel.tsx": "../../backend",
        "src/components/OperationsOverview.tsx": "../../backend",
        "src/components/ResearchActions.ts": "../../backend",
    }

    for relative_path, import_path in consumers.items():
        source = (APP / relative_path).read_text(encoding="utf-8")
        assert f"from '{import_path}'" in source
