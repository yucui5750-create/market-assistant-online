# 市场观察助手 · 在线版

这是适配电脑和手机的 GitHub Pages 网站。网页不依赖个人电脑开机，Python行情程序在 GitHub Actions 中运行。公开网页只包含公共市场行情和统一价格趋势研究，不包含任何用户的持仓截图、账户金额、盈亏或私人研究报告。

## 功能与边界

- 公开指数/ETF行情、未来7/30/60自然日的条件性趋势观察、来源、数据时间、前瞻预测复盘。
- 持仓只保存于当前浏览器，可导入/导出JSON在自己的设备间手动迁移；**不自动同步，不是登录账户系统**。清理浏览器数据会丢失本机持仓，先导出备份。
- 北京时间12点和16点计划更新网页；GitHub Actions可能延迟或停用，实际更新时间以网页为准。
- “检查最新报告”只获取已发布的报告，不会直接调用云端任务，也不是实时交易行情。
- 这项部署不提供微信/邮箱/手机系统推送。原Codex聊天中的文字投研和提醒仍遵循原日程，须单独确认其启用状态。
- 无法承诺预测准确率90%以上；目前是待验证的价格动量规则，不能作为自动买卖指令。

## 发布

1. 在选定GitHub账号创建新的公开仓库，上传本目录的发布文件。
2. 仓库 Settings → Pages → Source 选择 GitHub Actions。
3. Actions → Refresh market and publish website → Run workflow。
4. 工作流 build、deploy 都完成后，从部署详情打开真实HTTPS网址。

没有完成部署前，本项目不会把猜测的网址称为可用站点。GitHub仓库为私有也不代表Pages页面自动私有。

## 本地检查

```powershell
python -m unittest discover -s tests -v
node tests/test_online.js
python generate.py
python -m http.server 8788 --directory public --bind 127.0.0.1
```

来源为腾讯财经第三方公开接口，可能被限流或缺数据。更新失败时显示错误，保留已有数据的原时间，不伪造即时结果。未来窗口是7/30/60自然日；观察窗口是过去5/20/40完整交易日。预测成绩的起点在登记之后，不计入登记前的已知涨幅。

维护：修改网页文件后提交 main，GitHub Actions会重新构建。只发布 `public/`，不上传本地版 `portfolio.json`、截图和个性化报告。公开预测记录保存在 `data/forecasts.json`，不要放入个人持仓。

官方说明：[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[工作流发布](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、[计划任务限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。
