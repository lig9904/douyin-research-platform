"""Local browser-only regression for the project LAS review controls.

The fixture never calls a real supplier, Windmill, or project database.
"""
# requires-python = "==3.14.*"
from pathlib import Path
import subprocess
from tempfile import gettempdir

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[6]
BUNDLE = Path(gettempdir()) / 'douyin-las-ui-041-bundle.js'
STYLES = Path(gettempdir()) / 'douyin-las-ui-041-bundle.css'
VIDEO = Path(gettempdir()) / 'douyin-las-ui-041-fixture.mp4'
SCREENSHOT = ROOT / 'tmp/las-041/mock-review-played.png'


def main() -> None:
    assert BUNDLE.is_file() and STYLES.is_file(), 'run node tests/ui-harness/build.mjs first'
    if not VIDEO.is_file():
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=24',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100',
            '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-movflags', '+faststart', '-y', str(VIDEO),
        ], check=True)
    SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel='chrome', headless=True,
            args=['--autoplay-policy=no-user-gesture-required'],
        )
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.set_default_timeout(5000)
        page.route('**/bundle.js', lambda route: route.fulfill(
            status=200, content_type='text/javascript', body=BUNDLE.read_bytes(),
        ))
        page.route('**/bundle.css', lambda route: route.fulfill(
            status=200, content_type='text/css', body=STYLES.read_bytes(),
        ))
        media = {'fail': False}

        def serve_video(route) -> None:
            if media['fail']:
                route.abort('failed')
            else:
                route.fulfill(status=200, content_type='video/mp4', body=VIDEO.read_bytes(),
                              headers={'accept-ranges': 'bytes'})

        page.route('https://example.test/las-review-fixture.mp4', serve_video)
        page.route('https://example.test/project-preview-fixture.mp4', serve_video)
        page.goto('http://127.0.0.1:49321/index.html')
        page.get_by_role('button', name='定位原片 0:01').click()
        project_video = page.get_by_label('本项目已入库视频播放器')
        expect(project_video).to_be_visible()
        page.wait_for_function('() => { const video = document.querySelector(".private-video-preview video"); return video && video.currentTime >= 0.9 && video.currentTime <= 1.1 && video.paused }')
        expect(page.get_by_text('已定位到 0:01', exact=False)).to_be_visible()
        page.get_by_role('button', name='定位原片 0:09').click()
        expect(page.get_by_text('机器时间码超出原片长度', exact=False)).to_be_visible()
        expect(page.get_by_role('heading', name='本项目整片音画分析')).to_be_visible()
        page.get_by_role('button', name='加载本版本视频').click()
        page.get_by_role('button', name='核看这份视频').click()
        video = page.get_by_label('本项目待审核完整视频')
        expect(video).to_be_visible()
        checkbox = page.get_by_role('checkbox', name='我已核对这份完整视频并同意交由火山 LAS 进行音画分析')
        expect(checkbox).to_be_disabled()
        expect(page.get_by_role('button', name='保存整片审核')).to_be_disabled()
        video.evaluate('(element) => element.play()')
        expect(checkbox).to_be_enabled()
        expect(page.get_by_text('请人工核看完整画面和声音', exact=False)).to_be_visible()
        page.screenshot(path=str(SCREENSHOT), full_page=True)
        checkbox.check()
        page.get_by_role('button', name='保存整片审核').click()
        expect(page.get_by_text('本项目整片审核已保存', exact=False)).to_be_visible()
        approval = page.evaluate("window.lasCalls.find(call => call.action === 'approve')")
        assert approval['review_version'] == 'las-video-v2'
        assert approval['object_version_id'] == 'fixture-object-version-2'
        assert approval['expected_manifest_fingerprint'] == 'c' * 64
        assert approval['expected_manifest_fingerprint'] != 'b' * 64

        page.get_by_role('button', name='启动一次分析').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        # Route changes can happen outside the modal even though its overlay
        # blocks pointer clicks within this small harness.
        page.get_by_role('button', name='切换项目').evaluate('(button) => button.click()')
        expect(page.get_by_label('本项目已入库视频播放器')).to_have_count(0)
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'prepare').length") == 0
        page.get_by_role('button', name='切换项目').click()
        page.get_by_role('button', name='加载本版本视频').click()
        expect(page.get_by_role('button', name='启动一次分析')).to_be_visible()
        page.get_by_role('button', name='启动一次分析').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        page.locator('#project-las-review-version').fill('las-video-v1')
        expect(page.get_by_role('dialog')).to_have_count(0)
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'prepare').length") == 0
        page.get_by_role('button', name='加载本版本视频').click()
        expect(page.get_by_text('历史 v1 审核')).to_be_visible()
        expect(page.get_by_role('button', name='启动一次分析')).to_have_count(0)
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'prepare').length") == 0
        page.locator('#project-las-review-version').fill('las-video-v2')
        page.get_by_role('button', name='加载本版本视频').click()
        page.get_by_role('button', name='启动一次分析').click()
        page.get_by_role('button', name='确认启动').click()
        expect(page.get_by_text('付费分析请求已持久登记', exact=False)).to_be_visible()
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'prepare').length") == 1

        page.get_by_role('button', name='撤销审核').click()
        expect(page.get_by_text('审核已撤销', exact=False)).to_be_visible()
        page.locator('input').fill('las-video-v2')
        expect(page.get_by_label('本项目待审核完整视频')).to_have_count(0)
        expect(page.get_by_role('button', name='启动一次分析')).to_have_count(0)
        page.evaluate("window.lasPlaybackMalformed = true")
        page.get_by_role('button', name='加载本版本视频').click()
        page.get_by_role('button', name='核看这份视频').click()
        expect(page.get_by_text('这份视频无法播放或资产已变化', exact=False)).to_be_visible()
        expect(page.get_by_label('本项目待审核完整视频')).to_have_count(0)
        page.evaluate("window.lasPlaybackMalformed = false")
        media['fail'] = True
        page.get_by_role('button', name='加载本版本视频').click()
        page.get_by_role('button', name='核看这份视频').click()
        expect(page.get_by_text('视频播放失败或链接过期', exact=False)).to_be_visible()
        expect(page.get_by_role('checkbox', name='我已核对这份完整视频并同意交由火山 LAS 进行音画分析')).to_be_disabled()
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'approve').length") == 1
        assert page.evaluate("window.lasCalls.filter(call => call.action === 'revoke').length") == 1

        page.evaluate("window.lasDelayOldStatus = true; window.lasShowStatusByProject = true")
        prior_status_count = page.evaluate("window.lasCalls.filter(call => call.action === 'status').length")
        page.locator('.las-review-section .detail-section-head button').click()
        page.wait_for_function(
            '(count) => window.lasCalls.filter(call => call.action === "status").length > count',
            arg=prior_status_count,
        )
        page.get_by_role('button', name='切换项目').click()
        expect(page.get_by_text('任务 bbbbbbbb')).to_be_visible()
        page.wait_for_timeout(500)
        expect(page.get_by_text('任务 aaaaaaaa')).to_have_count(0)
        browser.close()
    print(f'LAS mock UI interaction passed; screenshot: {SCREENSHOT}')


if __name__ == '__main__':
    main()
