"""pytest 共用設定:自動標記慢測試。

依測試名稱樣式自動掛 `slow` marker(不必逐一改 test 檔——新增的網格掃描/
對抗式全掃測試只要名字含這些關鍵字,就會自動歸入慢層):
- CI 快層(push/PR)跑 `pytest -m "not slow"`;
- 慢層(對抗網格掃描)每週排程 + 手動觸發跑全套。

也可以直接在測試函數上手動加 `@pytest.mark.slow`,效果相同。
"""
import pytest

# 名稱含以下任一子字串的測試自動視為 slow(目前:極端網格全掃 ~76s)
_SLOW_NAME_PATTERNS = ("extreme_grid", "grid_scan", "full_sweep")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if any(pat in item.name for pat in _SLOW_NAME_PATTERNS):
            item.add_marker(pytest.mark.slow)
