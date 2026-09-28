"""Browser-only UI regression; all backend calls are local mocks."""
# requires-python = "==3.14.*"
from pathlib import Path
from tempfile import gettempdir

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[6]
BUNDLE = Path(gettempdir()) / 'douyin-concepts-ui-044-bundle.js'
STYLES = Path(gettempdir()) / 'douyin-concepts-ui-044-bundle.css'
SCREENSHOT = ROOT / 'tmp/creative-044/mock-concepts-saved.png'


def main() -> None:
    assert BUNDLE.is_file() and STYLES.is_file(), 'run node tests/ui-harness/build-concepts.mjs first'
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.set_default_timeout(8000)
        page.route('**/bundle.js', lambda route: route.fulfill(
            status=200, content_type='text/javascript', body=BUNDLE.read_bytes()))
        page.route('**/bundle.css', lambda route: route.fulfill(
            status=200, content_type='text/css', body=STYLES.read_bytes()))
        page.goto('http://127.0.0.1:49321/index.html')
        expect(page.get_by_role('heading', name='选题草稿')).to_be_visible()
        expect(page.get_by_text('暂无原创选题草稿', exact=False)).to_be_visible()
        fields = {
            '选题名称': '测试角色的修正选择',
            '一句话事件与人物目标': '虚构角色需要完成一个明确的测试目标。',
            '角色自己的选择与代价': '虚构角色改用另一种方式，并承担可见代价。',
            '本条可见回报': '本条展示选择后的结果，并留下可验证的问题。',
            '研究依据与尚未核实处': '机器参考仅为待核观察。',
            '低保真试片要验证的问题': '观众能否说清虚构角色的选择？',
            '设定、素材权利与制作边界': '只使用原创占位素材。',
        }
        for label, value in fields.items():
            page.get_by_label(label, exact=True).fill(value)
        page.get_by_role('button', name='保存选题草稿').click()
        expect(page.get_by_role('heading', name='测试角色的修正选择')).to_be_visible()
        expect(page.get_by_text('选题新版本已保存并回读', exact=False)).to_be_visible()
        page.get_by_role('button', name='追加修订').click()
        page.get_by_label('选题名称', exact=True).fill('测试角色的修正选择 · 修订')
        page.get_by_role('button', name='保存新版本').click()
        expect(page.get_by_role('heading', name='测试角色的修正选择 · 修订')).to_be_visible()
        page.get_by_role('button', name='查看版本历史').click()
        expect(page.get_by_text('v1 · 草稿', exact=False)).to_be_visible()
        assert len(page.evaluate("window.conceptCalls.filter(call => call.action === 'create')")) == 1
        assert len(page.evaluate("window.conceptCalls.filter(call => call.action === 'revise')")) == 1
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        page.locator('#switch-project').click()
        assert page.get_by_role('heading', name='测试角色的修正选择 · 修订').count() == 0
        expect(page.get_by_text('当前为只读成员', exact=False)).to_be_visible()
        expect(page.get_by_role('heading', name='测试角色的修正选择 · 修订')).to_have_count(0)
        expect(page.get_by_role('button', name='保存选题草稿')).to_have_count(0)
        browser.close()
    print(f'creative concept mock UI passed; screenshot: {SCREENSHOT}')


if __name__ == '__main__':
    main()
