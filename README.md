# Apple Pay 招聘岗位监测

每天自动检查 [Apple 招聘官网](https://jobs.apple.com/zh-cn/search?location=china-CHNC&product=apple-pay-APPAY) 上 **中国大陆 · Apple Pay 产品线** 的岗位。一旦出现新岗位，就往你的邮箱发一封邮件，里面包含：

- 岗位链接（点开直接投递）
- 岗位名称、团队、工作地点、发布日期、职位编号
- **岗位简介 / 主要职责 / 任职要求（必备）/ 加分项**，全部取自官网原文并翻译成中文

已经通知过的岗位不会重复打扰你；官网没变化的日子，不会收到任何邮件。

## 它是怎么拿到数据的

Apple 招聘站是服务端渲染的，页面里内嵌了一段 `window.__staticRouterHydrationData`，搜索结果和岗位详情的结构化数据都在里面。程序直接读这段 JSON，所以拿到的是官网原文字段（`description` / `responsibilities` / `minimumQualifications` / `preferredQualifications`），而不是从 HTML 里猜出来的文本。

整个流程：

```
抓搜索页(含翻页) → 和本地快照比对出新岗位 → 逐个抓详情页 → 翻译成中文 → 渲染邮件 → SMTP 发送 → 更新快照
```

邮件发送成功之后才更新快照。万一某天发信失败，这些岗位下次还会被当成"新增"重发，不会被漏掉。

## 快速开始

需要 Python 3.10+，**没有任何第三方依赖**，clone 下来就能跑。

```bash
# 1. 看看官网现在有哪些岗位（不发邮件、不写状态）
PYTHONPATH=src python3 -m applepay_watch list

# 2. 生成一封邮件预览，用浏览器打开 out/ 里的 .html 看效果
PYTHONPATH=src python3 -m applepay_watch preview
```

配置发信：

```bash
cp .env.example .env
# 编辑 .env，至少填 SMTP_USERNAME 和 SMTP_PASSWORD
```

然后先跑一次建立基线，再让它每天自动跑：

```bash
PYTHONPATH=src python3 -m applepay_watch seed   # 把现有岗位记为"已知"
PYTHONPATH=src python3 -m applepay_watch run    # 之后每天执行这一条
```

> 首次运行如果还没建立过基线，程序会自动把当前岗位记为基线且**不发邮件**——否则你第一次就会收到一封把现有岗位全列一遍的邮件。想收到这封"存量岗位汇总"，在 `.env` 里设 `NOTIFY_ON_FIRST_RUN=true`。

## 配置发信邮箱

### Gmail（默认）

Gmail 不接受账号密码直连，必须用**应用专用密码**。而应用专用密码又要求账号**先开启两步验证**，顺序不能反：

1. 先开启[两步验证](https://myaccount.google.com/signinoptions/twosv)，确认状态显示为「已开启」
   （只绑了手机号但没完成短信确认的"待处理"状态不算，后面照样拿不到密码）
2. 再进入[应用专用密码](https://myaccount.google.com/apppasswords)，创建一个，名字随便填
3. 把生成的 16 位密码（去掉空格）填进 `.env` 的 `SMTP_PASSWORD`

> 如果第 2 步提示 **「您的账号不支持您正在尝试的设置」**，说明第 1 步还没真正生效，回去把两步验证开完。
> 详见下面的[常见问题](#常见问题)。

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=你的Gmail地址@gmail.com
SMTP_PASSWORD=abcdefghijklmnop
MAIL_TO=你的邮箱@gmail.com
```

> `MAIL_TO` 必填，代码里没有内置默认收件人——本仓库是公开的，邮箱地址写进源码会被爬虫抓去发垃圾邮件。

### QQ / 163 邮箱

在邮箱设置里开启 SMTP 服务并获取「授权码」，然后：

```env
SMTP_HOST=smtp.qq.com      # 163 用 smtp.163.com
SMTP_PORT=465
SMTP_SSL=true
SMTP_STARTTLS=false
SMTP_USERNAME=你的QQ号@qq.com
SMTP_PASSWORD=授权码
```

**没配 SMTP 也能用**：程序会自动降级，把邮件写到 `out/` 目录（`.html` / `.txt` / `.eml` 三种格式），你可以先看效果再决定要不要配。

配完之后先验证一下，不用等到真有新岗位才发现配错了：

```bash
PYTHONPATH=src python3 -m applepay_watch mailtest          # 只测能不能登录
PYTHONPATH=src python3 -m applepay_watch mailtest --send   # 再发一封测试邮件
```

登录失败时程序会直接告诉你该去哪里改，而不是甩一串 SMTP 错误码。

## 翻译

默认 `TRANSLATOR=auto`，按这个顺序找可用的后端，某个挂了自动切下一个：

| 后端 | 需要 key | 说明 |
| --- | --- | --- |
| `llm` | `LLM_API_KEY` | 任意 OpenAI 兼容接口，翻译质量最好，推荐 |
| `deepl` | `DEEPL_API_KEY` | DeepL 免费额度够用（key 以 `:fx` 结尾会自动走 free 域名）|
| `google` | 不需要 | Google 翻译网页接口，**默认就能用**，可能被限流 |
| `mymemory` | 不需要 | 免费兜底，每日额度很小 |
| `none` | — | 全都不可用时保留英文原文，并在邮件里说明 |

想用大模型翻译（中文更自然、专有名词处理更好），在 `.env` 里填一组即可：

```env
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1    # 或 OpenAI / 通义千问 / Moonshot
LLM_MODEL=deepseek-chat
```

译文会缓存在 `.cache/translations.json`，同一段文字不会重复花钱或重复请求。Apple 本身给出中文文案的岗位会直接用官方中文，不再机器翻译。

## 每天自动运行

### 方式一：GitHub Actions（推荐，不需要自己的电脑一直开着）

仓库里已经带了 [`.github/workflows/daily-watch.yml`](.github/workflows/daily-watch.yml)，每天北京时间 09:00 跑一次。把代码推到 GitHub 后：

1. 仓库 **Settings → Secrets and variables → Actions**
2. **Secrets** 里加 `SMTP_USERNAME`、`SMTP_PASSWORD`（有大模型 key 就再加 `LLM_API_KEY`）
3. **Variables** 里加 `MAIL_TO`（以及可选的 `SMTP_HOST`、`LLM_BASE_URL`、`LLM_MODEL` 等）
4. 在 Actions 页面手动跑一次 `workflow_dispatch` 验证；勾上 `force` 可以强制发一封测试邮件

`MAIL_TO` 填在 Secrets 里也能跑（workflow 两边都会读），不用纠结放哪。但**必须填**——workflow 用的是 `run --require-mail`，配置不全会直接让这次运行失败并在日志里说明缺什么，而不是悄悄把邮件写成文件让你以为一切正常。

岗位快照 `state/seen_jobs.json` 会由 workflow 自动提交回仓库，作为下次比对的依据。这个文件每天都会更新（哪怕岗位没变，`last_run` 时间戳也会变），所以仓库每天都有一次提交——这是刻意的：**GitHub 会在公开仓库连续 60 天没有活动后自动停用定时 workflow**，每日提交正好让它一直保持活跃。

### 方式二：本机 crontab（macOS / Linux）

```bash
./scripts/install_cron.sh        # 默认每天 09:00
./scripts/install_cron.sh 20 30  # 或指定 20:30
```

日志写在 `logs/watch-YYYY-MM.log`。

### 方式三：常驻进程

```bash
PYTHONPATH=src python3 -m applepay_watch loop --interval 86400
```

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `run` | 执行一次监测，有新岗位就发邮件（定时任务调用的就是它）|
| `run --dry-run` | 不发邮件，只把邮件写到 `out/` |
| `run --force` | 即使没有新增岗位也发一封（测试用）|
| `run --require-mail` | 发信没配好就直接报错退出，而不是降级成本地预览（定时任务用）|
| `list` | 列出官网当前命中的岗位，加 `--json` 输出结构化数据 |
| `preview` | 用真实岗位渲染一封邮件预览，加 `--send` 真的发出去 |
| `mailtest` | 验证发件邮箱能否登录，加 `--send` 发一封测试邮件 |
| `status` | 查看本地快照、收件人、SMTP 和翻译配置 |
| `seed` | 把当前岗位记为基线，不发邮件 |
| `loop` | 常驻进程，按间隔重复检查 |

## 换一个监测目标

不想只盯 Apple Pay，或者想看别的地区？去 [jobs.apple.com](https://jobs.apple.com/zh-cn/search) 上把筛选条件勾好，把浏览器地址栏的 URL 整个粘到 `.env` 里：

```env
APPLE_JOBS_SEARCH_URL=https://jobs.apple.com/zh-cn/search?location=china-CHNC&team=apps-and-frameworks-SFTWR-AF
```

翻页、地区、产品线这些都会跟着这个 URL 走，代码不用改。

## 常见问题

### 创建应用专用密码时提示「您的账号不支持您正在尝试的设置」

这是 Google 账号侧的限制。**最常见的原因是两步验证没开**——应用专用密码只对已开启两步验证的账号开放，没开的话那个页面就只显示这一句话。

先去[开启两步验证](https://myaccount.google.com/signinoptions/twosv)，确认状态是「已开启」，再回到[应用专用密码](https://myaccount.google.com/apppasswords)页面（也可以在两步验证页面拉到最底部找到入口）。

如果两步验证确实开好了还是这个提示，那就是下面三种情况之一，这些账号**拿不到应用专用密码**，需要换一个发件邮箱：

- 两步验证只绑定了实体安全密钥（没有手机号或验证器 App）
- 账号加入了 [Google 高级保护计划](https://support.google.com/accounts/answer/7539956?hl=zh-Hans)，该计划明确禁用应用专用密码并会撤销已有的
- 这是公司 / 学校的 Google Workspace 账号，管理员禁用了应用专用密码

**换发件邮箱不影响收件。** 发件用哪个邮箱都行，`MAIL_TO` 保持你的 Gmail 就好，邮件照样收到同一个收件箱。QQ 邮箱最省事：邮箱设置 → 账户 → 开启 SMTP 服务 → 拿到授权码，然后按上面 [QQ / 163 邮箱](#qq--163-邮箱)那段配置即可。

### 收不到邮件，但程序显示发送成功

先去垃圾邮件里找一下。用 QQ / 163 往 Gmail 发信时，首封容易被归到「垃圾邮件」或「推广」标签，标记一次「非垃圾邮件」之后就正常了。

### 日志里出现「翻译后端 google 失败」

免费的 Google 网页翻译接口对同一个 IP 有频率限制，尤其是在云服务器 / GitHub Actions 上跑的时候。程序会自动降级到下一个后端，最差情况是保留英文原文并在邮件里说明，不会中断监测。想彻底稳定就配一个 `LLM_API_KEY` 或 `DEEPL_API_KEY`。

### 程序报 `ParseError`

说明 Apple 改版了页面结构。这是刻意设计的：解析不出来就报错退出，而不是静默发一封空邮件。`tests/fixtures/` 里存着官网页面快照，跑 `python3 -m pytest` 能定位是哪一层解析失效了。

## 开发

```bash
python3 -m pytest
```

测试用的是从 Apple 官网抓下来的真实页面快照（`tests/fixtures/`），所以官网一旦改版、解析逻辑失效，测试会立刻失败。

```
src/applepay_watch/
├── apple.py         抓取与解析 jobs.apple.com
├── store.py         岗位快照，判断哪些是新增
├── translate.py     多后端翻译 + 磁盘缓存 + 自动降级
├── email_render.py  渲染 HTML / 纯文本邮件
├── mailer.py        SMTP 发送，未配置时落盘预览
├── watcher.py       串起整条流程
└── cli.py           命令行入口
```

## 说明

- 岗位信息与要求均来自 Apple 官网原文，中文为机器翻译，**以官网英文原文为准**。
- 程序只做只读访问，请求频率就是每天几次，对官网没有压力。
- Apple 如果改版导致解析失败，程序会报错退出（`ParseError`），不会静默发出错误邮件。
