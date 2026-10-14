/* 浏览器冒烟测试：用本机 Edge 逐页打开系统，收集控制台错误并截图 */
const path = require('path');
const { chromium } = require('playwright-core');

const BASE = 'http://127.0.0.1:8000';
const ROUTES = ['dashboard', 'reconcile', 'invoice', 'txn', 'counterparty', 'report', 'import', 'logs'];
const OUT = process.env.FINRECON_SHOTS_DIR || 'shots';
require('fs').mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
  });
  const page = await browser.newPage({ viewport: { width: 1480, height: 900 } });

  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push('[console] ' + m.text()); });
  page.on('pageerror', e => errors.push('[pageerror] ' + e.message));

  let failed = 0;
  for (const r of ROUTES) {
    const url = BASE + '/#' + r;
    errors.length = 0;
    try {
      await page.goto(url, { waitUntil: 'load', timeout: 30000 });
      await page.waitForTimeout(2500);
      // 页面是否渲染出侧边栏与内容
      const nav = await page.locator('.nav-item').count();
      const content = await page.locator('.app-content').innerText().catch(() => '');
      const empty = content.includes('页面加载失败');
      const shot = path.join(OUT, r + '.png');
      await page.screenshot({ path: shot, fullPage: false });
      const bad = errors.filter(e => !e.includes('favicon'));
      const status = (nav > 0 && !empty && bad.length === 0) ? 'PASS' : 'FAIL';
      if (status === 'FAIL') failed++;
      console.log(`${status}  #${r}  nav=${nav}  contentLen=${content.trim().length}  errors=${bad.length}`);
      bad.slice(0, 3).forEach(e => console.log('    ' + e.slice(0, 220)));
    } catch (e) {
      failed++;
      console.log(`FAIL  #${r}  ${e.message.slice(0, 180)}`);
    }
  }

  await browser.close();
  console.log(failed === 0 ? '\nALL PAGES PASS' : `\n${failed} PAGE(S) FAILED`);
  process.exit(0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
